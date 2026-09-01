"""Fail-closed, local-only sealing for corpus publication handoffs."""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import stat
from ctypes import CDLL, get_errno
from dataclasses import dataclass
from errno import EEXIST, ENOSYS
from pathlib import Path

MAX_METADATA_BYTES = 1 << 20
CHUNK_BYTES = 1 << 20
_SOURCE_FILES = frozenset({"corpus.dump", "manifest.json", "SHA256SUMS"})
_SEAL_MANIFEST_FILE = "seal-manifest.json"
__all__ = [
    "HandoffError",
    "SealedHandoff",
    "prepare_publish_handoff",
    "seal_handoff",
    "verify_data_only_bundle",
    "verify_handoff",
    "verify_rights_record",
]


class HandoffError(ValueError):
    pass


def _valid_wheel_name(name: str) -> bool:
    return bool(
        re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9._]*-[A-Za-z0-9][A-Za-z0-9._]*"
            r"(?:-[A-Za-z0-9][A-Za-z0-9._]*)?-[A-Za-z0-9.]+-[A-Za-z0-9.]+-[A-Za-z0-9.]+\.whl",
            name,
        )
    )


@dataclass(frozen=True)
class SealedHandoff:
    object_id: str
    path: Path
    manifest: Path


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
        raise HandoffError(f"missing required file: {path.name}") from error
    if not stat.S_ISREG(info.st_mode):
        raise HandoffError(f"{path.name} must be a regular file")
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
        raise HandoffError(f"unsafe or missing required file: {path.name}") from error
    if owns_parent:
        os.close(parent_fd)
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode):
        os.close(fd)
        raise HandoffError(f"{path.name} must be a regular file")
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
            raise HandoffError(f"{path.name} exceeds {limit} byte limit")
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
        raise HandoffError(f"{path.name} exceeds {limit} byte limit")
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
        value = json.loads(_read_capped(path, parent_fd=parent_fd))
    except json.JSONDecodeError as error:
        raise HandoffError(f"invalid JSON: {path.name}") from error
    if not isinstance(value, dict):
        raise HandoffError(f"{path.name} must contain a JSON object")
    return value


def _parse_sums(path: Path, *, parent_fd: int | None = None) -> dict[str, str]:
    try:
        lines = _read_capped(path, parent_fd=parent_fd).decode("ascii").splitlines()
    except UnicodeDecodeError as error:
        raise HandoffError("SHA256SUMS must be ASCII") from error
    sums: dict[str, str] = {}
    for line in lines:
        parts = line.split("  ")
        if (
            len(parts) != 2
            or len(parts[0]) != 64
            or any(ch not in "0123456789abcdef" for ch in parts[0])
        ):
            raise HandoffError("SHA256SUMS contains an invalid checksum line")
        digest, name = parts
        if name not in {"corpus.dump", "manifest.json"} or name in sums:
            raise HandoffError("SHA256SUMS must name each required bundle file exactly once")
        sums[name] = digest
    if set(sums) != {"corpus.dump", "manifest.json"}:
        raise HandoffError("SHA256SUMS is incomplete")
    return sums


def _verify_source(
    source: Path, *, allow_extra: bool = False, directory_fd: int | None = None
) -> list[dict[str, object]]:
    if directory_fd is None:
        if not source.is_dir() or source.is_symlink():
            raise HandoffError("source must be a real directory")
        names = {path.name for path in source.iterdir()}
    else:
        names = set(os.listdir(directory_fd))
    if not _SOURCE_FILES.issubset(names) or (not allow_extra and names != _SOURCE_FILES):
        raise HandoffError("source must contain exactly the required regular files")
    if allow_extra:
        extras = names - _SOURCE_FILES
        if any(name != _SEAL_MANIFEST_FILE and not _valid_wheel_name(name) for name in extras):
            raise HandoffError("source contains an unexpected extra file")
        for name in extras:
            _sha256_facts(source / name, parent_fd=directory_fd)
    checksums = _parse_sums(source / "SHA256SUMS", parent_fd=directory_fd)
    files: list[dict[str, object]] = []
    for name in sorted(_SOURCE_FILES):
        path = source / name
        digest, size, mode = _sha256_facts(path, parent_fd=directory_fd)
        if name in checksums and checksums[name] != digest:
            raise HandoffError(f"checksum mismatch for {name}")
        files.append({"name": name, "sha256": digest, "size": size, "mode": mode})
    return files


def verify_data_only_bundle(
    bundle: Path, *, allow_extra: bool = False, directory_fd: int | None = None
) -> dict[str, object]:
    """Validate a fresh local bundle before restore or sealing."""
    from genereview_link.corpus.bundle_verifier import verify_data_only_bundle_impl

    return verify_data_only_bundle_impl(bundle, allow_extra=allow_extra, directory_fd=directory_fd)


