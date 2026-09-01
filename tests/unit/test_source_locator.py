"""The no-input build locator binds every retained offline source asset."""

from __future__ import annotations

import json

import pytest

from genereview_link.corpus.source_locator import (
    SOURCE_ASSETS,
    SourceLocatorError,
    load_source_locator,
)


def _locator() -> bytes:
    return json.dumps(
        {
            "format": "genereviews-source-locator-v1",
            "assets": [
                {
                    "name": name,
                    "url": f"https://api.github.com/repos/owner/sources/releases/assets/{index}",
                    "sha256": f"{index:064x}",
                    "size_bytes": 1024,
                }
                for index, name in enumerate(sorted(SOURCE_ASSETS), 1)
            ],
        },
        separators=(",", ":"),
    ).encode()


def test_source_locator_is_complete_small_and_allowlisted() -> None:
    raw = _locator()
    assert len(raw) < 48 * 1024
    assert load_source_locator(raw, allowed_repositories={"owner/sources"})["assets"]


def test_source_locator_rejects_mutable_or_unallowlisted_url() -> None:
    raw = _locator().replace(b"/releases/assets/", b"/contents/main/")
    with pytest.raises(SourceLocatorError, match="allowlisted"):
        load_source_locator(raw, allowed_repositories={"owner/sources"})


def test_source_locator_rejects_url_credentials() -> None:
    raw = _locator().replace(b"https://api.github.com", b"https://attacker@api.github.com")
    with pytest.raises(SourceLocatorError, match="allowlisted"):
        load_source_locator(raw, allowed_repositories={"owner/sources"})
