"""Strict rights-record validation for privileged corpus publication."""

from __future__ import annotations

import json
import os
import re
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
        "terms_version",
        "permitted_asset_use",
        "attribution",
        "evidence_uri",
        "source_sha256",
        "corpus_release_id",
    }
)


class RightsError(ValueError):
    """The rights record is incomplete or unsafe for publication."""


def _load_json(path: Path) -> dict[str, object]:
    try:
        info = path.lstat()
        if not os.path.isfile(path) or os.path.islink(path) or info.st_size > MAX_METADATA_BYTES:
            raise RightsError("rights record must be a regular bounded file")
        value = json.loads(path.read_bytes())
    except (FileNotFoundError, json.JSONDecodeError, UnicodeDecodeError) as error:
        raise RightsError("rights record is not valid JSON") from error
    if not isinstance(value, dict):
        raise RightsError("rights record must contain a JSON object")
    return value


def verify_rights_record(
    rights_path: Path,
    object_id: str,
    *,
    sealed_values: dict[str, str] | None = None,
) -> dict[str, object]:
    """Accept only a complete affirmative, durable, dated rights decision."""
    record = _load_json(rights_path)
    if set(record) != RIGHTS_FIELDS or not all(
        type(value) is str and value == value.strip() and value for value in record.values()
    ):
        raise RightsError("rights record must contain exactly the complete required fields")
    if record["object_id"] != object_id:
        raise RightsError("rights record is not bound to this handoff object")
    if record["decision"] != "affirmative":
        raise RightsError("rights record decision must be affirmative")
    reviewer = record["responsible_reviewer"]
    authority = record["rights_authority"]
    evidence = record["evidence_uri"]
    if not all(type(value) is str for value in (reviewer, authority, evidence)):
        raise RightsError("rights record contains invalid semantic field types")
    reviewer = cast(str, reviewer)
    authority = cast(str, authority)
    evidence = cast(str, evidence)
    if reviewer.casefold() == authority.casefold():
        raise RightsError("rights record requires distinct responsible reviewer and authority")
    parsed = urlparse(evidence)
    if parsed.scheme in {"http", "https", "s3"}:
        if not parsed.netloc:
            raise RightsError("rights record evidence_uri must be a durable URI")
    elif parsed.scheme == "file":
        if not parsed.path.startswith("/"):
            raise RightsError("rights record evidence_uri must use an absolute durable path")
    elif not evidence.startswith("/"):
        raise RightsError("rights record evidence_uri must be a durable URI or absolute path")
    for field in ("source_sha256", "artifact_sha256"):
        value = record[field]
        if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
            raise RightsError(f"rights record {field} must be a lowercase SHA-256")
    decision_time = record["decision_time"]
    if not isinstance(decision_time, str) or not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z", decision_time
    ):
        raise RightsError("rights record decision_time must be dated UTC")
    try:
        parsed_time = datetime.fromisoformat(f"{decision_time[:-1]}+00:00")
    except ValueError as error:
        raise RightsError(
            "rights record decision_time must be an ISO-8601 UTC timestamp"
        ) from error
    if parsed_time > datetime.now(UTC):
        raise RightsError("rights record decision_time cannot be in the future")
    if sealed_values is not None:
        for name in ("source_sha256", "artifact_sha256", "corpus_release_id"):
            if record[name] != sealed_values[name]:
                raise RightsError(f"rights record is not bound to sealed {name}")
    return record
