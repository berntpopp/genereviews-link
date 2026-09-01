"""Validation for retained, offline GeneReviews source captures."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import tarfile
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path

from genereview_link.corpus.archive import (
    FILE_LIST_URL,
    LITARCH_BASE,
    MAX_LISTING_BYTES,
    parse_file_list_row,
)
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
    parent_fd: int | None = None
    descriptor: int | None = None
    try:
        parent_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        descriptor = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent_fd)
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise SourceCaptureError("archive must be a retained regular file")
        with (
            os.fdopen(descriptor, "rb", closefd=False) as source,
            tarfile.open(fileobj=source, mode="r:gz") as archive,
        ):
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
        after = os.fstat(descriptor)
        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        ):
            raise SourceCaptureError("archive changed while its identity was computed")
    except (OSError, tarfile.TarError) as error:
        raise SourceCaptureError("archive is not a readable gzip tar capture") from error
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if parent_fd is not None:
            os.close(parent_fd)
    return hashlib.sha256(_canonical(members)).hexdigest(), expanded.hexdigest()


def _file_identity(path: Path, expected: object, *, label: str) -> None:
    if not isinstance(expected, Mapping):
        raise SourceCaptureError(f"{label} identity is missing")
    parent_fd: int | None = None
    descriptor: int | None = None
    try:
        parent_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        descriptor = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent_fd)
        before = os.fstat(descriptor)
    except OSError as error:
        if descriptor is not None:
            os.close(descriptor)
        if parent_fd is not None:
            os.close(parent_fd)
        raise SourceCaptureError(f"{label} file is missing") from error
    try:
        if not stat.S_ISREG(before.st_mode):
            raise SourceCaptureError(f"{label} must be a retained regular file")
        size = before.st_size
        digest = hashlib.sha256()
        while chunk := os.read(descriptor, 1 << 20):
            digest.update(chunk)
        after = os.fstat(descriptor)
        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        ):
            raise SourceCaptureError(f"{label} changed while reading")
    finally:
        os.close(descriptor)
        os.close(parent_fd)
    if expected.get("size_bytes") != size:
        raise SourceCaptureError(f"{label} size does not match capture")
    if expected.get("sha256") != digest.hexdigest():
        raise SourceCaptureError(f"{label} digest does not match capture")


def _read_regular_bounded(path: Path, *, label: str, limit: int) -> bytes:
    parent_fd: int | None = None
    try:
        parent_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        descriptor = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent_fd)
    except OSError as error:
        if parent_fd is not None:
            os.close(parent_fd)
        raise SourceCaptureError(f"{label} is missing or unsafe") from error
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_size > limit:
            raise SourceCaptureError(f"{label} is not a bounded regular file")
        chunks: list[bytes] = []
        remaining = limit + 1
        while remaining:
            chunk = os.read(descriptor, min(64 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        after = os.fstat(descriptor)
        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        ):
            raise SourceCaptureError(f"{label} changed while reading")
    finally:
        os.close(descriptor)
        os.close(parent_fd)
    value = b"".join(chunks)
    if len(value) > limit:
        raise SourceCaptureError(f"{label} exceeds its reviewed bound")
    return value


def load_offline_capture(
    metadata: Path,
    *,
    archive: Path,
    side_data_dir: Path,
    prior_manifest: Path,
    prior_seal_manifest: Path,
) -> dict[str, object]:
    """Load one exact capture without fetching or deleting any source file."""
    try:
        value = json.loads(_read_regular_bounded(metadata, label="source capture", limit=4 << 20))
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
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
    listing_bytes = _read_regular_bounded(
        metadata.with_name("file_list.csv"), label="file_list.csv", limit=MAX_LISTING_BYTES
    )
    if (
        len(listing_bytes) != listing["raw_size_bytes"]
        or hashlib.sha256(listing_bytes).hexdigest() != listing["raw_sha256"]
    ):
        raise SourceCaptureError("file_list.csv bytes do not match listing capture")
    try:
        matching_rows = [
            parsed
            for line in listing_bytes.decode("utf-8").splitlines()
            if (parsed := parse_file_list_row(line, nbk_filter="NBK1116")) is not None
        ]
    except UnicodeDecodeError as error:
        raise SourceCaptureError("file_list.csv is not canonical UTF-8") from error
    if len(matching_rows) != 1:
        raise SourceCaptureError("file_list.csv must contain exactly one canonical NBK1116 row")
    derived_listing = matching_rows[0]
    if (
        listing.get("relpath") != derived_listing.relpath
        or listing.get("last_updated") != derived_listing.last_updated
    ):
        raise SourceCaptureError("listing fields are not derived from retained file_list.csv")
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
        "manifest_sha256",
        "corpus_release_id",
        "app_git_sha",
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
        or not SHA256.fullmatch(str(prior.get("manifest_sha256", "")))
        or not re.fullmatch(
            r"20\d{2}-\d{2}-\d{2}-r[1-9]\d*", str(prior.get("corpus_release_id", ""))
        )
        or not re.fullmatch(r"[0-9a-f]{40}", str(prior.get("app_git_sha", "")))
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
    prior_bytes = _read_regular_bounded(
        prior_manifest, label="prior manifest", limit=4 * 1024 * 1024
    )
    if hashlib.sha256(prior_bytes).hexdigest() != prior["manifest_sha256"]:
        raise SourceCaptureError("prior manifest digest does not match retained bytes")
    prior_seal_bytes = _read_regular_bounded(
        prior_seal_manifest, label="prior seal manifest", limit=4 * 1024 * 1024
    )
    if hashlib.sha256(prior_seal_bytes).hexdigest() != prior["object_id"]:
        raise SourceCaptureError("prior object ID does not match retained seal manifest")
    try:
        prior_record = json.loads(prior_bytes)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise SourceCaptureError("prior manifest is not valid JSON") from error
    try:
        prior_seal = json.loads(prior_seal_bytes)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise SourceCaptureError("prior seal manifest is not valid JSON") from error
    seal_files = prior_seal.get("files") if isinstance(prior_seal, dict) else None
    manifest_entries = (
        [
            entry
            for entry in seal_files
            if isinstance(entry, dict) and entry.get("name") == "manifest.json"
        ]
        if isinstance(seal_files, list)
        else []
    )
    if (
        not isinstance(prior_seal, dict)
        or prior_seal.get("format") != "genereviews-local-handoff-v1"
        or prior_seal.get("corpus_release_id") != prior["corpus_release_id"]
        or len(manifest_entries) != 1
        or manifest_entries[0].get("sha256") != prior["manifest_sha256"]
        or manifest_entries[0].get("size") != len(prior_bytes)
    ):
        raise SourceCaptureError("prior seal manifest does not bind the retained prior manifest")
    prior_content = prior_record.get("content_identity") if isinstance(prior_record, dict) else None
    logical_keys = {
        "chapter_ids",
        "chapter_count",
        "chapter_digests",
        "chapters_sha256",
        "passages_sha256",
    }
    if (
        not isinstance(prior_record, dict)
        or prior_record.get("manifest_version") != "3"
        or prior_record.get("corpus_release_id") != prior["corpus_release_id"]
        or prior_record.get("app_git_sha") != prior["app_git_sha"]
        or not isinstance(prior_content, Mapping)
        or {key: prior_content.get(key) for key in logical_keys}
        != {key: prior.get(key) for key in logical_keys}
    ):
        raise SourceCaptureError("prior manifest does not prove the claimed logical identity")
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