def _publisher_wheel(publisher_tool: Path) -> tuple[Path, str, int, int]:
    try:
        info = publisher_tool.lstat()
    except FileNotFoundError as error:
        raise HandoffError("publisher-tool directory is missing") from error
    if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
        raise HandoffError("publisher-tool must be a real directory")
    entries = list(publisher_tool.iterdir())
    wheels = [path for path in entries if path.name.endswith(".whl")]
    if len(wheels) != 1 or any(path.name not in {wheels[0].name, ".gitignore"} for path in entries):
        raise HandoffError("publisher-tool must contain exactly one publisher wheel")
    if any(
        path.name == ".gitignore" and (path.is_symlink() or not path.is_file()) for path in entries
    ):
        raise HandoffError("publisher-tool contains an unsafe ignore marker")
    wheel = wheels[0]
    if not _valid_wheel_name(wheel.name):
        raise HandoffError("publisher wheel filename is not a valid PEP 427 wheel name")
    digest, size, mode = _sha256_facts(wheel)
    return wheel, digest, size, mode


def _assert_handoff_root(handoff_root: Path) -> None:
    if not handoff_root.is_absolute():
        raise HandoffError("handoff root must be an absolute durable path")
    resolved = handoff_root.resolve()
    repository = Path(__file__).resolve().parents[2]
    serving = os.getenv("GENEREVIEW_SERVING_ROOT")
    if resolved == repository or repository in resolved.parents or resolved in repository.parents:
        raise HandoffError("handoff root must not overlap the repository root")
    if serving:
        serving_path = Path(serving).resolve()
        if (
            resolved == serving_path
            or serving_path in resolved.parents
            or resolved in serving_path.parents
        ):
            raise HandoffError("handoff root must be outside serving volumes")
    try:
        info = handoff_root.lstat()
    except FileNotFoundError as error:
        raise HandoffError("handoff root is missing") from error
    if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
        raise HandoffError("handoff root must be a real directory")
    if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise HandoffError("handoff root must be owner-only mode 0700")


def _assert_local_archive(dump: Path, *, parent_fd: int) -> None:
    from genereview_link.db.restore import (
        ArchivePolicyError,
        assert_data_only_archive,
        read_archive_entries,
    )

    archive_fd, _ = _open_regular(dump, parent_fd=parent_fd)
    try:
        assert_data_only_archive(read_archive_entries(dump, file_descriptor=archive_fd))
    except ArchivePolicyError as error:
        raise HandoffError(f"corpus.dump archive policy failed: {error}") from error
    finally:
        os.close(archive_fd)


def _rename_noreplace(source: Path, target: Path, *, parent_fd: int | None = None) -> None:
    """Atomically publish a directory without ever replacing an existing target."""
    libc = CDLL(None, use_errno=True)
    renameat2 = getattr(libc, "renameat2", None)
    if renameat2 is None:
        raise HandoffError("atomic no-replace rename is unavailable on this platform")
    owns_parents = parent_fd is None
    source_parent = _open_directory(source.parent) if owns_parents else parent_fd
    target_parent = _open_directory(target.parent) if owns_parents else parent_fd
    assert source_parent is not None and target_parent is not None
    try:
        result = renameat2(
            source_parent,
            os.fsencode(source.name),
            target_parent,
            os.fsencode(target.name),
            1,  # RENAME_NOREPLACE
        )
    finally:
        if owns_parents:
            os.close(source_parent)
            os.close(target_parent)
    if result == 0:
        return
    error = get_errno()
    if error == EEXIST:
        raise FileExistsError(target)
    if error == ENOSYS:
        raise HandoffError("atomic no-replace rename is unavailable on this platform")
    raise OSError(error, os.strerror(error), target)


