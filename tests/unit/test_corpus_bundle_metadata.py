"""Tests for corpus bundle release metadata helpers."""

from __future__ import annotations

import pytest

from genereview_link.corpus.bundle_metadata import (
    asset_name_for_release,
    validate_release_id,
)


@pytest.mark.parametrize("release_id", ["2026-05-12-r1", "2026-12-31-r12"])
def test_validate_release_id_accepts_corpus_release_ids(release_id: str) -> None:
    assert validate_release_id(release_id) == release_id


@pytest.mark.parametrize(
    "release_id",
    ["corpus-2026-05-12-r1", "20260512-r1", "2026-05-12", "latest"],
)
def test_validate_release_id_rejects_invalid_values(release_id: str) -> None:
    with pytest.raises(ValueError, match="YYYY-MM-DD-rN"):
        validate_release_id(release_id)


def test_asset_name_for_release_includes_model_and_database_versions() -> None:
    assert (
        asset_name_for_release(
            "2026-05-12-r1",
            model_slug="bge-small-en-v1.5",
            postgres_major="pg18",
            pgvector_version="pgv0.8.2",
        )
        == "genereview-corpus-2026-05-12-r1-bge-small-en-v1.5-pg18-pgv0.8.2.tar.gz"
    )
