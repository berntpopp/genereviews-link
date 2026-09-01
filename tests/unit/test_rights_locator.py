"""Transferable rights evidence fits GitHub's protected-secret limit."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from genereview_link.corpus import rights_locator
from genereview_link.corpus.rights_locator import (
    RightsLocatorError,
    fetch_rights_assets,
    load_rights_locator,
)


def _locator() -> bytes:
    return json.dumps(
        {
            "format": "genereviews-rights-locator-v1",
            "assets": [
                {
                    "name": name,
                    "url": f"https://api.github.com/repos/owner/rights/releases/assets/{index}",
                    "sha256": f"{index:064x}",
                    "size_bytes": 1024,
                }
                for index, name in enumerate(
                    ("rights-record.json", "rights-evidence.json", "terms-snapshot.html"), 1
                )
            ],
        },
        separators=(",", ":"),
    ).encode()


def test_rights_locator_is_small_exact_and_allowlisted() -> None:
    raw = _locator()
    assert len(raw) < 48 * 1024
    assert load_rights_locator(raw, allowed_repositories={"owner/rights"})["assets"]


def test_rights_locator_rejects_redirect_host_and_secret_overflow() -> None:
    redirect = _locator().replace(b"api.github.com", b"github.example")
    with pytest.raises(RightsLocatorError, match="allowlisted"):
        load_rights_locator(redirect, allowed_repositories={"owner/rights"})
    with pytest.raises(RightsLocatorError, match="secret size"):
        load_rights_locator(b"x" * (48 * 1024 + 1), allowed_repositories={"owner/rights"})


def test_rights_fetch_removes_partial_file_when_stream_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class BrokenResponse:
        calls = 0

        def __enter__(self) -> BrokenResponse:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def read(self, _size: int) -> bytes:
            self.calls += 1
            if self.calls == 1:
                return b"partial"
            raise OSError("connection lost")

    class BrokenOpener:
        def open(self, *_args: object, **_kwargs: object) -> BrokenResponse:
            return BrokenResponse()

    monkeypatch.setattr(rights_locator, "build_opener", lambda *_args: BrokenOpener())
    destination = tmp_path / "rights"
    destination.mkdir()
    token = "".join(("fixture", "-token"))

    with pytest.raises(RightsLocatorError, match="could not be fetched safely"):
        fetch_rights_assets(
            _locator(),
            allowed_repositories={"owner/rights"},
            destination=destination,
            token=token,
        )

    assert list(destination.iterdir()) == []
