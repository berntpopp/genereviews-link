"""Fetch an exact sealed handoff from immutable, digest-addressed release assets."""

from __future__ import annotations

import hashlib
import os
import re
import stat
from collections.abc import Iterable
from contextlib import suppress
from pathlib import Path
from time import monotonic
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from genereview_link.strict_json import StrictJsonError, load_strict_json

MAX_LOCATOR_BYTES = 48 * 1024
MAX_METADATA_BYTES = 64 * 1024**2
MAX_ARCHIVE_BYTES = 4 * 1024**3
HANDOFF_TRANSFER_DEADLINE_SECONDS = 45 * 60.0
_FIXED_ASSETS = frozenset({"corpus.dump", "manifest.json", "SHA256SUMS", "seal-manifest.json"})
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_GIT_SHA = re.compile(r"^[0-9a-f]{40}$")
_REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_WHEEL = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._]*-[A-Za-z0-9][A-Za-z0-9._]*"
    r"(?:-[A-Za-z0-9][A-Za-z0-9._]*)?-[A-Za-z0-9.]+-[A-Za-z0-9.]+-[A-Za-z0-9.]+\.whl$"
)
_DOWNLOAD_HOSTS = frozenset(
    {
        "github.com",
        "release-assets.githubusercontent.com",
        "objects.githubusercontent.com",
        "github-releases.githubusercontent.com",
    }
)


class HandoffLocatorError(ValueError):
    """The protected locator does not identify an exact transferable handoff."""


def load_handoff_locator(
    raw: bytes,
    *,
    allowed_repositories: Iterable[str],
    expected_object_id: str,
    expected_build_revision: str | None = None,
) -> dict[str, object]:
    """Validate a bounded locator for one immutable five-asset sealed object."""
    if not 0 < len(raw) <= MAX_LOCATOR_BYTES:
        raise HandoffLocatorError("handoff locator exceeds the protected-secret bound")
    try:
        locator = load_strict_json(raw, max_bytes=MAX_LOCATOR_BYTES)
    except StrictJsonError as error:
        raise HandoffLocatorError("handoff locator is not valid JSON") from error
    if (
        not isinstance(locator, dict)
        or set(locator) != {"format", "object_id", "build_revision", "assets"}
        or locator["format"] != "genereviews-handoff-locator-v1"
    ):
        raise HandoffLocatorError("handoff locator format is invalid")
    object_id = locator["object_id"]
    revision = locator["build_revision"]
    if not isinstance(object_id, str) or not _SHA256.fullmatch(object_id):
        raise HandoffLocatorError("handoff object id is invalid")
    if object_id != expected_object_id:
        raise HandoffLocatorError("handoff object id does not match the requested object")
    if not isinstance(revision, str) or not _GIT_SHA.fullmatch(revision):
        raise HandoffLocatorError("handoff build revision is invalid")
    if expected_build_revision is not None and revision != expected_build_revision:
        raise HandoffLocatorError("handoff build revision does not match the trusted revision")
    allowed = set(allowed_repositories)
    if not allowed or any(not _REPOSITORY.fullmatch(repo) for repo in allowed):
        raise HandoffLocatorError("handoff repository allowlist is invalid")
    assets = locator["assets"]
    if not isinstance(assets, list) or len(assets) != 5:
        raise HandoffLocatorError("handoff locator asset set is incomplete")
    names: set[str] = set()
    wheels: set[str] = set()
    for asset in assets:
        if not isinstance(asset, dict) or set(asset) != {"name", "url", "sha256", "size_bytes"}:
            raise HandoffLocatorError("handoff locator asset identity is incomplete")
        name = asset["name"]
        if not isinstance(name, str) or name in names:
            raise HandoffLocatorError("handoff locator asset names are not exact")
        names.add(name)
        if _WHEEL.fullmatch(name):
            wheels.add(name)
        elif name not in _FIXED_ASSETS:
            raise HandoffLocatorError("handoff locator asset names are not exact")
        size = asset["size_bytes"]
        limit = MAX_ARCHIVE_BYTES if name == "corpus.dump" else MAX_METADATA_BYTES
        if not isinstance(size, int) or isinstance(size, bool) or not 0 < size <= limit:
            raise HandoffLocatorError("handoff locator asset size exceeds its reviewed bound")
        digest = asset["sha256"]
        if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
            raise HandoffLocatorError("handoff locator asset digest is invalid")
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
            raise HandoffLocatorError("handoff locator URL is not allowlisted")
    if names != _FIXED_ASSETS | wheels or len(wheels) != 1:
        raise HandoffLocatorError("handoff locator asset set is incomplete")
    return locator


