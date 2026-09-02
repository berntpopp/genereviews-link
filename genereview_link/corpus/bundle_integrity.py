"""Fail-closed integrity checks for a locally built, data-only corpus bundle.

A corpus bundle is three files -- ``corpus.dump``, ``manifest.json`` and ``SHA256SUMS``.
This module proves that set is internally consistent (every digest in ``SHA256SUMS``
matches the bytes on disk) through bounded, symlink-refusing descriptors, and then hands
the manifest to :mod:`genereview_link.corpus.bundle_verifier` for the semantic checks.

Bundles are built on the maintainer's workstation and published as ordinary immutable
GitHub release assets; there is no sealed handoff object, no locator and no second
signature.  What replaces them is this check plus the digests committed in
``container-release.json``, which every consumer verifies before opening a byte.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
from pathlib import Path

from genereview_link.strict_json import StrictJsonError, load_strict_json

MAX_METADATA_BYTES = 1 << 20
CHUNK_BYTES = 1 << 20
BUNDLE_FILES = frozenset({"corpus.dump", "manifest.json", "SHA256SUMS"})

__all__ = [
    "BUNDLE_FILES",
    "BundleIntegrityError",
    "verify_data_only_bundle",
]


class BundleIntegrityError(ValueError):
    """The bundle is missing files, unsafe to read, or internally inconsistent."""


class _FDGuard:
    def __init__(self, *fds: int) -> None:
        self.fds = list(fds)

    def close(self) -> None:
        while self.fds:
            os.close(self.fds.pop())

    def __del__(self) -> None:
        self.close()


def _regular_file(path: Path) -> os.stat_result:
    try:
        info = path.lstat()
    except FileNotFoundError as error:
        raise BundleIntegrityError(f"missing required file: {path.name}") from error
    if not stat.S_ISREG(info.st_mode):
        raise BundleIntegrityError(f"{path.name} must be a regular file")
    return info


def _open_regular(path: Path, *, parent_fd: int | None = None) -> tuple[int, os.stat_result]:
    owns_parent = parent_fd is None
    try:
        if owns_parent:
            parent_fd = _open_directory(path.parent)
        assert parent_fd is not None
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent_fd)
    except OSError as error:
        if owns_parent and parent_fd is not None:
            os.close(parent_fd)
        raise BundleIntegrityError(f"unsafe or missing required file: {path.name}") from error
    if owns_parent:
        os.close(parent_fd)
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode):
        os.close(fd)
        raise BundleIntegrityError(f"{path.name} must be a regular file")
    return fd, info


def _open_directory(path: Path) -> int:
    """Open every path component with openat/O_NOFOLLOW and return the final fd."""
    absolute = path.absolute()
    current = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in absolute.parts[1:]:
            next_fd = os.open(
                part,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=current,
            )
            os.close(current)
            current = next_fd
        return current
    except BaseException:
        os.close(current)
        raise


def _read_capped(
    path: Path, *, limit: int = MAX_METADATA_BYTES, parent_fd: int | None = None
) -> bytes:
    fd, info = _open_regular(path, parent_fd=parent_fd)
    try:
        if info.st_size > limit:
            raise BundleIntegrityError(f"{path.name} exceeds {limit} byte limit")
        chunks: list[bytes] = []
        remaining = limit + 1
        while remaining:
            chunk = os.read(fd, remaining)
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
    finally:
        os.close(fd)
    value = b"".join(chunks)
    if len(value) > limit:
        raise BundleIntegrityError(f"{path.name} exceeds {limit} byte limit")
    return value


def _sha256(path: Path, *, parent_fd: int | None = None) -> tuple[str, int]:
    digest, size, _ = _sha256_facts(path, parent_fd=parent_fd)
    return digest, size


def _sha256_facts(path: Path, *, parent_fd: int | None = None) -> tuple[str, int, int]:
    digest = hashlib.sha256()
    size = 0
    fd, info = _open_regular(path, parent_fd=parent_fd)
    try:
        while chunk := os.read(fd, CHUNK_BYTES):
            digest.update(chunk)
            size += len(chunk)
    finally:
        os.close(fd)
    return digest.hexdigest(), size, stat.S_IMODE(info.st_mode)


def _canonical_json(value: object) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n"
    ).encode()


def _load_json(path: Path, *, parent_fd: int | None = None) -> dict[str, object]:
    try:
        value = load_strict_json(
            _read_capped(path, parent_fd=parent_fd),
            max_bytes=MAX_METADATA_BYTES,
        )
    except StrictJsonError as error:
        raise BundleIntegrityError(f"invalid JSON: {path.name}") from error
    if not isinstance(value, dict):
        raise BundleIntegrityError(f"{path.name} must contain a JSON object")
    return value


def _parse_sums(path: Path, *, parent_fd: int | None = None) -> dict[str, str]:
    try:
        lines = _read_capped(path, parent_fd=parent_fd).decode("ascii").splitlines()
    except UnicodeDecodeError as error:
        raise BundleIntegrityError("SHA256SUMS must be ASCII") from error
    sums: dict[str, str] = {}
    for line in lines:
        parts = line.split("  ")
        if (
            len(parts) != 2
            or len(parts[0]) != 64
            or any(ch not in "0123456789abcdef" for ch in parts[0])
        ):
            raise BundleIntegrityError("SHA256SUMS contains an invalid checksum line")
        digest, name = parts
        if name not in {"corpus.dump", "manifest.json"} or name in sums:
            raise BundleIntegrityError(
                "SHA256SUMS must name each required bundle file exactly once"
            )
        sums[name] = digest
    if set(sums) != {"corpus.dump", "manifest.json"}:
        raise BundleIntegrityError("SHA256SUMS is incomplete")
    return sums


def _verify_source(source: Path, *, directory_fd: int | None = None) -> list[dict[str, object]]:
    """Prove the exact three-file bundle against its own ``SHA256SUMS``."""
    if directory_fd is None:
        if not source.is_dir() or source.is_symlink():
            raise BundleIntegrityError("source must be a real directory")
        names = {path.name for path in source.iterdir()}
    else:
        names = set(os.listdir(directory_fd))
    if names != BUNDLE_FILES:
        raise BundleIntegrityError("source must contain exactly the required regular files")
    checksums = _parse_sums(source / "SHA256SUMS", parent_fd=directory_fd)
    files: list[dict[str, object]] = []
    for name in sorted(BUNDLE_FILES):
        path = source / name
        digest, size, mode = _sha256_facts(path, parent_fd=directory_fd)
        if name in checksums and checksums[name] != digest:
            raise BundleIntegrityError(f"checksum mismatch for {name}")
        files.append({"name": name, "sha256": digest, "size": size, "mode": mode})
    return files


def verify_data_only_bundle(bundle: Path, *, directory_fd: int | None = None) -> dict[str, object]:
    """Validate a locally built bundle before restore or publication."""
    from genereview_link.corpus.bundle_verifier import verify_data_only_bundle_impl

    return verify_data_only_bundle_impl(bundle, directory_fd=directory_fd)
