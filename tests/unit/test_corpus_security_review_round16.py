"""Regressions for downloader descriptor ownership during cancellation.

The locator and release-asset call sites this round also covered were deleted with
the sealed-handoff publication scheme; the guard itself is still live behind the
upstream archive fetch and the container seed download, so its refusals stay here.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from genereview_link import download_guard
from genereview_link.download_guard import DownloadOwnership


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
    assert not target.exists()
