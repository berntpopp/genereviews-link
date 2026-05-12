"""Metadata helpers for corpus bundle releases."""

from __future__ import annotations

import re

RELEASE_ID_RE = re.compile(r"^\d{4}-\d{2}-\d{2}-r[1-9]\d*$")


def validate_release_id(release_id: str) -> str:
    """Validate the release id component used in corpus release tags."""
    if not RELEASE_ID_RE.fullmatch(release_id):
        raise ValueError("release_id must use YYYY-MM-DD-rN, for example 2026-05-12-r1")
    return release_id


def asset_name_for_release(
    release_id: str,
    *,
    model_slug: str,
    postgres_major: str,
    pgvector_version: str,
) -> str:
    """Return the canonical tarball asset name for a corpus release."""
    validated = validate_release_id(release_id)
    return f"genereview-corpus-{validated}-{model_slug}-{postgres_major}-{pgvector_version}.tar.gz"