def seal_handoff(source: Path, handoff_root: Path, *, publisher_tool: Path) -> SealedHandoff:
    """Verify and atomically seal one exact data-only bundle without publishing."""
    source_directory_fd = _open_directory(source)
    source_descriptors = _FDGuard(source_directory_fd)
    _assert_local_archive(source / "corpus.dump", parent_fd=source_directory_fd)
    source_files = _verify_source(source, directory_fd=source_directory_fd)
    source_manifest = verify_data_only_bundle(source, directory_fd=source_directory_fd)
    files = [
        {"name": entry["name"], "sha256": entry["sha256"], "size": entry["size"], "mode": 0o400}
        for entry in source_files
    ]
    wheel, wheel_digest, wheel_size, wheel_mode = _publisher_wheel(publisher_tool)
    if wheel_mode & 0o111:
        raise HandoffError("publisher wheel must not be executable")
    files.append({"name": wheel.name, "sha256": wheel_digest, "size": wheel_size, "mode": 0o400})
    seal = {
        "format": "genereviews-local-handoff-v1",
        "corpus_release_id": source_manifest["corpus_release_id"],
        "source_sha256": source_manifest["tarball_source_sha256"],
        "artifact_sha256": next(
            entry["sha256"] for entry in files if entry["name"] == "corpus.dump"
        ),
        "publisher_tool": {"name": wheel.name, "sha256": wheel_digest},
        "files": files,
    }
    seal_bytes = _canonical_json(seal)
    object_id = hashlib.sha256(seal_bytes).hexdigest()
    handoff_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    _assert_handoff_root(handoff_root)
    root_fd = _open_directory(handoff_root)
    root_guard = _FDGuard(root_fd)
    root_info = os.fstat(root_fd)
    target = handoff_root / object_id
    try:
        existing_fd = os.open(
            object_id, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root_fd
        )
    except FileNotFoundError:
        existing_fd = None
    except OSError as error:
        raise HandoffError("handoff object target is unsafe") from error
    if existing_fd is not None:
        os.close(existing_fd)
        raise HandoffError(f"handoff object already exists: {object_id}")

    staging_name = f".seal-{secrets.token_hex(16)}"
    os.mkdir(staging_name, mode=0o700, dir_fd=root_fd)
    staging = handoff_root / staging_name
    staging_fd = os.open(staging_name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root_fd)
    published = False
    try:
        expected_source = {
            entry["name"]: entry for entry in files if entry["name"] in _SOURCE_FILES
        }
        for name in sorted(_SOURCE_FILES):
            _copy_regular(
                source / name,
                staging / name,
                source_parent_fd=source_directory_fd,
                target_parent_fd=staging_fd,
            )
            copied_digest, copied_size, _ = _sha256_facts(staging / name, parent_fd=staging_fd)
            if (
                copied_digest != expected_source[name]["sha256"]
                or copied_size != expected_source[name]["size"]
            ):
                raise HandoffError(f"source {name} changed while sealing")
        if _load_json(staging / "manifest.json", parent_fd=staging_fd) != source_manifest:
            raise HandoffError("source manifest changed while sealing")
        _copy_regular(wheel, staging / wheel.name, target_parent_fd=staging_fd)
        copied_digest, copied_size, _ = _sha256_facts(staging / wheel.name, parent_fd=staging_fd)
        if (copied_digest, copied_size) != (wheel_digest, wheel_size):
            raise HandoffError("publisher wheel changed while sealing")
        manifest = staging / "seal-manifest.json"
        manifest_fd = os.open(
            manifest.name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o400,
            dir_fd=staging_fd,
        )
        try:
            view = memoryview(seal_bytes)
            while view:
                view = view[os.write(manifest_fd, view) :]
            os.fsync(manifest_fd)
        finally:
            os.close(manifest_fd)
        for name in os.listdir(staging_fd):
            os.chmod(name, 0o400, dir_fd=staging_fd, follow_symlinks=False)
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=staging_fd)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        os.fchmod(staging_fd, 0o500)
        os.fsync(staging_fd)
        try:
            _rename_noreplace(staging, target, parent_fd=root_fd)
        except FileExistsError as error:
            raise HandoffError(f"handoff object already exists: {object_id}") from error
        published = True
        os.fsync(root_fd)
        current_root = handoff_root.stat(follow_symlinks=False)
        if (current_root.st_dev, current_root.st_ino) != (root_info.st_dev, root_info.st_ino):
            raise HandoffError("handoff root was substituted during sealing")
    except BaseException:
        if not published:
            os.fchmod(staging_fd, 0o700)
            for name in os.listdir(staging_fd):
                os.chmod(name, 0o600, dir_fd=staging_fd, follow_symlinks=False)
                os.unlink(name, dir_fd=staging_fd)
            os.rmdir(staging_name, dir_fd=root_fd)
        raise
    finally:
        os.close(staging_fd)
        root_guard.close()
    source_descriptors.close()
    return SealedHandoff(object_id=object_id, path=target, manifest=target / "seal-manifest.json")


