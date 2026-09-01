"""Stdlib-only descriptor-bound file admission and exact HTTPS retrieval."""

from __future__ import annotations

import ctypes
import hashlib
import os
import secrets
import stat
from contextlib import suppress
from pathlib import Path
from time import monotonic
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


class DisallowedURLError(Exception):
    """Outbound request or redirect targets a non-allowlisted URL."""


class ResponseTooLargeError(Exception):
    """A streamed download exceeded its byte ceiling."""


class DownloadDeadlineError(Exception):
    """A download exceeded its monotonic end-to-end deadline."""


class DownloadAdmissionError(Exception):
    """Verified bytes could not be atomically admitted at the requested path."""


def _rename_noreplace(parent_fd: int, source: str, destination: str) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    result = libc.renameat2(
        parent_fd,
        ctypes.c_char_p(os.fsencode(source)),
        parent_fd,
        ctypes.c_char_p(os.fsencode(destination)),
        1,  # RENAME_NOREPLACE
    )
    if result != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))


class DownloadOwnership:
    """Keep the created inode and its parent directory pinned until admission."""

    __slots__ = ("_admitted", "_file_fd", "_parent_fd", "_parent_path", "name")

    def __init__(
        self,
        *,
        parent_fd: int,
        file_fd: int,
        name: str,
        parent_path: Path | None = None,
        admitted: bool = True,
    ) -> None:
        if not name or Path(name).name != name:
            raise ValueError("download ownership name must be one path component")
        self._parent_fd: int | None = parent_fd
        self._file_fd: int | None = file_fd
        self._parent_path = parent_path
        self._admitted = admitted
        self.name = name

    @classmethod
    def anonymous(cls, destination: Path) -> DownloadOwnership:
        """Create an unnamed regular inode beneath a pinned destination directory."""
        parent_fd = os.open(destination.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            return cls._anonymous_at(
                parent_fd=parent_fd,
                parent_path=destination.parent,
                name=destination.name,
            )
        finally:
            os.close(parent_fd)

    @classmethod
    def _anonymous_at(cls, *, parent_fd: int, parent_path: Path, name: str) -> DownloadOwnership:
        owned_parent = os.dup(parent_fd)
        try:
            try:
                os.stat(name, dir_fd=owned_parent, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise FileExistsError(parent_path / name)
            file_fd = os.open(".", os.O_RDWR | os.O_TMPFILE, 0o600, dir_fd=owned_parent)
        except BaseException:
            os.close(owned_parent)
            raise
        return cls(
            parent_fd=owned_parent,
            file_fd=file_fd,
            name=name,
            parent_path=parent_path,
            admitted=False,
        )

    @classmethod
    def named_at(cls, *, parent_fd: int, parent_path: Path, name: str) -> DownloadOwnership:
        """Exclusively create a named file while retaining both identity descriptors."""
        owned_parent = os.dup(parent_fd)
        try:
            file_fd = os.open(
                name,
                os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=owned_parent,
            )
        except BaseException:
            os.close(owned_parent)
            raise
        return cls(
            parent_fd=owned_parent,
            file_fd=file_fd,
            name=name,
            parent_path=parent_path,
            admitted=True,
        )

    @property
    def closed(self) -> bool:
        return self._file_fd is None

    def stat(self) -> os.stat_result:
        if self._file_fd is None:
            raise ValueError("download ownership is closed")
        return os.fstat(self._file_fd)

    def fileno(self) -> int:
        if self._file_fd is None:
            raise ValueError("download ownership is closed")
        return self._file_fd

    def write(self, content: bytes) -> None:
        if self._file_fd is None:
            raise ValueError("download ownership is closed")
        view = memoryview(content)
        while view:
            view = view[os.write(self._file_fd, view) :]

    def sync(self) -> None:
        if self._file_fd is None:
            raise ValueError("download ownership is closed")
        os.fsync(self._file_fd)

    def parent_matches_path(self) -> bool:
        if self._parent_fd is None or self._parent_path is None:
            return True
        pinned = os.fstat(self._parent_fd)
        try:
            current = self._parent_path.stat(follow_symlinks=False)
        except OSError:
            return False
        return stat.S_ISDIR(current.st_mode) and (
            current.st_dev,
            current.st_ino,
        ) == (pinned.st_dev, pinned.st_ino)

    def verify_content(self, *, expected_sha256: str, expected_size: int) -> bool:
        """Rehash the pinned object, rejecting mutation while it is being read."""
        if self._file_fd is None or expected_size < 0:
            return False
        before = os.fstat(self._file_fd)
        if not stat.S_ISREG(before.st_mode) or before.st_size != expected_size:
            return False
        digest = hashlib.sha256()
        size = 0
        try:
            os.lseek(self._file_fd, 0, os.SEEK_SET)
            while chunk := os.read(self._file_fd, 1 << 20):
                size += len(chunk)
                if size > expected_size:
                    return False
                digest.update(chunk)
        except OSError:
            return False
        after = os.fstat(self._file_fd)
        stable = (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        ) == (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        )
        return stable and size == expected_size and digest.hexdigest() == expected_sha256

    def admit_exact(self, *, expected_sha256: str, expected_size: int, mode: int = 0o600) -> None:
        """Atomically name the pinned exact bytes relative to the pinned parent."""
        if self._file_fd is None or self._parent_fd is None:
            raise DownloadAdmissionError("download ownership is closed")
        if not self.verify_content(expected_sha256=expected_sha256, expected_size=expected_size):
            raise DownloadAdmissionError("pinned download bytes failed final digest admission")
        if not self.parent_matches_path():
            raise DownloadAdmissionError("download destination parent identity changed")
        os.fchmod(self._file_fd, mode)
        if not self._admitted:
            libc = ctypes.CDLL(None, use_errno=True)
            result = libc.linkat(
                self._file_fd,
                ctypes.c_char_p(b""),
                self._parent_fd,
                ctypes.c_char_p(os.fsencode(self.name)),
                0x1000,  # AT_EMPTY_PATH: link the exact open O_TMPFILE inode.
            )
            if result != 0:
                error = ctypes.get_errno()
                raise DownloadAdmissionError("atomic descriptor admission failed") from OSError(
                    error, os.strerror(error)
                )
            self._admitted = True
            os.fsync(self._parent_fd)
        if not self.matches_path() or not self.verify_content(
            expected_sha256=expected_sha256, expected_size=expected_size
        ):
            self.unlink_if_owned()
            raise DownloadAdmissionError("admitted download identity changed")
        if not self.parent_matches_path():
            self.unlink_if_owned()
            raise DownloadAdmissionError(
                "download destination parent identity changed after admission"
            )

    def matches_path(self) -> bool:
        if self._parent_fd is None or not self._admitted:
            return False
        owned = self.stat()
        try:
            current = os.stat(self.name, dir_fd=self._parent_fd, follow_symlinks=False)
        except OSError:
            return False
        return (
            stat.S_ISREG(owned.st_mode)
            and stat.S_ISREG(current.st_mode)
            and (current.st_dev, current.st_ino) == (owned.st_dev, owned.st_ino)
        )

    def chmod(self, mode: int) -> None:
        if self._file_fd is None:
            raise ValueError("download ownership is closed")
        os.fchmod(self._file_fd, mode)

    def unlink_if_owned(self) -> bool:
        if self._parent_fd is None or self._file_fd is None or not self._admitted:
            return False
        quarantine = f".genereviews-unlink-{secrets.token_hex(16)}"
        try:
            _rename_noreplace(self._parent_fd, self.name, quarantine)
        except FileNotFoundError:
            return False
        owned = self.stat()
        current = os.stat(quarantine, dir_fd=self._parent_fd, follow_symlinks=False)
        if not stat.S_ISREG(current.st_mode) or (current.st_dev, current.st_ino) != (
            owned.st_dev,
            owned.st_ino,
        ):
            with suppress(FileExistsError):
                _rename_noreplace(self._parent_fd, quarantine, self.name)
            return False
        os.unlink(quarantine, dir_fd=self._parent_fd)
        self._admitted = False
        os.fsync(self._parent_fd)
        return True

    def close(self) -> None:
        file_fd, parent_fd = self._file_fd, self._parent_fd
        self._file_fd = None
        self._parent_fd = None
        try:
            if file_fd is not None:
                os.close(file_fd)
        finally:
            if parent_fd is not None:
                os.close(parent_fd)


class PinnedDownloadDirectory:
    """One fresh owned destination directory pinned across every download."""

    __slots__ = ("_directory_fd", "path")

    def __init__(self, *, directory_fd: int, path: Path) -> None:
        self._directory_fd: int | None = directory_fd
        self.path = path

    @classmethod
    def open_fresh(cls, path: Path) -> PinnedDownloadDirectory:
        try:
            directory_fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        except OSError as error:
            raise DownloadAdmissionError(
                "download destination must be a pre-created fresh real directory"
            ) from error
        pinned = cls(directory_fd=directory_fd, path=path)
        try:
            info = os.fstat(directory_fd)
            if (
                not stat.S_ISDIR(info.st_mode)
                or info.st_uid != os.geteuid()
                or os.listdir(directory_fd)
                or not pinned.matches_path()
            ):
                raise DownloadAdmissionError(
                    "download destination must be a fresh owned real directory"
                )
            return pinned
        except BaseException:
            pinned.close()
            raise

    def fileno(self) -> int:
        if self._directory_fd is None:
            raise DownloadAdmissionError("download destination directory is closed")
        return self._directory_fd

    def anonymous(self, name: str) -> DownloadOwnership:
        return DownloadOwnership._anonymous_at(
            parent_fd=self.fileno(), parent_path=self.path, name=name
        )

    def names(self) -> frozenset[str]:
        return frozenset(os.listdir(self.fileno()))

    def matches_path(self) -> bool:
        if self._directory_fd is None:
            return False
        pinned = os.fstat(self._directory_fd)
        try:
            current = self.path.stat(follow_symlinks=False)
        except OSError:
            return False
        return stat.S_ISDIR(current.st_mode) and (
            current.st_dev,
            current.st_ino,
        ) == (pinned.st_dev, pinned.st_ino)

    def close(self) -> None:
        directory_fd = self._directory_fd
        self._directory_fd = None
        if directory_fd is not None:
            os.close(directory_fd)


class _ExactRedirects(HTTPRedirectHandler):
    def __init__(self, *, allowed_hosts: frozenset[str], headers: dict[str, str]) -> None:
        super().__init__()
        self._allowed_hosts = allowed_hosts
        self._headers = headers

    def redirect_request(  # type: ignore[no-untyped-def]
        self, request, fp, code, msg, headers, newurl
    ):
        del request, fp, code, msg, headers
        parsed = urlsplit(newurl)
        if (
            parsed.scheme != "https"
            or parsed.hostname not in self._allowed_hosts
            or parsed.port is not None
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise DisallowedURLError("download redirect left the exact host allowlist")
        redirect_headers = {"Accept": self._headers.get("Accept", "application/octet-stream")}
        return Request(newurl, headers=redirect_headers)  # noqa: S310 - exact HTTPS host above


def _reject_expired_deadline(deadline_at: float) -> None:
    if monotonic() >= deadline_at:
        raise DownloadDeadlineError("download exceeded end-to-end deadline")


def download_exact_https(
    url: str,
    destination: Path,
    *,
    allowed_initial_hosts: frozenset[str],
    allowed_redirect_hosts: frozenset[str],
    expected_sha256: str,
    expected_size: int,
    max_bytes: int,
    authorization: str = "",
    deadline_seconds: float = 20 * 60.0,
) -> None:
    """Synchronously fetch one exact HTTPS object through bounded, guarded hops."""
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname not in allowed_initial_hosts
        or parsed.port is not None
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise DisallowedURLError("initial download URL is not exactly allowlisted")
    if not 0 <= expected_size <= max_bytes:
        raise ResponseTooLargeError("expected download size exceeds its byte ceiling")
    headers = {"Accept": "application/octet-stream"}
    if authorization:
        headers["Authorization"] = authorization
    opener = build_opener(_ExactRedirects(allowed_hosts=allowed_redirect_hosts, headers=headers))
    ownership = DownloadOwnership.anonymous(destination)
    remaining = expected_size
    digest = hashlib.sha256()
    deadline = monotonic() + deadline_seconds
    try:
        request = Request(url, headers=headers)  # noqa: S310 - exact HTTPS host above
        with opener.open(request, timeout=min(60.0, deadline_seconds)) as response:
            while True:
                _reject_expired_deadline(deadline)
                chunk = response.read(min(1 << 20, remaining + 1))
                if not chunk:
                    break
                remaining -= len(chunk)
                if remaining < 0:
                    raise ResponseTooLargeError("download exceeded its exact declared size")
                digest.update(chunk)
                ownership.write(chunk)
        _reject_expired_deadline(deadline)
        if remaining or digest.hexdigest() != expected_sha256:
            raise DownloadAdmissionError("download bytes do not match exact identity")
        ownership.sync()
        ownership.admit_exact(
            expected_sha256=expected_sha256,
            expected_size=expected_size,
            mode=0o400,
        )
    except BaseException:
        ownership.unlink_if_owned()
        raise
    finally:
        ownership.close()


__all__ = [
    "DisallowedURLError",
    "DownloadAdmissionError",
    "DownloadDeadlineError",
    "DownloadOwnership",
    "PinnedDownloadDirectory",
    "ResponseTooLargeError",
    "download_exact_https",
]
