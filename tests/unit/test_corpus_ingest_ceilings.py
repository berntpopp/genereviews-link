"""Adversarial tests (F-05): corpus ingest must bound every download and every
in-memory member so a malicious/compromised NCBI archive cannot exhaust disk,
RAM, or the process. Downloads are mocked -- no real ~600 MB corpus is fetched.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import tarfile
from collections.abc import AsyncIterator
from pathlib import Path
from typing import IO, cast
from unittest.mock import AsyncMock

import httpx
import pytest
import respx

from genereview_link import download_guard
from genereview_link.corpus import archive, parallel, pipeline
from genereview_link.corpus.archive import ArchiveListing
from genereview_link.download_guard import DownloadDeadlineError, ResponseTooLargeError


def test_stream_timeout_is_per_read_not_total() -> None:
    # Legit ~600 MB downloads must be allowed to complete: only per-read stalls
    # abort. A total-transfer deadline (or timeout=None) is a bug either way.
    t = download_guard.STREAM_TIMEOUT
    assert t.read == 60.0
    assert t.connect == 30.0
    assert t.write == 30.0
    assert t.pool == 30.0


def _listing() -> ArchiveListing:
    return ArchiveListing(
        relpath="jvig/gene_NBK1116/gene_NBK1116.tar.gz",
        title="GeneReviews",
        publisher="NCBI",
        initial_year="1993",
        nbk_id="NBK1116",
        last_updated="2026-05-10",
    )


@respx.mock(assert_all_called=False)
async def test_download_tarball_happy_path_streams_and_hashes(
    respx_mock: respx.Router, tmp_path: Path
) -> None:
    listing = _listing()
    url = f"{archive.LITARCH_BASE}/{listing.relpath}"
    respx_mock.get(url).mock(return_value=httpx.Response(200, content=b"hello-corpus"))

    dest = tmp_path / "corpus.tar.gz"
    sha = await archive.download_tarball(listing, dest=dest)

    assert dest.read_bytes() == b"hello-corpus"
    assert sha == hashlib.sha256(b"hello-corpus").hexdigest()


async def test_fetch_listing_passes_its_explicit_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    read = AsyncMock(return_value=b"path,title,publisher,1993,NBK1116,2026-05-10\n")
    monkeypatch.setattr(archive, "read_capped", read)

    await archive.fetch_listing()

    call = read.await_args
    assert call is not None
    assert call.kwargs["deadline_seconds"] == archive.LISTING_DOWNLOAD_DEADLINE_SECONDS


async def test_download_tarball_passes_its_explicit_deadline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stream = AsyncMock(return_value="digest")
    monkeypatch.setattr(archive, "stream_to_file", stream)

    assert await archive.download_tarball(_listing(), dest=tmp_path / "corpus.tar.gz") == "digest"

    call = stream.await_args
    assert call is not None
    assert call.kwargs["deadline_seconds"] == archive.TARBALL_DOWNLOAD_DEADLINE_SECONDS


@respx.mock(assert_all_called=False)
async def test_download_tarball_enforces_byte_ceiling(
    respx_mock: respx.Router, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(archive, "MAX_TARBALL_BYTES", 8)
    listing = _listing()
    url = f"{archive.LITARCH_BASE}/{listing.relpath}"
    respx_mock.get(url).mock(return_value=httpx.Response(200, content=b"x" * 4096))

    dest = tmp_path / "corpus.tar.gz"
    with pytest.raises(ResponseTooLargeError):
        await archive.download_tarball(listing, dest=dest)
    assert not dest.exists()


class _SlowResponse:
    def __init__(self) -> None:
        self.headers: dict[str, str] = {}

    def raise_for_status(self) -> None:
        return None

    async def aiter_bytes(self, chunk_size: int) -> AsyncIterator[bytes]:
        del chunk_size
        yield b"a"


class _SlowStream:
    async def __aenter__(self) -> _SlowResponse:
        return _SlowResponse()

    async def __aexit__(self, *_: object) -> None:
        return None


class _SlowClient:
    def stream(self, method: str, url: str) -> _SlowStream:
        assert method == "GET"
        assert url == "https://example.test/slow"
        return _SlowStream()


async def test_stream_to_file_rejects_elapsed_deadline_before_a_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ticks = iter((10.0, 11.0, 12.0))
    monkeypatch.setattr(download_guard, "monotonic", lambda: next(ticks))

    with pytest.raises(DownloadDeadlineError):
        await download_guard.stream_to_file(
            _SlowClient(),  # type: ignore[arg-type]
            "https://example.test/slow",
            tmp_path / "slow.tar.gz",
            max_bytes=10,
            deadline_seconds=0.5,
        )


class _DripResponse(_SlowResponse):
    async def aiter_bytes(self, chunk_size: int) -> AsyncIterator[bytes]:
        del chunk_size
        await asyncio.sleep(0.02)
        yield b"a"


class _DripStream(_SlowStream):
    async def __aenter__(self) -> _DripResponse:
        return _DripResponse()


class _DripClient(_SlowClient):
    def stream(self, method: str, url: str) -> _DripStream:
        assert method == "GET"
        assert url == "https://example.test/drip"
        return _DripStream()


async def test_stream_to_file_rejects_slow_drip_before_per_read_timeout(tmp_path: Path) -> None:
    with pytest.raises(DownloadDeadlineError):
        await download_guard.stream_to_file(
            _DripClient(),  # type: ignore[arg-type]
            "https://example.test/drip",
            tmp_path / "drip.tar.gz",
            max_bytes=10,
            deadline_seconds=0.001,
        )


@respx.mock(assert_all_called=False)
async def test_fetch_listing_enforces_byte_ceiling(
    respx_mock: respx.Router, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(archive, "MAX_LISTING_BYTES", 8)
    respx_mock.get(archive.FILE_LIST_URL).mock(
        return_value=httpx.Response(200, content=b"a," * 4096)
    )
    with pytest.raises(ResponseTooLargeError):
        await archive.fetch_listing()


def _make_targz(members: list[tuple[str, bytes]]) -> Path:
    import tempfile

    fd = Path(tempfile.mkdtemp()) / "arc.tar.gz"
    with tarfile.open(fd, "w:gz") as tf:
        for name, data in members:
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return fd


def test_iter_tarball_reads_normal_members() -> None:
    arc = _make_targz([("a.nxml", b"<x/>"), ("readme.txt", b"skip"), ("b.nxml", b"<y/>")])
    got = [(r.relpath, r.raw) for r in parallel._iter_tarball(arc)]
    assert got == [("a.nxml", b"<x/>"), ("b.nxml", b"<y/>")]


def test_iter_tarball_rejects_oversized_member(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(parallel, "MAX_MEMBER_BYTES", 16)
    arc = _make_targz([("big.nxml", b"z" * 4096)])
    with pytest.raises(ResponseTooLargeError):
        list(parallel._iter_tarball(arc))


def test_iter_tarball_rejects_highly_compressible_decompression_bomb(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(parallel, "MAX_MEMBER_BYTES", 1024)
    arc = _make_targz([("bomb.nxml", b"x" * (1024 * 1024))])

    assert arc.stat().st_size < 4096
    with pytest.raises(ResponseTooLargeError, match="exceeds"):
        list(parallel._iter_tarball(arc))


def test_iter_tarball_accounts_for_ignored_regular_members(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(parallel, "MAX_MEMBER_BYTES", 16)
    monkeypatch.setattr(parallel, "MAX_TOTAL_EXPANDED_BYTES", 15)
    arc = _make_targz([("ignored.bin", b"x" * 12), ("chapter.nxml", b"<x/>")])

    with pytest.raises(ResponseTooLargeError, match="declared"):
        list(parallel._iter_tarball(arc))


def test_iter_tarball_rejects_regular_member_actual_size_mismatch() -> None:
    class _ShortRead:
        def read(self, size: int) -> bytes:
            assert size == parallel.MAX_MEMBER_BYTES + 1
            return b"short"

    with pytest.raises(ResponseTooLargeError, match="size mismatch"):
        parallel._read_regular_member(
            cast(IO[bytes], _ShortRead()), declared_size=8, name="truncated.bin"
        )


def test_iter_tarball_enforces_member_count_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(parallel, "MAX_TAR_MEMBERS", 3)
    arc = _make_targz([(f"c{i}.nxml", b"<x/>") for i in range(10)])
    with pytest.raises(ResponseTooLargeError):
        list(parallel._iter_tarball(arc))


@respx.mock(assert_all_called=False)
async def test_download_sidedata_enforces_cap(
    respx_mock: respx.Router, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(pipeline, "MAX_SIDEDATA_BYTES", 8)
    base = "https://ftp.ncbi.nlm.nih.gov/pub/GeneReviews"
    for name in (
        "GRtitle_shortname_NBKid.txt",
        "NBKid_shortname_genesymbol.txt",
        "NBKid_shortname_OMIM.txt",
    ):
        respx_mock.get(f"{base}/{name}").mock(return_value=httpx.Response(200, content=b"q" * 4096))
    with pytest.raises(ResponseTooLargeError):
        await pipeline._download_sidedata(tmp_path)


async def test_download_sidedata_passes_its_explicit_deadline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    read = AsyncMock(return_value=b"content")
    monkeypatch.setattr(pipeline, "read_capped", read)

    await pipeline._download_sidedata(tmp_path)

    assert read.await_count == 3
    assert all(
        call.kwargs["deadline_seconds"] == pipeline.SIDEDATA_DOWNLOAD_DEADLINE_SECONDS
        for call in read.await_args_list
    )
