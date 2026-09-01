"""Bounded retrieval of the three exact data-only GitHub Release assets."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from urllib.parse import quote

import httpx

from genereview_link.download_guard import (
    STREAM_TIMEOUT,
    build_host_allowlist,
    make_url_guard,
    read_capped,
    stream_to_file,
)

GITHUB_API = "https://api.github.com"
ASSET_NAMES = ("corpus.dump", "manifest.json", "SHA256SUMS")
PUBLICATION_ASSET_NAMES = (*ASSET_NAMES, "rights-record.json")
MAX_METADATA_BYTES = 1 << 20
MAX_DUMP_BYTES = 2 * 1024**3
METADATA_DEADLINE_SECONDS = 2 * 60.0
DUMP_DEADLINE_SECONDS = 45 * 60.0
_REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_DOWNLOAD_HOSTS = frozenset(
    {
        "api.github.com",
        "github.com",
        "release-assets.githubusercontent.com",
        "objects.githubusercontent.com",
        "github-releases.githubusercontent.com",
    }
)
_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_REVISION_RE = re.compile(r"^[0-9a-f]{40,64}$")


class ReleaseAssetError(RuntimeError):
    """A release did not provide one exact bounded data-only asset set."""


@dataclass(frozen=True)
class AssetIdentity:
    asset_id: int
    name: str
    size: int
    digest: str
    url: str
    downloaded_sha256: str = ""


@dataclass(frozen=True)
class ReleaseIdentity:
    release_id: int
    tag: str
    target_commit: str
    assets: tuple[AssetIdentity, ...]

    def as_json(self) -> dict[str, object]:
        return asdict(self)


def _assert_fresh_directory(destination: Path) -> None:
    try:
        info = destination.lstat()
    except FileNotFoundError as error:
        raise ReleaseAssetError("destination must be a pre-created fresh directory") from error
    if destination.is_symlink() or not destination.is_dir() or any(destination.iterdir()):
        raise ReleaseAssetError("destination must be a fresh real directory")
    if info.st_uid != os.geteuid():
        raise ReleaseAssetError("destination must be owned by the invoking user")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


async def _release_assets(repo: str, tag: str, token: str) -> ReleaseIdentity:
    if not _REPO.fullmatch(repo) or not tag:
        raise ReleaseAssetError("repository or release tag is invalid")
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    url = f"{GITHUB_API}/repos/{repo}/releases/tags/{quote(tag, safe='')}"
    async with httpx.AsyncClient(
        headers=headers,
        timeout=STREAM_TIMEOUT,
        follow_redirects=False,
        event_hooks={"request": [make_url_guard(build_host_allowlist(GITHUB_API))]},
    ) as client:
        try:
            payload = await read_capped(
                client,
                url,
                max_bytes=MAX_METADATA_BYTES,
                deadline_seconds=METADATA_DEADLINE_SECONDS,
            )
        except Exception as error:
            raise ReleaseAssetError("release metadata could not be read safely") from error
    try:
        release = json.loads(payload)
        if not isinstance(release, dict):
            raise TypeError("release metadata is not an object")
        assets = release["assets"]
    except (KeyError, TypeError, json.JSONDecodeError) as error:
        raise ReleaseAssetError("release metadata is malformed") from error
    if not isinstance(assets, list):
        raise ReleaseAssetError("release metadata assets are malformed")
    release_id = release.get("id")
    target = release.get("target_commitish")
    if (
        type(release_id) is not int
        or release_id <= 0
        or release.get("tag_name") != tag
        or release.get("draft") is not False
        or release.get("prerelease") is not False
        or release.get("immutable") is not True
        or not isinstance(target, str)
        or not _REVISION_RE.fullmatch(target)
    ):
        raise ReleaseAssetError(
            "source release must be the exact immutable non-draft, non-prerelease release"
        )
    identities: dict[str, AssetIdentity] = {}
    for asset in assets:
        if not isinstance(asset, dict):
            raise ReleaseAssetError("release asset metadata is malformed")
        name = asset.get("name")
        asset_url = asset.get("url")
        asset_id = asset.get("id")
        size = asset.get("size")
        digest = asset.get("digest")
        if (
            not isinstance(name, str)
            or not isinstance(asset_url, str)
            or type(asset_id) is not int
            or asset_id <= 0
            or type(size) is not int
            or size <= 0
            or not isinstance(digest, str)
            or not _SHA256_RE.fullmatch(digest)
            or asset_url != f"{GITHUB_API}/repos/{repo}/releases/assets/{asset_id}"
        ):
            raise ReleaseAssetError("release asset name or URL is invalid")
        if name in identities or name not in PUBLICATION_ASSET_NAMES:
            raise ReleaseAssetError("release has unexpected or duplicate data assets")
        identities[name] = AssetIdentity(
            asset_id=asset_id,
            name=name,
            size=size,
            digest=digest,
            url=asset_url,
        )
    if set(identities) != set(PUBLICATION_ASSET_NAMES):
        raise ReleaseAssetError(
            "release must contain the exact data assets and public rights record"
        )
    return ReleaseIdentity(
        release_id=release_id,
        tag=tag,
        target_commit=target,
        assets=tuple(identities[name] for name in PUBLICATION_ASSET_NAMES),
    )


async def download_release_assets(
    repo: str, tag: str, destination: Path, *, token: str = ""
) -> ReleaseIdentity:
    """Download exact assets through allowlisted, byte- and deadline-bounded streams."""
    _assert_fresh_directory(destination)
    identity = await _release_assets(repo, tag, token)
    assets = {asset.name: asset for asset in identity.assets}
    headers = {"Accept": "application/octet-stream"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    downloaded: dict[str, str] = {}
    try:
        for name in PUBLICATION_ASSET_NAMES:
            asset = assets[name]
            url = asset.url
            target = destination / name
            limit = MAX_DUMP_BYTES if name == "corpus.dump" else MAX_METADATA_BYTES
            deadline = DUMP_DEADLINE_SECONDS if name == "corpus.dump" else METADATA_DEADLINE_SECONDS
            async with httpx.AsyncClient(
                headers=headers,
                timeout=STREAM_TIMEOUT,
                follow_redirects=True,
                max_redirects=5,
                # The initial URL is the API's exact numeric asset identity. The hook
                # also guards every redirect before any request can leave the process.
                event_hooks={"request": [make_url_guard(_DOWNLOAD_HOSTS)]},
            ) as client:
                await stream_to_file(
                    client, url, target, max_bytes=limit, deadline_seconds=deadline
                )
            if target.stat().st_size != asset.size:
                raise ReleaseAssetError(f"downloaded size does not match API identity: {name}")
            downloaded[name] = _sha256_file(target)
            if f"sha256:{downloaded[name]}" != asset.digest:
                raise ReleaseAssetError(f"downloaded digest does not match API identity: {name}")
    except Exception:
        for name in PUBLICATION_ASSET_NAMES:
            (destination / name).unlink(missing_ok=True)
        raise
    result = replace(
        identity,
        assets=tuple(
            replace(asset, downloaded_sha256=downloaded[asset.name]) for asset in identity.assets
        ),
    )
    # The public rights record is identity evidence, not part of the PostgreSQL
    # data-only restore bundle. Its verified digest remains in ``result``.
    (destination / "rights-record.json").unlink()
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--dest", required=True, type=Path)
    parser.add_argument("--identity-out", type=Path)
    args = parser.parse_args()
    identity = asyncio.run(
        download_release_assets(args.repo, args.tag, args.dest, token=os.getenv("GH_TOKEN", ""))
    )
    if args.identity_out is not None:
        args.identity_out.write_text(
            json.dumps(identity.as_json(), sort_keys=True, separators=(",", ":")) + "\n"
        )


if __name__ == "__main__":
    main()
