"""Resolve and download GitHub Release bundles."""

from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path

import httpx

from genereview_link.config import settings
from genereview_link.download_guard import (
    STREAM_TIMEOUT,
    build_host_allowlist,
    make_url_guard,
    read_capped,
    stream_to_file,
)

logger = logging.getLogger(__name__)

GITHUB_API = "https://api.github.com"

# Release-resolve client only ever talks to the GitHub REST API.
_RESOLVE_HOSTS = build_host_allowlist(GITHUB_API)

# Download client: a GitHub-Release asset 302s from github.com to the asset CDN
# (release-assets.githubusercontent.com, verified live). The allowlist MUST
# include the CDN or the bundle bootstrap breaks on the redirect; the extra
# hosts are defensive against GitHub rotating the CDN name.
_DEFENSIVE_DOWNLOAD_HOSTS = frozenset(
    {
        "github.com",
        "release-assets.githubusercontent.com",
        "objects.githubusercontent.com",
        "github-releases.githubusercontent.com",
    }
)

# Fail-closed download ceilings.
MAX_BUNDLE_BYTES = 2 * 1024**3  # 2 GiB
MAX_SHA256_BYTES = 1 * 1024 * 1024  # 1 MiB

# Committed, in-repo authenticity anchors keyed by bundle asset filename. Empty
# by default: operators pin a release out-of-band via EXPECTED_BUNDLE_SHA256, or
# add entries here in a reviewed commit. This is authenticity, NOT integrity --
# it must never be sourced from the download host's own .sha256 sibling.
BUNDLE_DIGEST_ANCHORS: dict[str, str] = {}


def _download_allowlist(url: str) -> frozenset[str]:
    """Allow the (operator-configurable) URL host plus the GitHub CDN set."""
    return build_host_allowlist(url) | _DEFENSIVE_DOWNLOAD_HOSTS


def _download_client(url: str) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=STREAM_TIMEOUT,
        follow_redirects=True,
        max_redirects=5,
        event_hooks={"request": [make_url_guard(_download_allowlist(url))]},
    )


def committed_bundle_digest(url: str) -> str | None:
    """Return the independently-committed SHA-256 anchor for *url*, or None.

    Precedence: the operator-set ``EXPECTED_BUNDLE_SHA256`` config, then an
    in-repo ``BUNDLE_DIGEST_ANCHORS`` entry keyed by asset filename.
    """
    configured = settings.EXPECTED_BUNDLE_SHA256.strip()
    if configured:
        return configured.lower()
    filename = url.rsplit("/", 1)[-1]
    anchor = BUNDLE_DIGEST_ANCHORS.get(filename)
    return anchor.lower() if anchor else None


def _select_bundle_asset(assets: list[dict[str, object]]) -> str | None:
    for asset in assets:
        name = str(asset.get("name", ""))
        if (
            name.startswith("genereview-corpus-")
            and name.endswith(".tar.gz")
            and not name.endswith(".sha256")
        ):
            return str(asset["browser_download_url"])
    return None


async def resolve_latest(repo: str) -> str:
    """Return the asset URL for the latest 'corpus-*' release bundle."""
    async with httpx.AsyncClient(
        timeout=STREAM_TIMEOUT,
        follow_redirects=False,
        event_hooks={"request": [make_url_guard(_RESOLVE_HOSTS)]},
    ) as c:
        r = await c.get(f"{GITHUB_API}/repos/{repo}/releases/latest")
        r.raise_for_status()
        selected = _select_bundle_asset(r.json().get("assets", []))
        if selected:
            return selected
    raise RuntimeError("no corpus bundle found in latest release")


async def fetch_sibling_sha256(url: str) -> str:
    """Fetch <url>.sha256 sibling file and return the hex digest (integrity)."""
    async with _download_client(url) as c:
        body = await read_capped(c, f"{url}.sha256", max_bytes=MAX_SHA256_BYTES)
    return body.decode("utf-8", "replace").strip().split()[0]


async def download_with_integrity(url: str, dest: Path, *, expected_sha256: str) -> None:
    """Stream-download *url* to *dest* with redirect allowlisting, a fail-closed
    byte cap, and dual verification: an independently-committed authenticity
    anchor and the transport-level sibling sha256. Writes atomically."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.parent / (dest.name + ".part")
    async with _download_client(url) as c:
        digest = await stream_to_file(c, url, part, max_bytes=MAX_BUNDLE_BYTES)

    # Authenticity (independent of the possibly-redirected host): the committed
    # anchor is the authority. A same-host .sha256 alone is NOT authenticity.
    anchor = committed_bundle_digest(url)
    if anchor is not None and digest != anchor:
        part.unlink(missing_ok=True)
        raise RuntimeError("bundle authenticity check failed: committed digest mismatch")

    # Integrity (transport check against the sibling .sha256).
    if digest != expected_sha256:
        part.unlink(missing_ok=True)
        raise RuntimeError(f"bundle sha256 mismatch: expected {expected_sha256}, got {digest}")

    if anchor is None:
        logger.warning(
            "bundle authenticity not anchored for %s (no committed digest); verified "
            "transport integrity only -- set EXPECTED_BUNDLE_SHA256 to anchor authenticity",
            dest.name,
        )
    os.replace(part, dest)


async def pg_restore(dump_path: Path, *, database_url: str, jobs: int | None = None) -> None:
    cmd = ["pg_restore", "--clean", "--if-exists", "--no-owner", "-d", database_url]
    if jobs:
        cmd += ["-j", str(jobs)]
    cmd.append(str(dump_path))
    result = subprocess.run(cmd, check=False, capture_output=True, text=True)  # noqa: S603
    if isinstance(result.returncode, int) and result.returncode != 0:
        raise RuntimeError(
            f"pg_restore failed with exit {result.returncode}: {result.stderr.strip()}"
        )
