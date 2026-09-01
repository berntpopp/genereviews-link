"""Validation for retained, offline GeneReviews source captures."""

from __future__ import annotations

import hashlib
import json
import re
import stat
import tarfile
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path

from genereview_link.corpus.archive import FILE_LIST_URL, LITARCH_BASE, MAX_LISTING_BYTES
from genereview_link.corpus.source_identity import SIDEDATA_FILES

SHA256 = re.compile(r"^[0-9a-f]{64}$")


class SourceCaptureError(ValueError):
    """A retained source capture is incomplete or does not match its files."""


def _canonical(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def archive_content_identities(path: Path) -> tuple[str, str]:
    """Hash an archive's exact sorted member inventory and expanded regular bytes."""
    members: list[dict[str, object]] = []
    expanded = hashlib.sha256()
    total = 0
    try:
        with tarfile.open(path, "r:gz") as archive:
            entries = sorted(archive.getmembers(), key=lambda entry: entry.name)
            if not entries or len(entries) > 10_000:
                raise SourceCaptureError("archive member count is outside the reviewed bound")
            seen: set[str] = set()
            regular_members = 0
            for entry in entries:
                if (
                    entry.name in seen
                    or not entry.name
                    or len(entry.name.encode("utf-8")) > 512
                    or entry.name.startswith("/")
                    or "\\" in entry.name
                    or "//" in entry.name
                    or entry.name in {".", ".."}
                    or ".." in Path(entry.name).parts
                ):
                    raise SourceCaptureError("archive contains an unsafe or duplicate member")
                seen.add(entry.name)
                if entry.isdir():
                    members.append({"name": entry.name, "type": "directory"})
                    continue
                if not entry.isfile():
                    raise SourceCaptureError("archive contains an unsafe or duplicate member")
                regular_members += 1
                total += entry.size
                if entry.size > 64 * 1024 * 1024 or total > 4 * 1024 * 1024 * 1024:
                    raise SourceCaptureError("archive expanded data exceeds the reviewed bound")
                stream = archive.extractfile(entry)
                if stream is None:
                    raise SourceCaptureError("archive regular member cannot be read")
                digest = hashlib.sha256()
                expanded.update(entry.name.encode())
                expanded.update(b"\0")
                size = 0
                while chunk := stream.read(1 << 20):
                    digest.update(chunk)
                    expanded.update(chunk)
                    size += len(chunk)
                if size != entry.size:
                    raise SourceCaptureError("archive regular member is truncated")
                expanded.update(b"\0")
                members.append(
                    {
                        "name": entry.name,
                        "type": "file",
                        "size_bytes": size,
                        "sha256": digest.hexdigest(),
                    }
                )
            if regular_members == 0:
                raise SourceCaptureError("archive contains no regular source members")
    except (OSError, tarfile.TarError) as error:
        raise SourceCaptureError("archive is not a readable gzip tar capture") from error
    return hashlib.sha256(_canonical(members)).hexdigest(), expanded.hexdigest()


def _file_identity(path: Path, expected: object, *, label: str) -> None:
    if not isinstance(expected, Mapping):
        raise SourceCaptureError(f"{label} identity is missing")
    try:
        info = path.lstat()
    except OSError as error:
        raise SourceCaptureError(f"{label} file is missing") from error
    if not stat.S_ISREG(info.st_mode):
        raise SourceCaptureError(f"{label} must be a retained regular file")
    size = info.st_size
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1 << 20):
            digest.update(chunk)
    if expected.get("size_bytes") != size:
        raise SourceCaptureError(f"{label} size does not match capture")
    if expected.get("sha256") != digest.hexdigest():
        raise SourceCaptureError(f"{label} digest does not match capture")


