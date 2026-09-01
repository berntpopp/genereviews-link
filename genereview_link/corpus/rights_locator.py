"""Stdlib-only validation for small, durable rights-evidence locators."""

from __future__ import annotations

import hashlib
import os
import re
import stat
from collections.abc import Iterable
from pathlib import Path
from time import monotonic
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from genereview_link.strict_json import StrictJsonError, load_strict_json

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
        locator = load_strict_json(raw, max_bytes=MAX_SECRET_BYTES)
    except StrictJsonError as error:
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


def _open_fresh_destination(destination: Path) -> tuple[int, tuple[int, int]]:
    try:
        path_info = destination.lstat()
        destination_fd = os.open(
            destination,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
        )
    except OSError as error:
        raise RightsLocatorError("rights destination must be a fresh real directory") from error
    try:
        descriptor_info = os.fstat(destination_fd)
        identity = (descriptor_info.st_dev, descriptor_info.st_ino)
        if (
            not stat.S_ISDIR(path_info.st_mode)
            or not stat.S_ISDIR(descriptor_info.st_mode)
            or identity != (path_info.st_dev, path_info.st_ino)
            or os.listdir(destination_fd)
        ):
            raise RightsLocatorError("rights destination must be a fresh real directory")
        return destination_fd, identity
    except BaseException:
        os.close(destination_fd)
        raise


def _admit_rights_asset(
    destination_fd: int,
    asset: dict[str, object],
    created_identity: tuple[int, int],
) -> None:
    name = str(asset["name"])
    expected_size = asset["size_bytes"]
    if type(expected_size) is not int:
        raise RightsLocatorError("rights asset path does not match its admitted identity")
    try:
        descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=destination_fd)
    except OSError as error:
        raise RightsLocatorError(
            "rights asset path does not match its admitted identity"
        ) from error
    try:
        info = os.fstat(descriptor)
        digest = hashlib.sha256()
        size = 0
        while chunk := os.read(descriptor, 64 * 1024):
            size += len(chunk)
            if size > expected_size:
                raise RightsLocatorError("rights asset path does not match its admitted identity")
            digest.update(chunk)
        if (
            not stat.S_ISREG(info.st_mode)
            or (info.st_dev, info.st_ino) != created_identity
            or size != expected_size
            or digest.hexdigest() != asset["sha256"]
        ):
            raise RightsLocatorError("rights asset path does not match its admitted identity")
    except OSError as error:
        raise RightsLocatorError("rights asset path could not be admitted safely") from error
    finally:
        os.close(descriptor)


def fetch_rights_assets(
    raw: bytes,
    *,
    allowed_repositories: Iterable[str],
    destination: Path,
    token: str,
) -> dict[str, object]:
    """Fetch exact locator bytes with bounded redirects, sizes, and SHA-256 checks."""
    locator = load_rights_locator(raw, allowed_repositories=allowed_repositories)
    opener = build_opener(_RightsRedirects())
    assets = locator["assets"]
    assert isinstance(assets, list)
    created: dict[str, tuple[int, int]] = {}
    deadline = monotonic() + RIGHTS_TRANSFER_DEADLINE_SECONDS
    destination_fd, destination_identity = _open_fresh_destination(destination)
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
            name = str(asset["name"])
            remaining = int(asset["size_bytes"])
            try:
                with opener.open(request, timeout=60) as response:
                    output_fd = os.open(
                        name,
                        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                        0o600,
                        dir_fd=destination_fd,
                    )
                    info = os.fstat(output_fd)
                    created[name] = (info.st_dev, info.st_ino)
                    output = os.fdopen(output_fd, "wb")
                    with output:
                        while True:
                            if monotonic() >= deadline:
                                raise RightsLocatorError(
                                    "rights asset exceeded its monotonic deadline"
                                )
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
                        output.flush()
                        os.fsync(output.fileno())
                        os.fchmod(output.fileno(), 0o400)
            except (HTTPError, URLError, TimeoutError, OSError) as error:
                raise RightsLocatorError("rights asset could not be fetched safely") from error
            if remaining != 0 or digest.hexdigest() != asset["sha256"]:
                raise RightsLocatorError("rights asset bytes do not match locator identity")
        for asset in assets:
            assert isinstance(asset, dict)
            _admit_rights_asset(
                destination_fd,
                asset,
                created[str(asset["name"])],
            )
        if set(os.listdir(destination_fd)) != RIGHTS_ASSET_NAMES:
            raise RightsLocatorError("rights destination contains an unadmitted path")
        try:
            current_destination = destination.stat(follow_symlinks=False)
        except OSError as error:
            raise RightsLocatorError("rights destination identity changed") from error
        if (
            not stat.S_ISDIR(current_destination.st_mode)
            or (current_destination.st_dev, current_destination.st_ino) != destination_identity
        ):
            raise RightsLocatorError("rights destination identity changed")
    except BaseException:
        for name, identity in created.items():
            try:
                current = os.stat(name, dir_fd=destination_fd, follow_symlinks=False)
            except FileNotFoundError:
                continue
            if stat.S_ISREG(current.st_mode) and (current.st_dev, current.st_ino) == identity:
                os.unlink(name, dir_fd=destination_fd)
        raise
    finally:
        os.close(destination_fd)
    return locator


__all__ = [
    "MAX_SECRET_BYTES",
    "RIGHTS_ASSET_NAMES",
    "RightsLocatorError",
    "fetch_rights_assets",
    "load_rights_locator",
]
