"""Adversarial tests (F-05): corpus ingest must bound every download and every
in-memory member so a malicious/compromised NCBI archive cannot exhaust disk,
RAM, or the process. Downloads are mocked -- no real ~600 MB corpus is fetched.
"""

from __future__ import annotations

import hashlib
import io
import tarfile
from pathlib import Path

import httpx
import pytest
import respx

from genereview_link import download_guard
from genereview_link.corpus import archive, parallel, pipeline
from genereview_link.corpus.archive import ArchiveListing
from genereview_link.download_guard import ResponseTooLargeError


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
