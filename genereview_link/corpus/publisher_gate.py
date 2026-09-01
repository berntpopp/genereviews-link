"""Rights gate shared by the stdlib-only sealed publisher and the CLI."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from genereview_link.corpus.handoff import (
    MAX_METADATA_BYTES,
    HandoffError,
    SealedHandoff,
    _open_directory,
    _read_capped,
    verify_handoff,
)
from genereview_link.strict_json import StrictJsonError, load_strict_json


def _read_bound_seal(sealed: SealedHandoff) -> dict[str, object]:
    if sealed.manifest.parent != sealed.path or sealed.manifest.name != "seal-manifest.json":
        raise HandoffError("sealed manifest is outside the admitted handoff")
    parent_fd = _open_directory(sealed.path)
    try:
        raw = _read_capped(sealed.manifest, limit=MAX_METADATA_BYTES, parent_fd=parent_fd)
    finally:
        os.close(parent_fd)
    if hashlib.sha256(raw).hexdigest() != sealed.object_id:
        raise HandoffError("sealed manifest identity changed")
    try:
        value = load_strict_json(raw, max_bytes=MAX_METADATA_BYTES)
    except StrictJsonError as error:
        raise HandoffError("sealed manifest is not strict bounded JSON") from error
    if not isinstance(value, dict):
        raise HandoffError("sealed manifest must be a JSON object")
    return value


def verify_rights_record(
    rights_path: Path, object_id: str, *, sealed: SealedHandoff | None = None
) -> dict[str, object]:
    from genereview_link.corpus.rights import RightsError
    from genereview_link.corpus.rights import verify_rights_record as verify

    sealed_values: dict[str, str] | None = None
    if sealed is not None:
        seal = _read_bound_seal(sealed)
        sealed_values = {}
        for name in ("source_sha256", "artifact_sha256", "corpus_release_id"):
            value = seal.get(name)
            if not isinstance(value, str):
                raise HandoffError(f"sealed manifest is missing string {name}")
            sealed_values[name] = value
    try:
        return verify(rights_path, object_id, sealed_values=sealed_values)
    except RightsError as error:
        raise HandoffError(str(error)) from error


def prepare_publish_handoff(handoff_root: Path, object_id: str, rights_path: Path) -> SealedHandoff:
    """Reverify an object and rights record; intentionally performs no publication."""
    sealed = verify_handoff(handoff_root, object_id)
    verify_rights_record(rights_path, sealed.object_id, sealed=sealed)
    return sealed
