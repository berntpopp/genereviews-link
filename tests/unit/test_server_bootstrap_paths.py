"""Tests for bundle bootstrap filesystem paths."""

from __future__ import annotations

from pathlib import Path

from genereview_link.server_manager import _bundle_bootstrap_paths


def test_bundle_bootstrap_paths_live_under_configured_work_dir() -> None:
    bundle, extract_dir = _bundle_bootstrap_paths(Path("/tmp/genereview-link"))  # noqa: S108

    assert bundle == Path("/tmp/genereview-link/bundle.tar.gz")  # noqa: S108
    assert extract_dir == Path("/tmp/genereview-link/bundle_extract")  # noqa: S108
