"""Pure publication-state decisions for resumable immutable corpus releases."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_GIT_SHA = re.compile(r"^[0-9a-f]{40}$")
_OBJECT_ID = re.compile(r"^[0-9a-f]{64}$")
EXPECTED_RELEASE_ASSETS = (
    "corpus.dump",
    "manifest.json",
    "SHA256SUMS",
    "rights-record.json",
    "rights-evidence.json",
    "terms-snapshot.html",
    "seal-manifest.json",
    "publisher-tool.whl",
)


class ReleaseTransactionError(ValueError):
    """Remote release state is unsafe or conflicts with the sealed object."""


@dataclass(frozen=True)
class ReleasePlan:
    action: Literal["create", "resume", "promote", "noop"]
    missing_assets: tuple[str, ...]


def _expected_assets(value: dict[str, dict[str, object]]) -> dict[str, tuple[int, str]]:
    if set(value) != set(EXPECTED_RELEASE_ASSETS):
        raise ReleaseTransactionError("expected publication asset set is not exact")
    expected: dict[str, tuple[int, str]] = {}
    for name in EXPECTED_RELEASE_ASSETS:
        facts = value[name]
        if (
            not isinstance(name, str)
            or not name
            or "/" in name
            or set(facts) != {"size", "digest"}
            or not isinstance(facts["size"], int)
            or isinstance(facts["size"], bool)
            or facts["size"] <= 0
            or not isinstance(facts["digest"], str)
            or not _DIGEST.fullmatch(facts["digest"])
        ):
            raise ReleaseTransactionError("expected publication asset identity is invalid")
        expected[name] = (facts["size"], facts["digest"])
    return expected


def plan_release(
    release: dict[str, object] | None,
    *,
    tag: str,
    target: str,
    expected_assets: dict[str, dict[str, object]],
    required_body_markers: tuple[str, ...],
) -> ReleasePlan:
    """Return the only safe next action for an absent, partial, or complete release."""
    expected = _expected_assets(expected_assets)
    if (
        not _GIT_SHA.fullmatch(target)
        or not tag.startswith("corpus-data-")
        or not required_body_markers
        or any(not marker for marker in required_body_markers)
    ):
        raise ReleaseTransactionError("expected release identity is invalid")
    if release is None:
        return ReleasePlan("create", tuple(expected))
    if release.get("tag_name") != tag or release.get("target_commitish") != target:
        raise ReleaseTransactionError("release identity conflicts with the sealed target")
    body = release.get("body")
    if (
        release.get("prerelease") is not False
        or not isinstance(body, str)
        or not required_body_markers
        or any(not marker or marker not in body for marker in required_body_markers)
    ):
        raise ReleaseTransactionError("release does not contain the exact rights markers")
    assets = release.get("assets")
    if not isinstance(assets, list):
        raise ReleaseTransactionError("release asset inventory is invalid")
    actual: dict[str, tuple[int, str]] = {}
    ids: set[int] = set()
    for item in assets:
        if not isinstance(item, dict):
            raise ReleaseTransactionError("release asset inventory is invalid")
        name, size, digest, asset_id = (
            item.get("name"),
            item.get("size"),
            item.get("digest"),
            item.get("id"),
        )
        if (
            not isinstance(name, str)
            or name in actual
            or name not in expected
            or not isinstance(size, int)
            or isinstance(size, bool)
            or not isinstance(digest, str)
            or not isinstance(asset_id, int)
            or isinstance(asset_id, bool)
            or asset_id <= 0
            or asset_id in ids
        ):
            raise ReleaseTransactionError("release asset inventory conflicts with the sealed set")
        ids.add(asset_id)
        actual[name] = (size, digest)
        if actual[name] != expected[name]:
            raise ReleaseTransactionError(f"release asset {name} conflicts with sealed bytes")
    draft = release.get("draft")
    immutable = release.get("immutable")
    published_at = release.get("published_at")
    missing = tuple(name for name in expected if name not in actual)
    if draft is True and immutable is False and published_at is None:
        return ReleasePlan("resume" if missing else "promote", missing)
    if draft is False and immutable is True and isinstance(published_at, str) and not missing:
        return ReleasePlan("noop", ())
    raise ReleaseTransactionError("release lifecycle state conflicts with immutable publication")


def annotated_tag_message(object_id: str) -> str:
    if not _OBJECT_ID.fullmatch(object_id):
        raise ReleaseTransactionError("sealed object id is invalid")
    return f"GeneReviews sealed object {object_id}"


def verify_annotated_tag(
    tag_ref: dict[str, object],
    tag_object: dict[str, object],
    *,
    tag: str,
    target: str,
    object_id: str,
) -> None:
    """Accept only our exact immutable annotated tag, never a lightweight tag."""
    ref_object = tag_ref.get("object")
    target_object = tag_object.get("object")
    if (
        not isinstance(ref_object, dict)
        or ref_object.get("type") != "tag"
        or not isinstance(ref_object.get("sha"), str)
        or tag_object.get("sha") != ref_object["sha"]
    ):
        raise ReleaseTransactionError("release tag must be the exact annotated tag")
    if (
        tag_object.get("tag") != tag
        or tag_object.get("message") != annotated_tag_message(object_id)
        or not isinstance(target_object, dict)
        or target_object.get("type") != "commit"
        or target_object.get("sha") != target
    ):
        raise ReleaseTransactionError("annotated tag conflicts with sealed publication identity")


def verify_existing_tag_state(
    tag_ref: dict[str, object] | None,
    tag_object: dict[str, object] | None,
    *,
    tag: str,
    target: str,
    object_id: str,
) -> None:
    """Allow an absent tag or the exact sealed annotated tag, never a partial conflict."""
    if tag_ref is None and tag_object is None:
        return
    if tag_ref is None or tag_object is None:
        raise ReleaseTransactionError("existing release tag state is incomplete")
    verify_annotated_tag(tag_ref, tag_object, tag=tag, target=target, object_id=object_id)


__all__ = [
    "EXPECTED_RELEASE_ASSETS",
    "ReleasePlan",
    "ReleaseTransactionError",
    "annotated_tag_message",
    "plan_release",
    "verify_annotated_tag",
    "verify_existing_tag_state",
]
