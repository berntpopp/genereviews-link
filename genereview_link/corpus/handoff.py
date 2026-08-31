"""Fail-closed, local-only sealing for corpus publication handoffs.

This module deliberately has no GitHub client and never starts a publication.
It turns a verified data-only bundle into an immutable local object which a
separate, privileged publisher may inspect after rights review.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

MAX_METADATA_BYTES = 1 << 20
CHUNK_BYTES = 1 << 20
_SOURCE_FILES = frozenset({"corpus.dump", "manifest.json", "SHA256SUMS"})
_PUBLISHER_FILE = "publisher-tool.whl"
_SEALED_FILES = _SOURCE_FILES | {_PUBLISHER_FILE, "seal-manifest.json"}
_RIGHTS_FIELDS = frozenset(
    {
        "artifact_sha256",
        "object_id",
        "decision",
        "authority",
        "decision_time",
        "terms_version",
        "permitted_asset_use",
        "attribution",
        "evidence_uri",
        "source_sha256",
        "corpus_release_id",
    }
)


class HandoffError(ValueError):
    """The handoff cannot safely be sealed or used."""


@dataclass(frozen=True)
class SealedHandoff:
    """An immutable, locally verified corpus object."""

    object_id: str
    path: Path
    manifest: Path


def _regular_file(path: Path) -> os.stat_result:
    try:
        info = path.lstat()
    except FileNotFoundError as error:
        raise HandoffError(f"missing required file: {path.name}") from error
    if not stat.S_ISREG(info.st_mode):
        raise HandoffError(f"{path.name} must be a regular file")
    return info


def _open_regular(path: Path) -> tuple[int, os.stat_result]:
    """Open one regular file without a pathname-following race."""
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError as error:
        raise HandoffError(f"unsafe or missing required file: {path.name}") from error
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode):
        os.close(fd)
        raise HandoffError(f"{path.name} must be a regular file")
    return fd, info


def _read_capped(path: Path, *, limit: int = MAX_METADATA_BYTES) -> bytes:
    fd, info = _open_regular(path)
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


def _sha256(path: Path) -> tuple[str, int]:
    digest, size, _ = _sha256_facts(path)
    return digest, size


def _sha256_facts(path: Path) -> tuple[str, int, int]:
    """Digest one no-follow regular file and return its current file mode too."""
    digest = hashlib.sha256()
    size = 0
    fd, info = _open_regular(path)
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


def _load_json(path: Path) -> dict[str, object]:
    try:
        value = json.loads(_read_capped(path))
    except json.JSONDecodeError as error:
        raise HandoffError(f"invalid JSON: {path.name}") from error
    if not isinstance(value, dict):
        raise HandoffError(f"{path.name} must contain a JSON object")
    return value


def _parse_sums(path: Path) -> dict[str, str]:
    try:
        lines = _read_capped(path).decode("ascii").splitlines()
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


def _verify_source(source: Path) -> list[dict[str, object]]:
    if not source.is_dir() or source.is_symlink():
        raise HandoffError("source must be a real directory")
    names = {path.name for path in source.iterdir()}
    if names != _SOURCE_FILES:
        raise HandoffError("source must contain exactly the required regular files")
    checksums = _parse_sums(source / "SHA256SUMS")
    files: list[dict[str, object]] = []
    for name in sorted(_SOURCE_FILES):
        path = source / name
        digest, size, mode = _sha256_facts(path)
        if name in checksums and checksums[name] != digest:
            raise HandoffError(f"checksum mismatch for {name}")
        files.append({"name": name, "sha256": digest, "size": size, "mode": mode})
    return files


def verify_data_only_bundle(bundle: Path) -> dict[str, object]:
    """Validate a fresh data-only release directory before restore or sealing.

    It accepts no archive expansion, links, side data, or unchecked metadata:
    callers must have already placed the three exact release assets in a fresh
    directory using their bounded downloader.
    """
    _verify_source(bundle)
    metadata = _load_json(bundle / "manifest.json")
    from genereview_link.corpus.bundle import BundleManifest

    expected = BundleManifest()
    stable = set(expected.__dataclass_fields__) - {"created_at", "checksums"}
    if set(metadata) != stable | {"checksums"}:
        raise HandoffError("manifest.json has missing, extra, or volatile fields")
    for name in stable:
        if type(metadata[name]) is not type(getattr(expected, name)):
            raise HandoffError(f"manifest.json field has invalid type: {name}")
    if (
        metadata["manifest_version"] != "3"
        or metadata["bundle_format"] != "postgresql-custom-data-only"
    ):
        raise HandoffError("manifest.json is not a v3 data-only bundle")
    source_sha256 = metadata.get("tarball_source_sha256")
    if not (
        isinstance(source_sha256, str)
        and len(source_sha256) == 64
        and all(char in "0123456789abcdef" for char in source_sha256)
    ):
        raise HandoffError("manifest.json tarball_source_sha256 must be a lowercase SHA-256")
    embedding = metadata.get("embedding")
    if not isinstance(embedding, dict) or set(embedding) != {
        "model_name",
        "dimension",
        "distance_metric",
        "active_table",
        "count",
        "expected_count",
    }:
        raise HandoffError("manifest.json embedding identity is incomplete")
    if any(
        type(embedding[name]) is not expected_type
        for name, expected_type in {
            "model_name": str,
            "dimension": int,
            "distance_metric": str,
            "active_table": str,
            "count": int,
            "expected_count": int,
        }.items()
    ):
        raise HandoffError("manifest.json embedding identity has invalid types")
    if (
        embedding["count"] != metadata["passage_count"]
        or embedding["expected_count"] != metadata["passage_count"]
    ):
        raise HandoffError("manifest.json embedding count does not match passage_count")
    validation = metadata.get("validation")
    if not isinstance(validation, dict) or validation.get("status") != "passed":
        raise HandoffError("manifest.json lacks a passing candidate validation")
    from genereview_link.corpus.bundle_metadata import validate_release_id

    try:
        validate_release_id(str(metadata["corpus_release_id"]))
    except ValueError as error:
        raise HandoffError("manifest.json corpus_release_id is invalid") from error
    checksums = metadata["checksums"]
    if not isinstance(checksums, dict) or set(checksums) != {"corpus.dump"}:
        raise HandoffError("manifest.json checksums must cover exactly corpus.dump")
    digest, _ = _sha256(bundle / "corpus.dump")
    if checksums["corpus.dump"] != digest:
        raise HandoffError("manifest checksum mismatch for corpus.dump")
    return metadata


def _copy_regular(source: Path, destination: Path) -> None:
    source_fd = os.open(source, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        if not stat.S_ISREG(os.fstat(source_fd).st_mode):
            raise HandoffError(f"{source.name} must be a regular file")
        destination_fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o400)
        try:
            while chunk := os.read(source_fd, CHUNK_BYTES):
                view = memoryview(chunk)
                while view:
                    view = view[os.write(destination_fd, view) :]
            os.fsync(destination_fd)
        finally:
            os.close(destination_fd)
    finally:
        os.close(source_fd)


def _publisher_wheel(publisher_tool: Path) -> tuple[Path, str, int, int]:
    """Return the sole regular wheel in a fresh publisher-tool directory."""
    try:
        info = publisher_tool.lstat()
    except FileNotFoundError as error:
        raise HandoffError("publisher-tool directory is missing") from error
    if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
        raise HandoffError("publisher-tool must be a real directory")
    wheels = [path for path in publisher_tool.iterdir() if path.name.endswith(".whl")]
    if len(wheels) != 1 or len(list(publisher_tool.iterdir())) != 1:
        raise HandoffError("publisher-tool must contain exactly one publisher wheel")
    wheel = wheels[0]
    digest, size, mode = _sha256_facts(wheel)
    return wheel, digest, size, mode


def _assert_handoff_root(handoff_root: Path) -> None:
    try:
        info = handoff_root.lstat()
    except FileNotFoundError as error:
        raise HandoffError("handoff root is missing") from error
    if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
        raise HandoffError("handoff root must be a real directory")
    if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise HandoffError("handoff root must be owner-only mode 0700")


def seal_handoff(source: Path, handoff_root: Path, *, publisher_tool: Path) -> SealedHandoff:
    """Verify and atomically seal one exact data-only bundle without publishing."""
    source_manifest = verify_data_only_bundle(source)
    files = _verify_source(source)
    wheel, wheel_digest, wheel_size, wheel_mode = _publisher_wheel(publisher_tool)
    if wheel_mode & 0o111:
        raise HandoffError("publisher wheel must not be executable")
    files.append(
        {"name": _PUBLISHER_FILE, "sha256": wheel_digest, "size": wheel_size, "mode": 0o400}
    )
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
    target = handoff_root / object_id
    if target.exists() or target.is_symlink():
        raise HandoffError(f"handoff object already exists: {object_id}")

    staging = Path(tempfile.mkdtemp(prefix=".seal-", dir=handoff_root))
    try:
        for name in sorted(_SOURCE_FILES):
            _copy_regular(source / name, staging / name)
        _copy_regular(wheel, staging / _PUBLISHER_FILE)
        copied_digest, copied_size, _ = _sha256_facts(staging / _PUBLISHER_FILE)
        if (copied_digest, copied_size) != (wheel_digest, wheel_size):
            raise HandoffError("publisher wheel changed while sealing")
        manifest = staging / "seal-manifest.json"
        manifest.write_bytes(seal_bytes)
        with manifest.open("rb") as file:
            os.fsync(file.fileno())
        for path in staging.iterdir():
            path.chmod(0o400)
        staging.chmod(0o500)
        os.replace(staging, target)
        root_fd = os.open(handoff_root, os.O_RDONLY)
        try:
            os.fsync(root_fd)
        finally:
            os.close(root_fd)
    except BaseException:
        if staging.exists():
            for path in staging.iterdir():
                path.unlink()
            staging.rmdir()
        raise
    return SealedHandoff(object_id=object_id, path=target, manifest=target / "seal-manifest.json")


def verify_handoff(handoff_root: Path, object_id: str) -> SealedHandoff:
    """Reverify a sealed object before any privileged action is considered."""
    if len(object_id) != 64 or any(char not in "0123456789abcdef" for char in object_id):
        raise HandoffError("object_id must be a lowercase SHA-256")
    _assert_handoff_root(handoff_root)
    target = handoff_root / object_id
    if not target.is_dir() or target.is_symlink():
        raise HandoffError("sealed handoff object is missing or unsafe")
    target_info = target.lstat()
    if stat.S_IMODE(target_info.st_mode) != 0o500:
        raise HandoffError("sealed handoff object must be owner-read-only mode 0500")
    if {path.name for path in target.iterdir()} != _SEALED_FILES:
        raise HandoffError("sealed handoff object has unexpected files")
    manifest = target / "seal-manifest.json"
    manifest_bytes = _read_capped(manifest)
    if hashlib.sha256(manifest_bytes).hexdigest() != object_id:
        raise HandoffError("sealed handoff object ID does not match its manifest")
    record = _load_json(manifest)
    if (
        record.get("format") != "genereviews-local-handoff-v1"
        or not isinstance(record.get("corpus_release_id"), str)
        or not isinstance(record.get("source_sha256"), str)
        or not isinstance(record.get("artifact_sha256"), str)
    ):
        raise HandoffError("seal manifest lacks source/artifact identity")
    files = record.get("files")
    if not isinstance(files, list) or {
        entry.get("name") for entry in files if isinstance(entry, dict)
    } != _SOURCE_FILES | {_PUBLISHER_FILE}:
        raise HandoffError("seal manifest has an incomplete file list")
    expected = {entry["name"]: entry for entry in files if isinstance(entry, dict)}
    for name in _SOURCE_FILES:
        path = target / name
        digest, size, mode = _sha256_facts(path)
        if mode != 0o400:
            raise HandoffError(f"sealed {name} must be owner-read-only mode 0400")
        entry = expected[name]
        if entry.get("sha256") != digest or entry.get("size") != size:
            raise HandoffError(f"sealed {name} does not match seal manifest")
        if name == "corpus.dump" and record["artifact_sha256"] != digest:
            raise HandoffError("sealed corpus.dump does not match source/artifact identity")
    publisher = record.get("publisher_tool")
    if (
        not isinstance(publisher, dict)
        or set(publisher) != {"name", "sha256"}
        or not isinstance(publisher["name"], str)
        or not publisher["name"].endswith(".whl")
        or not isinstance(publisher["sha256"], str)
    ):
        raise HandoffError("seal manifest lacks publisher wheel identity")
    tool_digest, tool_size, tool_mode = _sha256_facts(target / _PUBLISHER_FILE)
    tool_entry = expected[_PUBLISHER_FILE]
    if (
        publisher["sha256"] != tool_digest
        or tool_entry.get("sha256") != tool_digest
        or tool_entry.get("size") != tool_size
        or tool_mode != 0o400
    ):
        raise HandoffError("sealed publisher wheel does not match seal manifest")
    checksums = _parse_sums(target / "SHA256SUMS")
    for name, digest in checksums.items():
        actual, _ = _sha256(target / name)
        if actual != digest:
            raise HandoffError(f"checksum mismatch for {name}")
    return SealedHandoff(object_id=object_id, path=target, manifest=manifest)


def verify_rights_record(
    rights_path: Path, object_id: str, *, sealed: SealedHandoff | None = None
) -> dict[str, object]:
    """Accept only a complete affirmative rights decision bound to ``object_id``."""
    record = _load_json(rights_path)
    if set(record) != _RIGHTS_FIELDS or not all(
        isinstance(value, str) and value for value in record.values()
    ):
        raise HandoffError("rights record must contain exactly the complete required fields")
    if record["object_id"] != object_id:
        raise HandoffError("rights record is not bound to this handoff object")
    if record["decision"] != "affirmative":
        raise HandoffError("rights record decision must be affirmative")
    decision_time = str(record["decision_time"])
    if not decision_time.endswith("Z"):
        raise HandoffError("rights record decision_time must be dated UTC")
    try:
        datetime.fromisoformat(f"{decision_time[:-1]}+00:00")
    except ValueError as error:
        raise HandoffError(
            "rights record decision_time must be an ISO-8601 UTC timestamp"
        ) from error
    if sealed is not None:
        seal = _load_json(sealed.manifest)
        for name in ("source_sha256", "artifact_sha256", "corpus_release_id"):
            if record[name] != seal[name]:
                raise HandoffError(f"rights record is not bound to sealed {name}")
    return record


def prepare_publish_handoff(handoff_root: Path, object_id: str, rights_path: Path) -> SealedHandoff:
    """Reverify an object and rights record; intentionally performs no publication."""
    sealed = verify_handoff(handoff_root, object_id)
    verify_rights_record(rights_path, sealed.object_id, sealed=sealed)
    return sealed
