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
    if sorted(names) != ["SHA256SUMS", "corpus.dump", "manifest.json", "rights-record.json"]:
        raise PromotionStateError("release asset set is not exact")
    return sorted(projected, key=lambda entry: str(entry["name"]))


def freeze_release(
    release: dict[str, Any], *, etag: str, tag: str, target_commit: str
) -> dict[str, object]:
    if (
        not etag
        or type(release.get("id")) is not int
        or release.get("tag_name") != tag
        or release.get("target_commitish") != target_commit
        or not SHA.fullmatch(target_commit)
        or release.get("draft") is not True
        or release.get("immutable") is not False
    ):
        raise PromotionStateError("draft release identity cannot be frozen")
    return {
        "release_id": release["id"],
        "tag": tag,
        "target_commit": target_commit,
        "etag": etag,
        "assets": _assets(release),
    }


def assert_prepatch(
    frozen: dict[str, Any], *, conditional_status: int, tag_ref: dict[str, Any]
) -> None:
    if conditional_status != 304:
        raise PromotionStateError("release ETag precondition changed after semantic verification")
    obj = tag_ref.get("object")
    if (
        not isinstance(obj, dict)
        or obj.get("type") != "commit"
        or obj.get("sha") != frozen.get("target_commit")
    ):
        raise PromotionStateError("tag changed after semantic verification")


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
    assert_prepatch(frozen, conditional_status=304, tag_ref=tag_ref)


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
    freeze.add_argument("--out", required=True, type=Path)
    prepatch = subparsers.add_parser("prepatch")
    prepatch.add_argument("--frozen", required=True, type=Path)
    prepatch.add_argument("--status", required=True, type=int)
    prepatch.add_argument("--tag-ref", required=True, type=Path)
    post = subparsers.add_parser("post")
    post.add_argument("--frozen", required=True, type=Path)
    post.add_argument("--published", required=True, type=Path)
    post.add_argument("--tag-ref", required=True, type=Path)
    args = parser.parse_args()
    if args.command == "freeze":
        state = freeze_release(
            _read(args.release), etag=args.etag, tag=args.tag, target_commit=args.target
        )
        args.out.write_text(json.dumps(state, sort_keys=True, separators=(",", ":")) + "\n")
    elif args.command == "prepatch":
        assert_prepatch(
            _read(args.frozen), conditional_status=args.status, tag_ref=_read(args.tag_ref)
        )
    else:
        assert_postpublication(
            _read(args.frozen), published=_read(args.published), tag_ref=_read(args.tag_ref)
        )


if __name__ == "__main__":
    main()
