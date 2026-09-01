"""Regressions for downloader ownership and CI-only runtime boundaries."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path

import pytest

import genereview_link.corpus.release_assets as release_assets
import genereview_link.corpus.source_locator as source_locator
from genereview_link import download_guard
from genereview_link.download_guard import DownloadOwnership


def _locator(names: frozenset[str]) -> bytes:
    return json.dumps(
        {
            "format": "genereviews-source-locator-v1",
            "assets": [
                {
                    "name": name,
                    "url": f"https://api.github.com/repos/owner/source/releases/assets/{index}",
                    "sha256": hashlib.sha256(b"owned").hexdigest(),
                    "size_bytes": len(b"owned"),
                }
                for index, name in enumerate(sorted(names), 1)
            ],
        },
        separators=(",", ":"),
    ).encode()


class _Client:
    async def __aenter__(self) -> _Client:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None


@pytest.mark.asyncio
async def test_stream_closes_retained_descriptors_when_cancellation_cleanup_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Response:
        def __init__(self) -> None:
            self.headers: dict[str, str] = {}

        def raise_for_status(self) -> None:
            return None

        async def aiter_bytes(self, _size: int):  # type: ignore[no-untyped-def]
            yield b"partial"
            raise asyncio.CancelledError

    class Stream:
        async def __aenter__(self) -> Response:
            return Response()

        async def __aexit__(self, *_args: object) -> None:
            return None

    class Client:
        def stream(self, *_args: object) -> Stream:
            return Stream()

    def failed_cleanup(_ownership: DownloadOwnership) -> bool:
        raise OSError("forced cleanup failure")

    monkeypatch.setattr(DownloadOwnership, "unlink_if_owned", failed_cleanup)
    ownership: list[DownloadOwnership] = []
    target = tmp_path / "partial"

    with pytest.raises(OSError, match="forced cleanup failure"):
        await download_guard.stream_to_file(
            Client(),  # type: ignore[arg-type]
            "https://example.test/partial",
            target,
            max_bytes=16,
            created_ownership=ownership,
        )

    assert len(ownership) == 1 and ownership[0].closed
    target.unlink()


def _substituting_stream(
    target: Path, kwargs: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> tuple[os.stat_result, DownloadOwnership | None]:
    target.write_bytes(b"owned")
    parent_fd = os.open(target.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    file_fd = os.open(target.name, os.O_WRONLY | os.O_NOFOLLOW, dir_fd=parent_fd)
    owned = os.fstat(file_fd)
    retained: DownloadOwnership | None = None
    ownership_output = kwargs.get("created_ownership")
    if isinstance(ownership_output, list):
        retained = DownloadOwnership(parent_fd=parent_fd, file_fd=file_fd, name=target.name)
        ownership_output.append(retained)
    else:
        identity_output = kwargs.get("created_identity")
        if isinstance(identity_output, list):
            identity_output.append((owned.st_dev, owned.st_ino))
        os.close(file_fd)
        os.close(parent_fd)
    target.unlink()
    target.write_bytes(b"foreign")

    real_stat = Path.stat

    def reused_identity(path: Path, *args: object, **stat_kwargs: object) -> os.stat_result:
        if path == target:
            return owned
        return real_stat(path, *args, **stat_kwargs)

    # Deterministically model GitHub's immediate inode reuse for legacy tuple-only
    # pathname cleanup. Descriptor-backed cleanup uses os.stat on its pinned dir FD.
    monkeypatch.setattr(Path, "stat", reused_identity)
    return owned, retained


@pytest.mark.asyncio
async def test_source_cleanup_retains_descriptor_and_preserves_reused_foreign_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    destination = tmp_path / "source"
    destination.mkdir()
    first = destination / sorted(source_locator.SOURCE_ASSETS)[0]
    retained: list[DownloadOwnership] = []

    async def swapped(_client: object, _url: str, target: Path, **kwargs: object) -> str:
        _owned, handle = _substituting_stream(target, kwargs, monkeypatch)
        if handle is not None:
            retained.append(handle)
        return hashlib.sha256(b"owned").hexdigest()

    monkeypatch.setattr(source_locator.httpx, "AsyncClient", lambda **_kwargs: _Client())
    monkeypatch.setattr(source_locator, "stream_to_file", swapped)

    with pytest.raises(source_locator.SourceLocatorError):
        await source_locator.fetch_source_assets(
            _locator(source_locator.SOURCE_ASSETS),
            allowed_repositories={"owner/source"},
            destination=destination,
            token="".join(("fixture", "-token")),
        )

    assert first.read_bytes() == b"foreign"
    assert retained and all(handle.closed for handle in retained)


@pytest.mark.asyncio
async def test_release_cleanup_retains_descriptor_and_preserves_reused_foreign_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    names = release_assets.PUBLICATION_ASSET_NAMES
    identities = tuple(
        release_assets.AssetIdentity(
            asset_id=index,
            name=name,
            size=len(b"owned"),
            digest="sha256:" + hashlib.sha256(b"owned").hexdigest(),
            url=f"https://api.github.com/repos/owner/repo/releases/assets/{index}",
        )
        for index, name in enumerate(names, 1)
    )

    async def identity(*_args: object, **_kwargs: object) -> release_assets.ReleaseIdentity:
        return release_assets.ReleaseIdentity(17, "corpus-data-2026-09-01-r1", "a" * 40, identities)

    retained: list[DownloadOwnership] = []

    async def swapped(_client: object, _url: str, target: Path, **kwargs: object) -> str:
        _owned, handle = _substituting_stream(target, kwargs, monkeypatch)
        if handle is not None:
            retained.append(handle)
        return hashlib.sha256(b"owned").hexdigest()

    monkeypatch.setattr(release_assets, "_release_assets", identity)
    monkeypatch.setattr(release_assets, "stream_to_file", swapped)
    destination = tmp_path / "release"
    destination.mkdir()

    with pytest.raises(release_assets.ReleaseAssetError):
        await release_assets.download_release_assets(
            "owner/repo",
            "corpus-data-2026-09-01-r1",
            destination,
            release_id=17,
        )

    assert (destination / names[0]).read_bytes() == b"foreign"
    assert retained and all(handle.closed for handle in retained)
