"""Strict, digest-bound rights evidence for privileged corpus publication."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from datetime import UTC, datetime
from pathlib import Path

MAX_METADATA_BYTES = 1 << 20
RIGHTS_AUTHORITY = "Bernt Popp / repository owner"
RIGHTS_APPROVAL_KIND = "repository-owner redistribution determination"
RIGHTS_AUTHORIZATION_URI = "https://github.com/berntpopp/genereviews-link/issues/27"
RIGHTS_TERMS_SOURCE_URI = "https://www.genereviews.org/"
RIGHTS_PERMITTED_ASSET_USE = (
    "immutable GeneReviews research corpus artifact for noncommercial research purposes only; "
    "no further modifications"
)
RIGHTS_ATTRIBUTION = (
    "GeneReviews® content ©1993-2026 University of Washington, Seattle; "
    "source https://www.genereviews.org; noncommercial research purposes only; "
    "comply with the copyright notice and Usage Disclaimer; no further modifications."
)
RIGHTS_FIELDS = frozenset(
    {
        "artifact_sha256",
        "object_id",
        "decision",
        "approval_kind",
        "upstream_approval",
        "responsible_reviewer",
        "rights_authority",
        "authorization_uri",
        "decision_time",
        "terms_uri",
        "terms_sha256",
        "terms_version",
        "terms_source_uri",
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
    from genereview_link.corpus.handoff import _open_directory

    parent_fd: int | None = None
    try:
        parent_fd = _open_directory(path.parent)
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


def _bundle_document(value: str, *, label: str, bundle_root: Path) -> Path:
    expected = {
        "terms_uri": "terms-snapshot.html",
        "evidence_uri": "rights-evidence.json",
    }[label]
    if value != f"bundle:{expected}":
        raise RightsError(f"{label} must be the exact transferable bundle reference")
    return bundle_root / expected


def verify_rights_record(
    rights_path: Path,
    object_id: str,
    *,
    sealed_values: dict[str, str] | None = None,
) -> dict[str, object]:
    """Accept only a complete affirmative, immutable, dated rights decision."""
    raw_record = _load_json(rights_path)
    string_fields = RIGHTS_FIELDS - {"upstream_approval"}
    if (
        set(raw_record) != RIGHTS_FIELDS
        or not all(
            type(raw_record.get(name)) is str
            and raw_record[name] == str(raw_record[name]).strip()
            and raw_record[name]
            for name in string_fields
        )
        or raw_record.get("upstream_approval") is not False
    ):
        raise RightsError("rights record must contain exactly the complete required fields")
    record = raw_record
    if record["object_id"] != object_id:
        raise RightsError("rights record is not bound to this handoff object")
    if record["decision"] != "affirmative":
        raise RightsError("rights record decision must be affirmative")
    if record["approval_kind"] != RIGHTS_APPROVAL_KIND or record["upstream_approval"] is not False:
        raise RightsError("rights record is not an explicit non-upstream owner determination")
    if record["rights_authority"] != RIGHTS_AUTHORITY:
        raise RightsError("rights record authority is not the repository owner")
    if record["authorization_uri"] != RIGHTS_AUTHORIZATION_URI:
        raise RightsError("rights record is not bound to the durable owner authorization")
    if str(record["responsible_reviewer"]).casefold() == str(record["rights_authority"]).casefold():
        raise RightsError("rights record requires distinct responsible reviewer and authority")
    if record["permitted_asset_use"] != RIGHTS_PERMITTED_ASSET_USE:
        raise RightsError("rights record permitted_asset_use is not the reviewed value")
    if record["attribution"] != RIGHTS_ATTRIBUTION:
        raise RightsError("rights record attribution is not the reviewed value")
    for name in ("source_sha256", "artifact_sha256", "terms_sha256", "evidence_sha256"):
        if not SHA256_RE.fullmatch(str(record[name])):
            raise RightsError(f"rights record {name} must be a lowercase SHA-256")
    terms_path = _bundle_document(
        str(record["terms_uri"]), label="terms_uri", bundle_root=rights_path.parent
    )
    terms_bytes = _read_bounded(terms_path)
    if hashlib.sha256(terms_bytes).hexdigest() != record["terms_sha256"]:
        raise RightsError("terms document digest does not match rights record")
    try:
        terms_text = terms_bytes.decode("utf-8")
    except UnicodeDecodeError as error:
        raise RightsError("terms snapshot is not valid UTF-8") from error
    required_terms = (
        "©1993-2026 University of Washington, Seattle",
        "https://www.genereviews.org",
        "noncommercial research purposes only",
        "copyright notice",
        "Usage Disclaimer",
        "no further modifications",
    )
    if record["terms_source_uri"] != RIGHTS_TERMS_SOURCE_URI or any(
        phrase not in terms_text for phrase in required_terms
    ):
        raise RightsError("terms snapshot does not contain the official reviewed terms")
    evidence_path = _bundle_document(
        str(record["evidence_uri"]), label="evidence_uri", bundle_root=rights_path.parent
    )
    evidence_bytes = _read_bounded(evidence_path)
    if hashlib.sha256(evidence_bytes).hexdigest() != record["evidence_sha256"]:
        raise RightsError("evidence document digest does not match rights record")
    try:
        evidence = json.loads(evidence_bytes)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise RightsError("rights evidence is not valid JSON") from error
    evidence_fields = {
        "format",
        "approval_kind",
        "upstream_approval",
        "rights_authority",
        "responsible_reviewer",
        "authorization_uri",
        "decision_time",
        "terms_source_uri",
        "permitted_asset_use",
        "attribution",
        "object_id",
        "source_sha256",
        "artifact_sha256",
        "corpus_release_id",
    }
    if not isinstance(evidence, dict) or set(evidence) != evidence_fields:
        raise RightsError("rights evidence has missing or extra claims")
    if evidence["format"] != "genereviews-owner-rights-evidence-v1" or any(
        evidence[name] != record[name] for name in evidence_fields - {"format"}
    ):
        raise RightsError("rights evidence does not match the owner determination")
    decision_time = str(record["decision_time"])
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
    return record
