"""Pure state checks binding release semantics to the one promotion PATCH."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

SHA = re.compile(r"^[0-9a-f]{40,64}$")


class PromotionStateError(RuntimeError):
    pass


def _assets(release: dict[str, Any]) -> list[dict[str, object]]:
    assets = release.get("assets")
    if not isinstance(assets, list):
        raise PromotionStateError("release assets are malformed")
    projected = []
    for asset in assets:
        if not isinstance(asset, dict):
            raise PromotionStateError("release asset identity is malformed")
        entry = {key: asset.get(key) for key in ("id", "name", "size", "digest")}
        if (
            type(entry["id"]) is not int
            or type(entry["size"]) is not int
            or not isinstance(entry["name"], str)
            or not isinstance(entry["digest"], str)
            or not re.fullmatch(r"sha256:[0-9a-f]{64}", entry["digest"])
        ):
            raise PromotionStateError("release asset identity is incomplete")
        projected.append(entry)
    names = [str(entry["name"]) for entry in projected]
    if sorted(names) != [
        "SHA256SUMS",
        "corpus.dump",
        "manifest.json",
        "publisher-tool.whl",
        "rights-evidence.json",
        "rights-record.json",
        "seal-manifest.json",
        "terms-snapshot.html",
    ]:
        raise PromotionStateError("release asset set is not exact")
    return sorted(projected, key=lambda entry: str(entry["name"]))


def freeze_release(
    release: dict[str, Any],
    *,
    etag: str,
    tag: str,
    target_commit: str,
    tag_object_sha: str,
) -> dict[str, object]:
    if (
        not etag
        or type(release.get("id")) is not int
        or release.get("tag_name") != tag
        or release.get("target_commitish") != target_commit
        or not SHA.fullmatch(target_commit)
        or not re.fullmatch(r"[0-9a-f]{40}", tag_object_sha)
        or release.get("draft") is not True
        or release.get("immutable") is not False
    ):
        raise PromotionStateError("draft release identity cannot be frozen")
    return {
        "release_id": release["id"],
        "tag": tag,
        "target_commit": target_commit,
        "tag_object_sha": tag_object_sha,
        "etag": etag,
        "assets": _assets(release),
    }


def assert_tag_ruleset(ruleset: dict[str, Any]) -> None:
    """Require an active, no-bypass rule that makes corpus tag refs immutable."""
    conditions = ruleset.get("conditions")
    ref_name = conditions.get("ref_name") if isinstance(conditions, dict) else None
    rules = ruleset.get("rules")
    rule_types = (
        {rule.get("type") for rule in rules if isinstance(rule, dict)}
        if isinstance(rules, list)
        else set()
    )
    if (
        type(ruleset.get("id")) is not int
        or ruleset.get("target") != "tag"
        or ruleset.get("enforcement") != "active"
        or ruleset.get("bypass_actors") != []
        or not isinstance(ref_name, dict)
        or ref_name.get("include") != ["refs/tags/corpus-data-*"]
        or ref_name.get("exclude") != []
        or not {"deletion", "update"}.issubset(rule_types)
    ):
        raise PromotionStateError("corpus tag ruleset is not active and immutable without bypass")


def assert_prepatch(
    frozen: dict[str, Any],
    *,
    conditional_status: int,
    tag_object_sha: str,
    ruleset: dict[str, Any],
) -> None:
    if conditional_status != 304:
        raise PromotionStateError("release ETag precondition changed after semantic verification")
    if tag_object_sha != frozen.get("tag_object_sha"):
        raise PromotionStateError("corpus tag changed after semantic verification")
    assert_tag_ruleset(ruleset)


def assert_postpublication(
    frozen: dict[str, Any], *, published: dict[str, Any], tag_ref: dict[str, Any]
) -> None:
    if (
        published.get("id") != frozen.get("release_id")
        or published.get("tag_name") != frozen.get("tag")
        or published.get("target_commitish") != frozen.get("target_commit")
        or published.get("draft") is not False
        or published.get("immutable") is not True
        or _assets(published) != frozen.get("assets")
    ):
        raise PromotionStateError("published release differs from the verified representation")
    obj = tag_ref.get("object")
    if (
        not isinstance(obj, dict)
        or obj.get("type") != "commit"
        or obj.get("sha") != frozen.get("target_commit")
    ):
        raise PromotionStateError("published tag does not bind the verified target")


def _read(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise PromotionStateError("state input must be a JSON object")
    return value


def main() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    freeze = subparsers.add_parser("freeze")
    freeze.add_argument("--release", required=True, type=Path)
    freeze.add_argument("--etag", required=True)
    freeze.add_argument("--tag", required=True)
    freeze.add_argument("--target", required=True)
    freeze.add_argument("--tag-object-sha", required=True)
    freeze.add_argument("--out", required=True, type=Path)
    prepatch = subparsers.add_parser("prepatch")
    prepatch.add_argument("--frozen", required=True, type=Path)
    prepatch.add_argument("--status", required=True, type=int)
    prepatch.add_argument("--tag-object-sha", required=True)
    prepatch.add_argument("--ruleset", required=True, type=Path)
    ruleset = subparsers.add_parser("ruleset")
    ruleset.add_argument("--ruleset", required=True, type=Path)
    post = subparsers.add_parser("post")
    post.add_argument("--frozen", required=True, type=Path)
    post.add_argument("--published", required=True, type=Path)
    post.add_argument("--tag-ref", required=True, type=Path)
    args = parser.parse_args()
    if args.command == "freeze":
        state = freeze_release(
            _read(args.release),
            etag=args.etag,
            tag=args.tag,
            target_commit=args.target,
            tag_object_sha=args.tag_object_sha,
        )
        args.out.write_text(json.dumps(state, sort_keys=True, separators=(",", ":")) + "\n")
    elif args.command == "prepatch":
        assert_prepatch(
            _read(args.frozen),
            conditional_status=args.status,
            tag_object_sha=args.tag_object_sha,
            ruleset=_read(args.ruleset),
        )
    elif args.command == "ruleset":
        assert_tag_ruleset(_read(args.ruleset))
    else:
        assert_postpublication(
            _read(args.frozen), published=_read(args.published), tag_ref=_read(args.tag_ref)
        )


if __name__ == "__main__":
    main()
