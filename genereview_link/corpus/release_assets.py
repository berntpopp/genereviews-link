"""Bounded retrieval of the exact public corpus reconstruction assets."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from urllib.parse import quote

import httpx

from genereview_link.download_admission import (
    DownloadAdmissionError,
    PinnedDownloadDirectory,
)
from genereview_link.download_guard import (
    STREAM_TIMEOUT,
    DownloadOwnership,
    build_host_allowlist,
    make_url_guard,
    read_capped,
    stream_to_file,
)
from genereview_link.strict_json import StrictJsonError, load_strict_json

GITHUB_API = "https://api.github.com"
ASSET_NAMES = ("corpus.dump", "manifest.json", "SHA256SUMS")
PUBLICATION_ASSET_NAMES = (
    *ASSET_NAMES,
    "rights-record.json",
    "rights-evidence.json",
    "terms-snapshot.html",
    "seal-manifest.json",
    "publisher-tool.whl",
)
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


def _open_fresh_directory(destination: Path) -> PinnedDownloadDirectory:
    try:
        return PinnedDownloadDirectory.open_fresh(destination)
    except DownloadAdmissionError as error:
        raise ReleaseAssetError("destination must be a pre-created fresh directory") from error


async def _release_assets(
    repo: str,
    tag: str,
    token: str,
    *,
    release_id: int | None = None,
    allow_draft: bool = False,
) -> ReleaseIdentity:
    if not _REPO.fullmatch(repo) or not tag or (release_id is not None and release_id <= 0):
        raise ReleaseAssetError("repository or release tag is invalid")
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    url = (
        f"{GITHUB_API}/repos/{repo}/releases/{release_id}"
        if release_id is not None
        else f"{GITHUB_API}/repos/{repo}/releases/tags/{quote(tag, safe='')}"
    )
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
        release = load_strict_json(payload, max_bytes=MAX_METADATA_BYTES)
        if not isinstance(release, dict):
            raise TypeError("release metadata is not an object")
        assets = release["assets"]
    except StrictJsonError as error:
        raise ReleaseAssetError("release metadata is not strict bounded JSON") from error
    except (KeyError, TypeError) as error:
        raise ReleaseAssetError("release metadata is malformed") from error
    if not isinstance(assets, list):
        raise ReleaseAssetError("release metadata assets are malformed")
    release_id = release.get("id")
    target = release.get("target_commitish")
    if (
        type(release_id) is not int
        or release_id <= 0
        or release.get("tag_name") != tag
        or release.get("prerelease") is not False
        or (
            (release.get("draft") is not False or release.get("immutable") is not True)
            and not (
                allow_draft
                and release.get("draft") is True
                and release.get("immutable") is False
                and release.get("published_at") is None
            )
        )
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
    repo: str,
    tag: str,
    destination: Path,
    *,
    token: str = "",
    release_id: int | None = None,
    allow_draft: bool = False,
) -> ReleaseIdentity:
    """Download exact assets through allowlisted, byte- and deadline-bounded streams."""
    directory = _open_fresh_directory(destination)
    try:
        identity = await _release_assets(
            repo, tag, token, release_id=release_id, allow_draft=allow_draft
        )
    except BaseException:
        directory.close()
        raise
    assets = {asset.name: asset for asset in identity.assets}
    headers = {"Accept": "application/octet-stream"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    downloaded: dict[str, str] = {}
    created: dict[str, DownloadOwnership] = {}
    retained: list[DownloadOwnership] = []
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
                ownership_output: list[DownloadOwnership] = []
                try:
                    digest = await stream_to_file(
                        client,
                        url,
                        target,
                        max_bytes=limit,
                        deadline_seconds=deadline,
                        created_ownership=ownership_output,
                        defer_admission=True,
                        destination_directory=directory,
                    )
                finally:
                    retained.extend(ownership_output)
            if len(ownership_output) != 1 or ownership_output[0].closed:
                raise ReleaseAssetError(f"download did not report created identity: {name}")
            created[name] = ownership_output[0]
            ownership = created[name]
            info = ownership.stat()
            if info.st_size != asset.size:
                raise ReleaseAssetError(f"downloaded size does not match API identity: {name}")
            downloaded[name] = digest
            if f"sha256:{downloaded[name]}" != asset.digest:
                raise ReleaseAssetError(f"downloaded digest does not match API identity: {name}")
            try:
                ownership.admit_exact(
                    expected_sha256=asset.digest.removeprefix("sha256:"),
                    expected_size=asset.size,
                    mode=0o400,
                )
            except Exception as error:
                raise ReleaseAssetError(
                    f"downloaded digest failed exact admission: {name}"
                ) from error
        if (
            len(created) != len(PUBLICATION_ASSET_NAMES)
            or directory.names() != frozenset(PUBLICATION_ASSET_NAMES)
            or not directory.matches_path()
            or any(
                not ownership.matches_path() or not ownership.parent_matches_path()
                for ownership in created.values()
            )
        ):
            raise ReleaseAssetError("release asset path changed before complete admission")
    except BaseException:
        for ownership in retained:
            ownership.unlink_if_owned()
        raise
    finally:
        for ownership in retained:
            ownership.close()
        directory.close()
    result = replace(
        identity,
        assets=tuple(
            replace(asset, downloaded_sha256=downloaded[asset.name]) for asset in identity.assets
        ),
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--release-id", type=int)
    parser.add_argument("--allow-draft", action="store_true")
    parser.add_argument("--dest", required=True, type=Path)
    parser.add_argument("--identity-out", type=Path)
    args = parser.parse_args()
    identity = asyncio.run(
        download_release_assets(
            args.repo,
            args.tag,
            args.dest,
            token=os.getenv("GH_TOKEN", ""),
            release_id=args.release_id,
            allow_draft=args.allow_draft,
        )
    )
    if args.identity_out is not None:
        args.identity_out.write_text(
            json.dumps(identity.as_json(), sort_keys=True, separators=(",", ":")) + "\n"
        )


if __name__ == "__main__":
    main()
