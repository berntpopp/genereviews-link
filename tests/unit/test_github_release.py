"""Tests for GitHub release bundle helpers."""

from __future__ import annotations

from genereview_link.ingest.github_release import _select_bundle_asset


def test_select_bundle_asset_picks_genereview_corpus_tarball() -> None:
    assets = [
        {"name": "notes.txt", "browser_download_url": "https://example/notes.txt"},
        {
            "name": ("genereview-corpus-2026-05-12-r1-bge-small-en-v1.5-pg18-pgv0.8.2.tar.gz"),
            "browser_download_url": "https://example/bundle.tar.gz",
        },
    ]

    assert _select_bundle_asset(assets) == "https://example/bundle.tar.gz"


def test_select_bundle_asset_ignores_sha256_and_other_tarballs() -> None:
    assets = [
        {
            "name": "genereview-corpus-2026-05-12-r1.tar.gz.sha256",
            "browser_download_url": "https://example/sha",
        },
        {"name": "other.tar.gz", "browser_download_url": "https://example/other.tar.gz"},
    ]

    assert _select_bundle_asset(assets) is None
