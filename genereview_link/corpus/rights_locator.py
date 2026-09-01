"""Stdlib-only validation for small, durable rights-evidence locators."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable
from contextlib import suppress
from pathlib import Path
from time import monotonic
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

MAX_SECRET_BYTES = 48 * 1024
MAX_RIGHTS_ASSET_BYTES = 1 << 20
RIGHTS_TRANSFER_DEADLINE_SECONDS = 2 * 60.0
RIGHTS_ASSET_NAMES = frozenset(
    {"rights-record.json", "rights-evidence.json", "terms-snapshot.html"}
)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_DOWNLOAD_HOSTS = frozenset(
    {
        "github.com",
        "release-assets.githubusercontent.com",
        "objects.githubusercontent.com",
        "github-releases.githubusercontent.com",
    }
)


class RightsLocatorError(ValueError):
    """The protected secret does not identify exact durable evidence bytes."""


def load_rights_locator(raw: bytes, *, allowed_repositories: Iterable[str]) -> dict[str, object]:
    """Validate a <48 KiB locator for immutable GitHub release assets."""
    if not 0 < len(raw) <= MAX_SECRET_BYTES:
        raise RightsLocatorError("rights locator exceeds the GitHub secret size bound")
    try:
        locator = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise RightsLocatorError("rights locator is not valid JSON") from error
    if not isinstance(locator, dict) or set(locator) != {"format", "assets"}:
        raise RightsLocatorError("rights locator has missing or extra fields")
    if locator["format"] != "genereviews-rights-locator-v1":
        raise RightsLocatorError("rights locator format is not reviewed")
    allowed = set(allowed_repositories)
    if not allowed or any(not _REPOSITORY.fullmatch(repo) for repo in allowed):
        raise RightsLocatorError("rights locator repository allowlist is invalid")
    assets = locator["assets"]
    if not isinstance(assets, list) or len(assets) != len(RIGHTS_ASSET_NAMES):
        raise RightsLocatorError("rights locator asset set is incomplete")
    names: set[str] = set()
    total = 0
    for asset in assets:
        if not isinstance(asset, dict) or set(asset) != {"name", "url", "sha256", "size_bytes"}:
            raise RightsLocatorError("rights locator asset identity is incomplete")
        name = asset["name"]
        url = asset["url"]
        size = asset["size_bytes"]
        if not isinstance(name, str) or name not in RIGHTS_ASSET_NAMES or name in names:
            raise RightsLocatorError("rights locator asset names are not exact")
        names.add(name)
        if (
            not isinstance(size, int)
            or isinstance(size, bool)
            or not 0 < size <= MAX_RIGHTS_ASSET_BYTES
        ):
            raise RightsLocatorError("rights locator asset size is outside the reviewed bound")
        total += size
        if not isinstance(url, str):
            raise RightsLocatorError("rights locator asset URL is invalid")
        parsed = urlsplit(url)
        path = unquote(parsed.path)
        match = re.fullmatch(r"/repos/([^/]+/[^/]+)/releases/assets/([1-9][0-9]{0,18})", path)
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
            raise RightsLocatorError("rights locator URL is not an allowlisted release asset")
        if not isinstance(asset["sha256"], str) or not _SHA256.fullmatch(asset["sha256"]):
            raise RightsLocatorError("rights locator asset digest is invalid")
    if names != RIGHTS_ASSET_NAMES or total > 3 * MAX_RIGHTS_ASSET_BYTES:
        raise RightsLocatorError("rights locator does not bind the complete bounded asset set")
    return locator


class _RightsRedirects(HTTPRedirectHandler):
    def redirect_request(  # type: ignore[no-untyped-def]
        self, request, fp, code, msg, headers, newurl
    ):
        del fp, code, msg, headers
        parsed = urlsplit(newurl)
        if (
            parsed.scheme != "https"
            or parsed.hostname not in _DOWNLOAD_HOSTS
            or parsed.port is not None
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise RightsLocatorError("rights asset redirect left the reviewed host allowlist")
        return Request(  # noqa: S310 - parsed HTTPS URL and exact host allowlist above
            newurl, headers={"Accept": "application/octet-stream"}
        )


def fetch_rights_assets(
    raw: bytes,
    *,
    allowed_repositories: Iterable[str],
    destination: Path,
    token: str,
) -> dict[str, object]:
    """Fetch exact locator bytes with bounded redirects, sizes, and SHA-256 checks."""
    locator = load_rights_locator(raw, allowed_repositories=allowed_repositories)
    if destination.is_symlink() or not destination.is_dir() or any(destination.iterdir()):
        raise RightsLocatorError("rights destination must be a fresh real directory")
    opener = build_opener(_RightsRedirects())
    assets = locator["assets"]
    assert isinstance(assets, list)
    written: list[Path] = []
    deadline = monotonic() + RIGHTS_TRANSFER_DEADLINE_SECONDS
    try:
        for asset in assets:
            assert isinstance(asset, dict)
            request = Request(  # noqa: S310 - URL was validated by load_rights_locator
                str(asset["url"]),
                headers={
                    "Accept": "application/octet-stream",
                    "Authorization": f"Bearer {token}",
                },
            )
            digest = hashlib.sha256()
            target = destination / str(asset["name"])
            written.append(target)
            remaining = int(asset["size_bytes"])
            try:
                with opener.open(request, timeout=60) as response, target.open("xb") as output:
                    while True:
                        if monotonic() >= deadline:
                            raise RightsLocatorError("rights asset exceeded its monotonic deadline")
                        chunk = response.read(min(64 * 1024, remaining + 1))
                        if not chunk:
                            break
                        remaining -= len(chunk)
                        if remaining < 0:
                            raise RightsLocatorError("rights asset exceeds its declared size")
                        digest.update(chunk)
                        output.write(chunk)
                    if monotonic() >= deadline:
                        raise RightsLocatorError("rights asset exceeded its monotonic deadline")
            except (HTTPError, URLError, TimeoutError, OSError) as error:
                raise RightsLocatorError("rights asset could not be fetched safely") from error
            if remaining != 0 or digest.hexdigest() != asset["sha256"]:
                raise RightsLocatorError("rights asset bytes do not match locator identity")
            target.chmod(0o400)
    except BaseException:
        for target in written:
            with suppress(FileNotFoundError):
                target.chmod(0o600)
            target.unlink(missing_ok=True)
        raise
    return locator


__all__ = [
    "MAX_SECRET_BYTES",
    "RIGHTS_ASSET_NAMES",
    "RightsLocatorError",
    "fetch_rights_assets",
    "load_rights_locator",
]