def load_offline_capture(
    metadata: Path, *, archive: Path, side_data_dir: Path
) -> dict[str, object]:
    """Load one exact capture without fetching or deleting any source file."""
    try:
        value = json.loads(metadata.read_bytes())
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as error:
        raise SourceCaptureError("source capture metadata is not valid JSON") from error
    if not isinstance(value, dict) or value.get("format") != "genereviews-offline-source-v1":
        raise SourceCaptureError("source capture format is invalid")
    listing = value.get("listing")
    archive_identity = value.get("archive")
    side_data = value.get("side_data")
    chapter_ids = value.get("chapter_ids")
    prior = value.get("prior_artifact")
    if not isinstance(listing, Mapping) or set(listing) != {
        "url",
        "raw_sha256",
        "raw_size_bytes",
        "captured_at",
        "integrity_class",
        "relpath",
        "last_updated",
    }:
        raise SourceCaptureError("listing capture metadata is incomplete")
    captured_at = listing.get("captured_at")
    try:
        captured_time = datetime.fromisoformat(str(captured_at).replace("Z", "+00:00"))
    except ValueError as error:
        raise SourceCaptureError("listing capture time is invalid") from error
    if listing.get("url") != FILE_LIST_URL:
        raise SourceCaptureError("listing URL is not the canonical captured upstream URL")
    if (
        listing.get("integrity_class") != "https-captured-untrusted"
        or not SHA256.fullmatch(str(listing.get("raw_sha256", "")))
        or type(listing.get("raw_size_bytes")) is not int
        or not 0 < int(listing["raw_size_bytes"]) <= MAX_LISTING_BYTES
        or not isinstance(captured_at, str)
        or not captured_at.endswith("Z")
        or captured_time.utcoffset() is None
    ):
        raise SourceCaptureError("listing capture integrity is invalid")
    if not isinstance(archive_identity, Mapping) or set(archive_identity) != {
        "url",
        "sha256",
        "size_bytes",
        "members_sha256",
        "expanded_sha256",
    }:
        raise SourceCaptureError("archive capture metadata is incomplete")
    for key in ("members_sha256", "expanded_sha256"):
        if not SHA256.fullmatch(str(archive_identity.get(key, ""))):
            raise SourceCaptureError(f"archive {key} is invalid")
    expected_archive_url = f"{LITARCH_BASE}/{listing.get('relpath')}"
    if archive_identity.get("url") != expected_archive_url:
        raise SourceCaptureError("archive URL is not the canonical captured upstream URL")
    if not isinstance(side_data, Mapping) or set(side_data) != set(SIDEDATA_FILES):
        raise SourceCaptureError("side-data capture set is incomplete")
    if (
        not isinstance(chapter_ids, list)
        or chapter_ids != sorted(set(chapter_ids))
        or not all(
            isinstance(value, str) and re.fullmatch(r"NBK\d+", value) for value in chapter_ids
        )
    ):
        raise SourceCaptureError("capture chapter IDs must be exact, unique, and sorted")
    if not isinstance(prior, Mapping) or set(prior) != {
        "object_id",
        "chapter_ids",
        "chapter_count",
        "chapter_digests",
        "chapters_sha256",
        "passages_sha256",
    }:
        raise SourceCaptureError("prior artifact identity is incomplete")
    prior_ids = prior.get("chapter_ids")
    prior_digests = prior.get("chapter_digests")
    if (
        not SHA256.fullmatch(str(prior.get("object_id", "")))
        or not isinstance(prior_ids, list)
        or prior_ids != sorted(set(prior_ids))
        or not all(isinstance(item, str) and re.fullmatch(r"NBK\d+", item) for item in prior_ids)
        or prior.get("chapter_count") != len(prior_ids)
        or not isinstance(prior_digests, Mapping)
        or set(prior_digests) != set(prior_ids)
        or not all(SHA256.fullmatch(str(digest)) for digest in prior_digests.values())
        or not SHA256.fullmatch(str(prior.get("chapters_sha256", "")))
        or not SHA256.fullmatch(str(prior.get("passages_sha256", "")))
    ):
        raise SourceCaptureError("prior artifact logical identity is invalid")
    _file_identity(archive, archive_identity, label="archive")
    members_sha256, expanded_sha256 = archive_content_identities(archive)
    if archive_identity.get("members_sha256") != members_sha256:
        raise SourceCaptureError("archive members_sha256 does not match retained members")
    if archive_identity.get("expanded_sha256") != expanded_sha256:
        raise SourceCaptureError("archive expanded_sha256 does not match retained contents")
    for name in SIDEDATA_FILES:
        entry = side_data[name]
        expected_url = f"https://ftp.ncbi.nlm.nih.gov/pub/GeneReviews/{name}"
        if (
            not isinstance(entry, Mapping)
            or set(entry) != {"url", "sha256", "size_bytes"}
            or entry.get("url") != expected_url
        ):
            raise SourceCaptureError(f"side-data {name} URL is missing")
        _file_identity(side_data_dir / name, entry, label=f"side-data {name}")
    from genereview_link.corpus.sidedata import load_sidedata

    mapped_chapter_ids = sorted(load_sidedata(side_data_dir).short_name_by_nbk)
    if mapped_chapter_ids != chapter_ids:
        raise SourceCaptureError("capture chapter IDs do not match the authoritative side mapping")
    return value


__all__ = ["SourceCaptureError", "archive_content_identities", "load_offline_capture"]
