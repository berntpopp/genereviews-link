"""Exact identity checks for publication verifier dispatch acceptance."""

from __future__ import annotations

from datetime import datetime
from typing import Any


class DispatchIdentityError(ValueError):
    """A verifier run is stale or not bound to the requested publication."""


def _time(value: object) -> datetime:
    if not isinstance(value, str):
        raise DispatchIdentityError("dispatch timestamp is missing")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise DispatchIdentityError("dispatch timestamp is malformed") from error


def verify_acceptance(accepted: dict[str, Any], *, expected: dict[str, Any]) -> None:
    for key in (
        "release_id",
        "target_commit",
        "assets_sha256",
        "nonce",
        "dispatch_time",
        "phase",
        "release_etag",
    ):
        if key not in expected:
            continue
        if accepted.get(key) != expected.get(key):
            raise DispatchIdentityError(f"acceptance {key} does not match dispatch")
    if accepted.get("head_sha") != expected.get("target_commit"):
        raise DispatchIdentityError("acceptance head SHA does not match target commit")
    if accepted.get("status") != "passed" or type(accepted.get("run_id")) is not int:
        raise DispatchIdentityError("acceptance run did not pass with an exact run ID")
    if _time(accepted.get("run_started_at")) < _time(expected.get("dispatch_time")):
        raise DispatchIdentityError("acceptance run predates its dispatch")
    source_ref = accepted.get("source_ref")
    expected_ref = expected.get("source_ref")
    if expected_ref is not None:
        if source_ref != expected_ref:
            raise DispatchIdentityError("acceptance source ref does not match dispatch")
    elif not isinstance(source_ref, str) or not source_ref.startswith("refs/tags/corpus-data-"):
        raise DispatchIdentityError("acceptance source ref is not the exact corpus tag")


__all__ = ["DispatchIdentityError", "verify_acceptance"]
