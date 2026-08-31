"""Rights gate shared by the stdlib-only sealed publisher and the CLI."""

from __future__ import annotations

import json
from pathlib import Path

from genereview_link.corpus.handoff import HandoffError, SealedHandoff, verify_handoff


def verify_rights_record(
    rights_path: Path, object_id: str, *, sealed: SealedHandoff | None = None
) -> dict[str, object]:
    from genereview_link.corpus.rights import RightsError
    from genereview_link.corpus.rights import verify_rights_record as verify

    sealed_values: dict[str, str] | None = None
    if sealed is not None:
        try:
            seal = json.loads(sealed.manifest.read_bytes())
        except (OSError, json.JSONDecodeError) as error:
            raise HandoffError("sealed manifest is not valid JSON") from error
        if not isinstance(seal, dict):
            raise HandoffError("sealed manifest must be a JSON object")
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
