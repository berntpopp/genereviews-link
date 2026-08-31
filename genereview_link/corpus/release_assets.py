"""Bounded retrieval of the three exact data-only GitHub Release assets."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
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
MAX_METADATA_BYTES = 1 << 20
MAX_DUMP_BYTES = 2 * 1024**3
METADATA_DEADLINE_SECONDS = 2 * 60.0
DUMP_DEADLINE_SECONDS = 45 * 60.0
_REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_DOWNLOAD_HOSTS = frozenset(
    {
        "github.com",
        "release-assets.githubusercontent.com",
        "objects.githubusercontent.com",
        "github-releases.githubusercontent.com",
    }
)


class ReleaseAssetError(RuntimeError):
    """A release did not provide one exact bounded data-only asset set."""


def _assert_fresh_directory(destination: Path) -> None:
    try:
        info = destination.lstat()
    except FileNotFoundError as error:
        raise ReleaseAssetError("destination must be a pre-created fresh directory") from error
    if destination.is_symlink() or not destination.is_dir() or any(destination.iterdir()):
        raise ReleaseAssetError("destination must be a fresh real directory")
    if info.st_uid != os.geteuid():
        raise ReleaseAssetError("destination must be owned by the invoking user")


async def _release_assets(repo: str, tag: str, token: str) -> dict[str, str]:
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
        assets = release["assets"]
    except (KeyError, TypeError, json.JSONDecodeError) as error:
        raise ReleaseAssetError("release metadata is malformed") from error
    if not isinstance(assets, list):
        raise ReleaseAssetError("release metadata assets are malformed")
    urls: dict[str, str] = {}
    for asset in assets:
        if not isinstance(asset, dict):
            raise ReleaseAssetError("release asset metadata is malformed")
        name = asset.get("name")
        asset_url = asset.get("browser_download_url")
        if not isinstance(name, str) or not isinstance(asset_url, str):
            raise ReleaseAssetError("release asset name or URL is invalid")
        if name in urls or name not in ASSET_NAMES:
            raise ReleaseAssetError("release has unexpected or duplicate data assets")
        urls[name] = asset_url
    if set(urls) != set(ASSET_NAMES):
        raise ReleaseAssetError(
            "release must contain exactly corpus.dump, manifest.json, SHA256SUMS"
        )
    return urls


async def download_release_assets(
    repo: str, tag: str, destination: Path, *, token: str = ""
) -> None:
    """Download exact assets through allowlisted, byte- and deadline-bounded streams."""
    _assert_fresh_directory(destination)
    urls = await _release_assets(repo, tag, token)
    headers = {"Accept": "application/octet-stream"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        for name in ("manifest.json", "SHA256SUMS", "corpus.dump"):
            url = urls[name]
            target = destination / name
            limit = MAX_DUMP_BYTES if name == "corpus.dump" else MAX_METADATA_BYTES
            deadline = DUMP_DEADLINE_SECONDS if name == "corpus.dump" else METADATA_DEADLINE_SECONDS
            async with httpx.AsyncClient(
                headers=headers,
                timeout=STREAM_TIMEOUT,
                follow_redirects=True,
                max_redirects=5,
                event_hooks={
                    "request": [make_url_guard(build_host_allowlist(url) | _DOWNLOAD_HOSTS)]
                },
            ) as client:
                await stream_to_file(
                    client, url, target, max_bytes=limit, deadline_seconds=deadline
                )
    except Exception:
        for name in ASSET_NAMES:
            (destination / name).unlink(missing_ok=True)
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--dest", required=True, type=Path)
    args = parser.parse_args()
    asyncio.run(
        download_release_assets(args.repo, args.tag, args.dest, token=os.getenv("GH_TOKEN", ""))
    )


if __name__ == "__main__":
    main()
