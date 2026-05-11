"""Resolve and download GitHub Release bundles."""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

import httpx

GITHUB_API = "https://api.github.com"


async def resolve_latest(repo: str) -> str:
    """Return the asset URL for the latest 'corpus-*' release bundle."""
    async with httpx.AsyncClient(timeout=30.0) as c:
        r = await c.get(f"{GITHUB_API}/repos/{repo}/releases/latest")
        r.raise_for_status()
        for asset in r.json().get("assets", []):
            if asset["name"].endswith(".tar.gz") and asset["name"].startswith("genereview-corpus-"):
                return str(asset["browser_download_url"])
    raise RuntimeError("no corpus bundle found in latest release")


async def fetch_sibling_sha256(url: str) -> str:
    """Fetch <url>.sha256 sibling file and return the hex digest."""
    async with httpx.AsyncClient(timeout=30.0) as c:
        r = await c.get(f"{url}.sha256")
        r.raise_for_status()
        return r.text.strip().split()[0]


async def download_with_integrity(url: str, dest: Path, *, expected_sha256: str) -> None:
    """Stream-download *url* to *dest*, verifying sha256."""
    sha = hashlib.sha256()
    dest.parent.mkdir(parents=True, exist_ok=True)
    # timeout=None is intentional: large bundles take unbounded time.
    async with (
        httpx.AsyncClient(timeout=None) as c,  # noqa: S113
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
    cmd = ["pg_restore", "-d", database_url]
    if jobs:
        cmd += ["-j", str(jobs)]
    cmd.append(str(dump_path))
    subprocess.run(cmd, check=True)  # noqa: S603