class _HandoffRedirects(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        del request, fp, code, msg, headers
        parsed = urlsplit(newurl)
        if (
            parsed.scheme != "https"
            or parsed.hostname not in _DOWNLOAD_HOSTS
            or parsed.port is not None
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise HandoffLocatorError("handoff asset redirect left the reviewed host allowlist")
        return Request(newurl, headers={"Accept": "application/octet-stream"})  # noqa: S310


def fetch_handoff(
    raw: bytes,
    *,
    allowed_repositories: Iterable[str],
    destination_root: Path,
    token: str,
    expected_object_id: str,
    expected_build_revision: str | None = None,
) -> dict[str, object]:
    """Reconstruct, mode-seal, and verify one handoff beneath a fresh owner-only root."""
    from genereview_link.corpus.handoff import HandoffError, verify_handoff

    locator = load_handoff_locator(
        raw,
        allowed_repositories=allowed_repositories,
        expected_object_id=expected_object_id,
        expected_build_revision=expected_build_revision,
    )
    info = destination_root.lstat()
    if (
        destination_root.is_symlink()
        or not stat.S_ISDIR(info.st_mode)
        or stat.S_IMODE(info.st_mode) != 0o700
        or any(destination_root.iterdir())
    ):
        raise HandoffLocatorError("handoff destination must be a fresh owner-only directory")
    target = destination_root / expected_object_id
    target.mkdir(mode=0o700)
    opener = build_opener(_HandoffRedirects())
    assets = locator["assets"]
    assert isinstance(assets, list)
    deadline = monotonic() + HANDOFF_TRANSFER_DEADLINE_SECONDS
    try:
        for asset in assets:
            assert isinstance(asset, dict)
            target_path = target / str(asset["name"])
            request = Request(  # noqa: S310 - exact API URL validated above
                str(asset["url"]),
                headers={"Accept": "application/octet-stream", "Authorization": f"Bearer {token}"},
            )
            remaining = int(asset["size_bytes"])
            digest = hashlib.sha256()
            try:
                with opener.open(request, timeout=60) as response, target_path.open("xb") as output:
                    while True:
                        if monotonic() >= deadline:
                            raise HandoffLocatorError(
                                "handoff asset exceeded its monotonic deadline"
                            )
                        chunk = response.read(min(1 << 20, remaining + 1))
                        if not chunk:
                            break
                        remaining -= len(chunk)
                        if remaining < 0:
                            raise HandoffLocatorError("handoff asset exceeds its declared size")
                        digest.update(chunk)
                        output.write(chunk)
                    if monotonic() >= deadline:
                        raise HandoffLocatorError("handoff asset exceeded its monotonic deadline")
                    output.flush()
                    os.fsync(output.fileno())
            except (HTTPError, URLError, TimeoutError, OSError) as error:
                raise HandoffLocatorError("handoff asset could not be fetched safely") from error
            if remaining or digest.hexdigest() != asset["sha256"]:
                raise HandoffLocatorError("handoff asset bytes do not match locator identity")
            target_path.chmod(0o400)
        target.chmod(0o500)
        directory_fd = os.open(target, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        root_fd = os.open(destination_root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(root_fd)
        finally:
            os.close(root_fd)
        try:
            verify_handoff(destination_root, expected_object_id)
        except HandoffError as error:
            raise HandoffLocatorError("downloaded handoff failed sealed verification") from error
    except BaseException:
        with suppress(FileNotFoundError):
            target.chmod(0o700)
        for child in target.iterdir() if target.exists() else ():
            child.chmod(0o600)
            child.unlink()
        with suppress(FileNotFoundError):
            target.rmdir()
        raise
    return locator


__all__ = ["HandoffLocatorError", "fetch_handoff", "load_handoff_locator"]
