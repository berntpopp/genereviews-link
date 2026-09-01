"""Fetch one exact retained offline source capture from durable release assets."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable
from pathlib import Path
from urllib.parse import unquote, urlsplit

import httpx

from genereview_link.download_guard import STREAM_TIMEOUT, make_url_guard, stream_to_file

SOURCE_ASSETS = frozenset(
    {
        "source-capture.json",
        "prior-manifest.json",
        "prior-seal-manifest.json",
        "gene_NBK1116.tar.gz",
        "GRtitle_shortname_NBKid.txt",
        "NBKid_shortname_genesymbol.txt",
        "NBKid_shortname_OMIM.txt",
    }
)
MAX_LOCATOR_BYTES = 48 * 1024
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_REDIRECT_HOSTS = frozenset(
    {
        "api.github.com",
        "github.com",
        "release-assets.githubusercontent.com",
        "objects.githubusercontent.com",
        "github-releases.githubusercontent.com",
    }
)


class SourceLocatorError(ValueError):
    """The retained-source locator or downloaded bytes are not exact."""


def load_source_locator(raw: bytes, *, allowed_repositories: Iterable[str]) -> dict[str, object]:
    if not 0 < len(raw) <= MAX_LOCATOR_BYTES:
        raise SourceLocatorError("source locator exceeds the protected-secret bound")
    try:
        locator = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise SourceLocatorError("source locator is not valid JSON") from error
    if (
        not isinstance(locator, dict)
        or set(locator) != {"format", "assets"}
        or locator["format"] != "genereviews-source-locator-v1"
    ):
        raise SourceLocatorError("source locator format is invalid")
    allowed = set(allowed_repositories)
    if not allowed or any(not _REPOSITORY.fullmatch(repo) for repo in allowed):
        raise SourceLocatorError("source repository allowlist is invalid")
    assets = locator["assets"]
    if not isinstance(assets, list) or len(assets) != len(SOURCE_ASSETS):
        raise SourceLocatorError("source locator asset set is incomplete")
    names: set[str] = set()
    for asset in assets:
        if not isinstance(asset, dict) or set(asset) != {"name", "url", "sha256", "size_bytes"}:
            raise SourceLocatorError("source locator asset identity is incomplete")
        name = asset["name"]
        size = asset["size_bytes"]
        if not isinstance(name, str) or name not in SOURCE_ASSETS or name in names:
            raise SourceLocatorError("source locator names are not exact")
        names.add(name)
        limit = 4 * 1024**3 if name == "gene_NBK1116.tar.gz" else 64 * 1024**2
        if not isinstance(size, int) or isinstance(size, bool) or not 0 < size <= limit:
            raise SourceLocatorError("source locator size exceeds its reviewed bound")
        if not isinstance(asset["sha256"], str) or not _SHA256.fullmatch(asset["sha256"]):
            raise SourceLocatorError("source locator digest is invalid")
        parsed = urlsplit(str(asset["url"]))
        match = re.fullmatch(
            r"/repos/([^/]+/[^/]+)/releases/assets/([1-9][0-9]{0,18})",
            unquote(parsed.path),
        )
        if (
            parsed.scheme != "https"
            or parsed.hostname != "api.github.com"
            or parsed.port is not None
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or match is None
            or match.group(1) not in allowed
        ):
            raise SourceLocatorError("source locator URL is not allowlisted")
    if names != SOURCE_ASSETS:
        raise SourceLocatorError("source locator asset set is incomplete")
    return locator


async def fetch_source_assets(
    raw: bytes,
    *,
    allowed_repositories: Iterable[str],
    destination: Path,
    token: str,
) -> dict[str, object]:
    locator = load_source_locator(raw, allowed_repositories=allowed_repositories)
    if destination.is_symlink() or not destination.is_dir() or any(destination.iterdir()):
        raise SourceLocatorError("source destination must be a fresh real directory")
    headers = {"Accept": "application/octet-stream", "Authorization": f"Bearer {token}"}
    assets = locator["assets"]
    assert isinstance(assets, list)
    try:
        for asset in assets:
            assert isinstance(asset, dict)
            name = str(asset["name"])
            deadline = 30 * 60.0 if name == "gene_NBK1116.tar.gz" else 2 * 60.0
            async with httpx.AsyncClient(
                headers=headers,
                timeout=STREAM_TIMEOUT,
                follow_redirects=True,
                max_redirects=5,
                event_hooks={"request": [make_url_guard(_REDIRECT_HOSTS)]},
            ) as client:
                await stream_to_file(
                    client,
                    str(asset["url"]),
                    destination / name,
                    max_bytes=int(asset["size_bytes"]),
                    deadline_seconds=deadline,
                )
            target = destination / name
            digest = hashlib.sha256()
            with target.open("rb") as stream:
                while chunk := stream.read(1 << 20):
                    digest.update(chunk)
            if (
                target.stat().st_size != asset["size_bytes"]
                or digest.hexdigest() != asset["sha256"]
            ):
                raise SourceLocatorError("downloaded source bytes do not match locator")
            target.chmod(0o400)
    except BaseException:
        for name in SOURCE_ASSETS:
            target = destination / name
            if target.exists():
                target.chmod(0o600)
                target.unlink()
        raise
    return locator


__all__ = ["SOURCE_ASSETS", "SourceLocatorError", "fetch_source_assets", "load_source_locator"]
