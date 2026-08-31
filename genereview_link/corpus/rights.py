"""Strict, digest-bound rights evidence for privileged corpus publication."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from urllib.parse import urlparse

MAX_METADATA_BYTES = 1 << 20
RIGHTS_FIELDS = frozenset(
    {
        "artifact_sha256",
        "object_id",
        "decision",
        "responsible_reviewer",
        "rights_authority",
        "decision_time",
        "terms_uri",
        "terms_sha256",
        "terms_version",
        "permitted_asset_use",
        "attribution",
        "evidence_uri",
        "evidence_sha256",
        "source_sha256",
        "corpus_release_id",
        "rights_record_sha256",
    }
)
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class RightsError(ValueError):
    """The rights record is incomplete or unsafe for publication."""


def _canonical(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _read_bounded(path: Path) -> bytes:
    parent_fd: int | None = None
    try:
        parent_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent_fd)
    except OSError as error:
        if parent_fd is not None:
            os.close(parent_fd)
        raise RightsError("rights record must be a regular bounded file") from error
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_METADATA_BYTES:
            raise RightsError("rights record must be a regular bounded file")
        data = bytearray()
        while len(data) <= MAX_METADATA_BYTES:
            chunk = os.read(fd, min(64 * 1024, MAX_METADATA_BYTES + 1 - len(data)))
            if not chunk:
                return bytes(data)
            data.extend(chunk)
        raise RightsError("rights record exceeds the metadata size limit")
    finally:
        os.close(fd)
        os.close(parent_fd)


def _load_json(path: Path) -> dict[str, object]:
    try:
        value = json.loads(_read_bounded(path))
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise RightsError("rights record is not valid JSON") from error
    if not isinstance(value, dict):
        raise RightsError("rights record must contain a JSON object")
    return value


def _digest_file(path: Path) -> str:
    parent_fd: int | None = None
    try:
        parent_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent_fd)
    except OSError as error:
        if parent_fd is not None:
            os.close(parent_fd)
        raise RightsError("evidence document must be a regular durable file") from error
    digest = hashlib.sha256()
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_METADATA_BYTES:
            raise RightsError("evidence document must be a regular bounded file")
        while chunk := os.read(fd, 64 * 1024):
            digest.update(chunk)
    finally:
        os.close(fd)
        os.close(parent_fd)
    return digest.hexdigest()


def _validate_durable_uri(value: str, *, label: str, local_only: bool = False) -> None:
    parsed = urlparse(value)
    if parsed.scheme in {"http", "https", "s3"}:
        if local_only:
            raise RightsError(f"{label} must identify an existing durable local document")
        if not parsed.netloc:
            raise RightsError(f"{label} must be a durable URI")
        return
    if parsed.scheme == "file":
        if not parsed.path.startswith("/"):
            raise RightsError(f"{label} must use an absolute durable path")
        return
    if not value.startswith("/"):
        raise RightsError(f"{label} must be a durable URI or absolute path")


def verify_rights_record(
    rights_path: Path,
    object_id: str,
    *,
    sealed_values: dict[str, str] | None = None,
) -> dict[str, object]:
    """Accept only a complete affirmative, immutable, dated rights decision."""
    raw_record = _load_json(rights_path)
    if set(raw_record) != RIGHTS_FIELDS or not all(
        type(value) is str and value == value.strip() and value for value in raw_record.values()
    ):
        raise RightsError("rights record must contain exactly the complete required fields")
    record = cast(dict[str, str], raw_record)
    if record["object_id"] != object_id:
        raise RightsError("rights record is not bound to this handoff object")
    if record["decision"] != "affirmative":
        raise RightsError("rights record decision must be affirmative")
    if record["responsible_reviewer"].casefold() == record["rights_authority"].casefold():
        raise RightsError("rights record requires distinct responsible reviewer and authority")
    if record["permitted_asset_use"] != "immutable research corpus artifact":
        raise RightsError("rights record permitted_asset_use is not the reviewed value")
    if record["attribution"] != "GeneReviews":
        raise RightsError("rights record attribution is not the reviewed value")
    for name in ("source_sha256", "artifact_sha256", "terms_sha256", "evidence_sha256"):
        if not SHA256_RE.fullmatch(record[name]):
            raise RightsError(f"rights record {name} must be a lowercase SHA-256")
    _validate_durable_uri(record["terms_uri"], label="terms_uri")
    _validate_durable_uri(record["evidence_uri"], label="evidence_uri", local_only=True)
    evidence = record["evidence_uri"]
    if evidence.startswith("/") or evidence.startswith("file:"):
        evidence_path = Path(urlparse(evidence).path if evidence.startswith("file:") else evidence)
        if _digest_file(evidence_path) != record["evidence_sha256"]:
            raise RightsError("evidence document digest does not match rights record")
    decision_time = record["decision_time"]
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z", decision_time):
        raise RightsError("rights record decision_time must be dated UTC")
    try:
        parsed_time = datetime.fromisoformat(f"{decision_time[:-1]}+00:00")
    except ValueError as error:
        raise RightsError(
            "rights record decision_time must be an ISO-8601 UTC timestamp"
        ) from error
    if parsed_time > datetime.now(UTC):
        raise RightsError("rights record decision_time cannot be in the future")
    unsigned = {key: value for key, value in record.items() if key != "rights_record_sha256"}
    if hashlib.sha256(_canonical(unsigned)).hexdigest() != record["rights_record_sha256"]:
        raise RightsError("rights record canonical digest mismatch")
    if sealed_values is not None:
        for name in ("source_sha256", "artifact_sha256", "corpus_release_id"):
            if record[name] != sealed_values[name]:
                raise RightsError(f"rights record is not bound to sealed {name}")
    return cast(dict[str, object], record)
