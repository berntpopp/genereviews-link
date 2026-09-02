"""The committed, versioned redistribution rights notice for the GeneReviews corpus.

GeneReviews is copyrighted and licensed for noncommercial research use only, so
redistribution needs an honest, reviewed notice rather than a per-release two-person
sign-off ceremony.  The notice lives in ``data/RIGHTS.json`` under version control; the
maintainer reviews the upstream terms, records the review date, and commits the result.

The bundle builder copies the validated notice verbatim into ``manifest.json`` so the
attribution and the use restriction travel with every published byte, and the corpus
verification workflow re-checks that the published manifest still matches the committed
notice.  No secret and no locator is involved: to refresh the determination, edit
``data/RIGHTS.json`` (and ``terms_reviewed_at``) in a reviewed pull request.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from hashlib import sha256
from pathlib import Path
from typing import cast

from genereview_link.strict_json import StrictJsonError, load_strict_json

DEFAULT_RIGHTS_PATH = Path(__file__).resolve().parents[2] / "data" / "RIGHTS.json"
MAX_RIGHTS_BYTES = 1 << 16

_FIELDS = frozenset(
    {
        "schema_version",
        "dataset",
        "license",
        "attribution",
        "citation",
        "source_url",
        "terms_url",
        "terms_reviewed_at",
        "reviewer",
        "use_restriction",
    }
)
_LICENSE_FIELDS = frozenset({"name", "spdx_id", "url"})


class RightsNoticeError(ValueError):
    """The committed rights notice is missing, malformed, or incomplete."""


@dataclass(frozen=True)
class RightsNotice:
    """A validated notice plus the canonical block published in the manifest."""

    digest: str
    license_name: str
    license_url: str
    attribution: str
    terms_url: str
    terms_reviewed_at: str
    reviewer: str
    use_restriction: str
    block: Mapping[str, object]


def _text(record: Mapping[str, object], field: str) -> str:
    value = record.get(field)
    if not isinstance(value, str) or not value.strip():
        raise RightsNoticeError(f"{field} must be a non-empty string")
    return value


def _https(record: Mapping[str, object], field: str) -> str:
    value = _text(record, field)
    if not value.startswith("https://"):
        raise RightsNoticeError(f"{field} must be an HTTPS URL")
    return value


def validate_rights_notice(raw: object, *, today: date | None = None) -> RightsNotice:
    """Validate a decoded rights notice for exact shape; never for staleness.

    The determination does not expire between releases, so the only temporal check is
    that the review date is not in the future.
    """
    if not isinstance(raw, dict) or set(raw) != _FIELDS:
        raise RightsNoticeError("rights notice must contain exactly the required fields")
    record = cast(Mapping[str, object], raw)
    if record.get("schema_version") != 1:
        raise RightsNoticeError("rights notice schema_version must be integer 1")
    licence = record.get("license")
    if not isinstance(licence, dict) or set(licence) != _LICENSE_FIELDS:
        raise RightsNoticeError("license must name exactly name, spdx_id, and url")
    licence_map = cast(Mapping[str, object], licence)
    name = _text(licence_map, "name")
    _text(licence_map, "spdx_id")
    url = _https(licence_map, "url")
    attribution = _text(record, "attribution")
    _text(record, "citation")
    _text(record, "dataset")
    reviewer = _text(record, "reviewer")
    restriction = _text(record, "use_restriction")
    if "research use only" not in restriction.casefold():
        raise RightsNoticeError("use_restriction must state that the corpus is research use only")
    _https(record, "source_url")
    terms_url = _https(record, "terms_url")
    reviewed_text = _text(record, "terms_reviewed_at")
    try:
        reviewed = date.fromisoformat(reviewed_text)
    except ValueError as error:
        raise RightsNoticeError("terms_reviewed_at must be an ISO-8601 date") from error
    if reviewed > (today or datetime.now(UTC).date()):
        raise RightsNoticeError("terms_reviewed_at must not be in the future")
    block = json.loads(json.dumps(dict(record), sort_keys=True))
    canonical = json.dumps(block, sort_keys=True, separators=(",", ":")).encode()
    return RightsNotice(
        digest="sha256:" + sha256(canonical).hexdigest(),
        license_name=name,
        license_url=url,
        attribution=attribution,
        terms_url=terms_url,
        terms_reviewed_at=reviewed.isoformat(),
        reviewer=reviewer,
        use_restriction=restriction,
        block=block,
    )


def load_rights_notice(path: Path | None = None, *, today: date | None = None) -> RightsNotice:
    """Read the committed notice through a bounded, no-follow descriptor."""
    source = DEFAULT_RIGHTS_PATH if path is None else path
    try:
        status = source.lstat()
        regular = source.is_file()
    except OSError as error:
        raise RightsNoticeError("rights notice is missing") from error
    if not regular or status.st_size > MAX_RIGHTS_BYTES:
        raise RightsNoticeError("rights notice must be a regular file within the size limit")
    try:
        descriptor = os.open(source, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(descriptor, "rb") as handle:
            payload = handle.read(MAX_RIGHTS_BYTES + 1)
    except OSError as error:
        raise RightsNoticeError("rights notice cannot be read without following links") from error
    if len(payload) > MAX_RIGHTS_BYTES:
        raise RightsNoticeError("rights notice exceeds the size limit")
    try:
        decoded = load_strict_json(payload, max_bytes=MAX_RIGHTS_BYTES)
    except StrictJsonError as error:
        raise RightsNoticeError("rights notice is not valid JSON") from error
    return validate_rights_notice(decoded, today=today)


__all__ = [
    "DEFAULT_RIGHTS_PATH",
    "MAX_RIGHTS_BYTES",
    "RightsNotice",
    "RightsNoticeError",
    "load_rights_notice",
    "validate_rights_notice",
]