def verify_handoff(handoff_root: Path, object_id: str) -> SealedHandoff:
    if len(object_id) != 64 or any(char not in "0123456789abcdef" for char in object_id):
        raise HandoffError("object_id must be a lowercase SHA-256")
    _assert_handoff_root(handoff_root)
    target = handoff_root / object_id
    root_fd = _open_directory(handoff_root)
    try:
        target_fd = os.open(
            object_id,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
            dir_fd=root_fd,
        )
    except OSError as error:
        os.close(root_fd)
        raise HandoffError("sealed handoff object is missing or unsafe") from error
    descriptors = _FDGuard(root_fd, target_fd)
    target_info = os.fstat(target_fd)
    if target_info.st_uid != os.geteuid() or stat.S_IMODE(target_info.st_mode) != 0o500:
        raise HandoffError("sealed handoff object must be owned by the invoking user and mode 0500")
    manifest = target / "seal-manifest.json"
    _, _, manifest_mode = _sha256_facts(manifest, parent_fd=target_fd)
    manifest_fd, manifest_info = _open_regular(manifest, parent_fd=target_fd)
    os.close(manifest_fd)
    if manifest_info.st_uid != os.geteuid() or manifest_mode != 0o400:
        raise HandoffError("sealed seal-manifest.json must be owner-read-only mode 0400")
    manifest_bytes = _read_capped(manifest, parent_fd=target_fd)
    if hashlib.sha256(manifest_bytes).hexdigest() != object_id:
        raise HandoffError("sealed handoff object ID does not match its manifest")
    record = _load_json(manifest, parent_fd=target_fd)
    if (
        record.get("format") != "genereviews-local-handoff-v1"
        or not isinstance(record.get("corpus_release_id"), str)
        or not isinstance(record.get("source_sha256"), str)
        or not isinstance(record.get("artifact_sha256"), str)
        or set(record)
        != {
            "format",
            "corpus_release_id",
            "source_sha256",
            "artifact_sha256",
            "publisher_tool",
            "files",
        }
    ):
        raise HandoffError("seal manifest lacks source/artifact identity")
    files = record.get("files")
    publisher = record.get("publisher_tool")
    if (
        not isinstance(publisher, dict)
        or set(publisher) != {"name", "sha256"}
        or not isinstance(publisher["name"], str)
        or not _valid_wheel_name(publisher["name"])
        or not isinstance(publisher["sha256"], str)
        or not re.fullmatch(r"[0-9a-f]{64}", publisher["sha256"])
    ):
        raise HandoffError("seal manifest lacks publisher wheel identity")
    publisher_name = publisher["name"]
    publisher_names = {publisher_name}
    if (
        not isinstance(files, list)
        or {entry.get("name") for entry in files if isinstance(entry, dict)}
        != _SOURCE_FILES | publisher_names
    ):
        raise HandoffError("seal manifest has an incomplete file list")
    if set(os.listdir(target_fd)) != _SOURCE_FILES | publisher_names | {"seal-manifest.json"}:
        raise HandoffError("sealed handoff object has unexpected files")
    expected = {entry["name"]: entry for entry in files if isinstance(entry, dict)}
    if len(expected) != len(files):
        raise HandoffError("seal manifest has duplicate file entries")
    for name, entry in expected.items():
        if set(entry) != {"name", "sha256", "size", "mode"}:
            raise HandoffError("seal manifest file entry has missing or extra fields")
        if (
            not isinstance(entry["sha256"], str)
            or not re.fullmatch(r"[0-9a-f]{64}", entry["sha256"])
            or type(entry["size"]) is not int
            or entry["size"] <= 0
            or entry["mode"] != 0o400
        ):
            raise HandoffError("seal manifest file entry has invalid digest, size, or mode")
        path = target / name
        digest, size, mode = _sha256_facts(path, parent_fd=target_fd)
        file_fd, info = _open_regular(path, parent_fd=target_fd)
        os.close(file_fd)
        if info.st_uid != os.geteuid() or mode != 0o400:
            raise HandoffError(f"sealed {name} must be owned by the invoking user and mode 0400")
        if entry["sha256"] != digest or entry["size"] != size:
            label = "publisher wheel" if name in publisher_names else name
            raise HandoffError(f"sealed {label} does not match seal manifest")
        if name == "corpus.dump" and record["artifact_sha256"] != digest:
            raise HandoffError("sealed corpus.dump does not match source/artifact identity")
    if publisher["sha256"] != expected[publisher_name]["sha256"]:
        raise HandoffError("sealed publisher wheel does not match seal manifest")
    checksums = _parse_sums(target / "SHA256SUMS", parent_fd=target_fd)
    for name, digest in checksums.items():
        actual, _ = _sha256(target / name, parent_fd=target_fd)
        if actual != digest:
            raise HandoffError(f"checksum mismatch for {name}")
    embedded = verify_data_only_bundle(target, allow_extra=True, directory_fd=target_fd)
    if (
        record["source_sha256"] != embedded["tarball_source_sha256"]
        or record["corpus_release_id"] != embedded["corpus_release_id"]
    ):
        raise HandoffError("sealed source/release identity does not match embedded manifest")
    descriptors.close()
    return SealedHandoff(object_id=object_id, path=target, manifest=manifest)


from genereview_link.corpus.handoff_io import copy_regular as _copy_regular  # noqa: E402
from genereview_link.corpus.publisher_gate import (  # noqa: E402
    prepare_publish_handoff,
    verify_rights_record,
)
