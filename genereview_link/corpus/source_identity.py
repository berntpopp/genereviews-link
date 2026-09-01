"""Canonical, fail-closed identity for one upstream GeneReviews snapshot."""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import date
from pathlib import PurePosixPath

SIDEDATA_FILES = (
    "GRtitle_shortname_NBKid.txt",
    "NBKid_shortname_genesymbol.txt",
    "NBKid_shortname_OMIM.txt",
)
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
RELEASE_ID_RE = re.compile(r"^\d{4}-\d{2}-\d{2}-r[1-9]\d*$")


def validate_release_id(release_id: str) -> str:
    """Validate the stable release-id component without loading runtime dependencies."""
    if not RELEASE_ID_RE.fullmatch(release_id):
        raise ValueError("release_id must use YYYY-MM-DD-rN, for example 2026-05-12-r1")
    return release_id


def _digest_entry(value: object, *, label: str) -> dict[str, str | int]:
    if not isinstance(value, Mapping) or set(value) != {"sha256", "size_bytes"}:
        raise ValueError(f"{label} must contain exactly sha256 and size_bytes")
    digest = value.get("sha256")
    size = value.get("size_bytes")
    if not isinstance(digest, str) or not SHA256_RE.fullmatch(digest):
        raise ValueError(f"{label} SHA-256 is invalid")
    if type(size) is not int or size <= 0:
        raise ValueError(f"{label} size is invalid")
    return {"sha256": digest, "size_bytes": size}


def validate_source_identity(
    value: object,
    *,
    tarball_sha256: str | None = None,
    last_updated: str | None = None,
) -> dict[str, object]:
    """Return a canonical upstream identity or reject any incomplete shape."""
    if not isinstance(value, Mapping) or set(value) != {
        "listing_relpath",
        "last_updated",
        "tarball",
        "side_data",
    }:
        raise ValueError("upstream source identity has missing or extra fields")

    relpath = value.get("listing_relpath")
    if not isinstance(relpath, str) or relpath != relpath.strip() or not relpath:
        raise ValueError("listing_relpath is invalid")
    path = PurePosixPath(relpath)
    if (
        path.is_absolute()
        or "\\" in relpath
        or "//" in relpath
        or any(part in {"", ".", ".."} for part in path.parts)
        or path.name != "gene_NBK1116.tar.gz"
    ):
        raise ValueError("listing_relpath must be a safe GeneReviews archive path")

    updated = value.get("last_updated")
    if not isinstance(updated, str) or updated != updated.strip() or not updated:
        raise ValueError("source last_updated is invalid")
    try:
        date.fromisoformat(updated[:10])
    except ValueError as error:
        raise ValueError("source last_updated must begin with an ISO date") from error
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}(?:[ T]\d{2}:\d{2}:\d{2})?", updated):
        raise ValueError("source last_updated must use the normalized upstream format")
    tarball = _digest_entry(value.get("tarball"), label="tarball")
    if tarball_sha256 is not None and tarball["sha256"] != tarball_sha256:
        raise ValueError("source tarball SHA-256 does not match the manifest")
    if last_updated is not None and updated != last_updated:
        raise ValueError("source last_updated does not match the manifest")

    raw_side_data = value.get("side_data")
    if not isinstance(raw_side_data, Mapping) or set(raw_side_data) != set(SIDEDATA_FILES):
        raise ValueError("source side_data must bind exactly the three upstream files")
    side_data = {
        name: _digest_entry(raw_side_data[name], label=f"side_data {name}")
        for name in SIDEDATA_FILES
    }
    return {
        "listing_relpath": relpath,
        "last_updated": updated,
        "tarball": tarball,
        "side_data": side_data,
    }
