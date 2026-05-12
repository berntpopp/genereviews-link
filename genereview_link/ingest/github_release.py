"""Resolve and download GitHub Release bundles."""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

import httpx

GITHUB_API = "https://api.github.com"


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
    async with httpx.AsyncClient(timeout=30.0) as c:
        r = await c.get(f"{GITHUB_API}/repos/{repo}/releases/latest")
        r.raise_for_status()
        selected = _select_bundle_asset(r.json().get("assets", []))
        if selected:
            return selected
    raise RuntimeError("no corpus bundle found in latest release")


async def fetch_sibling_sha256(url: str) -> str:
    """Fetch <url>.sha256 sibling file and return the hex digest."""
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as c:
        r = await c.get(f"{url}.sha256")
        r.raise_for_status()
        return r.text.strip().split()[0]


async def download_with_integrity(url: str, dest: Path, *, expected_sha256: str) -> None:
    """Stream-download *url* to *dest*, verifying sha256."""
    sha = hashlib.sha256()
    dest.parent.mkdir(parents=True, exist_ok=True)
    # timeout=None is intentional: large bundles take unbounded time.
    async with (
        httpx.AsyncClient(timeout=None, follow_redirects=True) as c,  # noqa: S113
        c.stream("GET", url) as r,
    ):
        r.raise_for_status()
        with dest.open("wb") as fh:
            async for chunk in r.aiter_bytes(1 << 20):
                sha.update(chunk)
                fh.write(chunk)
    if sha.hexdigest() != expected_sha256:
        dest.unlink(missing_ok=True)
        raise RuntimeError(
            f"bundle sha256 mismatch: expected {expected_sha256}, got {sha.hexdigest()}"
        )


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
