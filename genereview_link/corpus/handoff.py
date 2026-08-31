"""Fail-closed, local-only sealing for corpus publication handoffs."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import stat
import tempfile
from dataclasses import dataclass
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


def _regular_file(path: Path) -> os.stat_result:
    try:
        info = path.lstat()
    except FileNotFoundError as error:
        raise HandoffError(f"missing required file: {path.name}") from error
    if not stat.S_ISREG(info.st_mode):
        raise HandoffError(f"{path.name} must be a regular file")
    return info


def _open_regular(path: Path) -> tuple[int, os.stat_result]:
    parent_fd: int | None = None
    try:
        parent_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as error:
        if parent_fd is not None:
            os.close(parent_fd)
        raise HandoffError(f"unsafe or missing required file: {path.name}") from error
    os.close(parent_fd)
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


def _verify_source(source: Path, *, allow_extra: bool = False) -> list[dict[str, object]]:
    if not source.is_dir() or source.is_symlink():
        raise HandoffError("source must be a real directory")
    names = {path.name for path in source.iterdir()}
    if not _SOURCE_FILES.issubset(names) or (not allow_extra and names != _SOURCE_FILES):
        raise HandoffError("source must contain exactly the required regular files")
    if allow_extra:
        extras = names - _SOURCE_FILES
        if any(name != _SEAL_MANIFEST_FILE and not _valid_wheel_name(name) for name in extras):
            raise HandoffError("source contains an unexpected extra file")
        for name in extras:
            _regular_file(source / name)
    checksums = _parse_sums(source / "SHA256SUMS")
    files: list[dict[str, object]] = []
    for name in sorted(_SOURCE_FILES):
        path = source / name
        digest, size, mode = _sha256_facts(path)
        if name in checksums and checksums[name] != digest:
            raise HandoffError(f"checksum mismatch for {name}")
        files.append({"name": name, "sha256": digest, "size": size, "mode": mode})
    return files


def verify_data_only_bundle(bundle: Path, *, allow_extra: bool = False) -> dict[str, object]:
    """Validate a fresh local bundle before restore or sealing."""
    _verify_source(bundle, allow_extra=allow_extra)
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
    app_git_sha = metadata.get("app_git_sha")
    if not (
        isinstance(app_git_sha, str)
        and len(app_git_sha) in {40, 64}
        and all(char in "0123456789abcdef" for char in app_git_sha)
    ):
        raise HandoffError("manifest.json application Git revision is incomplete")
    app_version = metadata.get("app_version")
    if not (
        isinstance(app_version, str)
        and app_version
        and metadata.get("genereview_link_version") == app_version
    ):
        raise HandoffError("manifest.json application version identity is incomplete")
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
    hnsw = metadata.get("hnsw")
    if not (
        isinstance(hnsw, dict)
        and set(hnsw) == {"index_name", "exists"}
        and hnsw.get("index_name") == "genereview_embeddings_bge384_hnsw_cosine"
        and hnsw.get("exists") is True
    ):
        raise HandoffError("manifest.json lacks the validated HNSW identity")
    migrations = metadata.get("schema_migrations")
    if not (
        isinstance(migrations, dict)
        and set(migrations) == {"control", "data"}
        and all(
            isinstance(values, list)
            and values
            and all(isinstance(value, str) and value for value in values)
            and len(values) == len(set(values))
            for values in migrations.values()
        )
    ):
        raise HandoffError("manifest.json schema migration identity is incomplete")
    from genereview_link.corpus.bundle_validation import (
        EXPECTED_CONTROL_MIGRATIONS,
        EXPECTED_DATA_MIGRATIONS,
    )

    if (
        set(migrations["control"]) != EXPECTED_CONTROL_MIGRATIONS
        or set(migrations["data"]) != EXPECTED_DATA_MIGRATIONS
    ):
        raise HandoffError("manifest.json schema migrations do not match reviewed migrations")
    migration_digests = metadata.get("migration_file_sha256")
    from genereview_link.corpus.bundle import _reviewed_migration_digests

    expected_migration_digests = _reviewed_migration_digests()
    if migration_digests != expected_migration_digests:
        raise HandoffError("manifest.json migration file digests do not match reviewed SQL")
    postgres = metadata.get("postgres")
    if not (
        isinstance(postgres, dict)
        and set(postgres) == {"major_version", "pgvector_version"}
        and all(isinstance(postgres[name], str) and postgres[name] for name in postgres)
    ):
        raise HandoffError("manifest.json PostgreSQL identity is incomplete")
    if postgres != {"major_version": "18", "pgvector_version": "0.8.2"}:
        raise HandoffError("manifest.json PostgreSQL identity does not match reviewed runtime")
    from genereview_link.corpus.source_identity import validate_source_identity

    try:
        validate_source_identity(
            metadata.get("source"),
            tarball_sha256=source_sha256,
            last_updated=str(metadata.get("tarball_last_updated")),
        )
    except ValueError as error:
        raise HandoffError("manifest.json upstream source identity is incomplete") from error
    validation = metadata.get("validation")
    if not isinstance(validation, dict) or validation.get("status") != "passed":
        raise HandoffError("manifest.json lacks a passing candidate validation")
    evaluation = metadata.get("evaluation")
    if not isinstance(evaluation, dict) or set(evaluation) != {
        "status",
        "suite",
        "suite_sha256",
        "model_name",
        "results",
        "result_sha256",
    }:
        raise HandoffError("manifest.json lacks exact evaluation evidence")
    if (
        evaluation["status"] != "passed"
        or evaluation["suite"] != "tests/eval/genereviews_queries.jsonl"
        or evaluation["model_name"] != "BAAI/bge-small-en-v1.5"
        or not isinstance(evaluation["suite_sha256"], str)
        or not re.fullmatch(r"[0-9a-f]{64}", evaluation["suite_sha256"])
        or not isinstance(evaluation["result_sha256"], str)
        or not re.fullmatch(r"[0-9a-f]{64}", evaluation["result_sha256"])
        or not isinstance(evaluation["results"], dict)
        or set(evaluation["results"]) != {"mrr_at_10", "section_precision_at_5", "queries_run"}
        or type(evaluation["results"]["queries_run"]) is not int
        or evaluation["results"]["queries_run"] <= 0
        or any(
            type(evaluation["results"][name]) not in {int, float}
            or not math.isfinite(evaluation["results"][name])
            or not 0 <= evaluation["results"][name] <= 1
            for name in ("mrr_at_10", "section_precision_at_5")
        )
    ):
        raise HandoffError("manifest.json evaluation evidence is invalid")
    if (
        hashlib.sha256(_canonical_json(evaluation["results"])).hexdigest()
        != evaluation["result_sha256"]
    ):
        raise HandoffError("manifest.json evaluation result digest mismatch")
    from genereview_link.corpus.source_identity import validate_release_id

    try:
        validate_release_id(str(metadata["corpus_release_id"]))
    except ValueError as error:
        raise HandoffError("manifest.json corpus_release_id is invalid") from error
    updated = metadata.get("tarball_last_updated")
    release_id = str(metadata["corpus_release_id"])
    if (
        not isinstance(updated, str)
        or len(updated) < 10
        or release_id[:10] != updated[:10]
        or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", updated[:10])
    ):
        raise HandoffError(
            "manifest.json corpus_release_id date must match upstream last_updated date"
        )
    checksums = metadata["checksums"]
    if not isinstance(checksums, dict) or set(checksums) != {"corpus.dump"}:
        raise HandoffError("manifest.json checksums must cover exactly corpus.dump")
    digest, _ = _sha256(bundle / "corpus.dump")
    if checksums["corpus.dump"] != digest:
        raise HandoffError("manifest checksum mismatch for corpus.dump")
    return metadata


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
    checkout = Path.cwd().resolve()
    serving = os.getenv("GENEREVIEW_SERVING_ROOT")
    if resolved == checkout or checkout in resolved.parents:
        raise HandoffError("handoff root must be outside the checkout")
    if serving:
        serving_path = Path(serving).resolve()
        if resolved == serving_path or serving_path in resolved.parents:
            raise HandoffError("handoff root must be outside serving volumes")
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
    source_files = _verify_source(source)
    source_manifest = verify_data_only_bundle(source)
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
    target = handoff_root / object_id
    if target.exists() or target.is_symlink():
        raise HandoffError(f"handoff object already exists: {object_id}")

    staging = Path(tempfile.mkdtemp(prefix=".seal-", dir=handoff_root))
    try:
        expected_source = {
            entry["name"]: entry for entry in files if entry["name"] in _SOURCE_FILES
        }
        for name in sorted(_SOURCE_FILES):
            _copy_regular(source / name, staging / name)
            copied_digest, copied_size, _ = _sha256_facts(staging / name)
            if (
                copied_digest != expected_source[name]["sha256"]
                or copied_size != expected_source[name]["size"]
            ):
                raise HandoffError(f"source {name} changed while sealing")
        if _load_json(staging / "manifest.json") != source_manifest:
            raise HandoffError("source manifest changed while sealing")
        _copy_regular(wheel, staging / wheel.name)
        copied_digest, copied_size, _ = _sha256_facts(staging / wheel.name)
        if (copied_digest, copied_size) != (wheel_digest, wheel_size):
            raise HandoffError("publisher wheel changed while sealing")
        manifest = staging / "seal-manifest.json"
        manifest.write_bytes(seal_bytes)
        with manifest.open("rb") as file:
            os.fsync(file.fileno())
        for path in staging.iterdir():
            path.chmod(0o400)
        staging.chmod(0o500)
        staging_fd = os.open(staging, os.O_RDONLY)
        try:
            os.fsync(staging_fd)
        finally:
            os.close(staging_fd)
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
    if len(object_id) != 64 or any(char not in "0123456789abcdef" for char in object_id):
        raise HandoffError("object_id must be a lowercase SHA-256")
    _assert_handoff_root(handoff_root)
    target = handoff_root / object_id
    if not target.is_dir() or target.is_symlink():
        raise HandoffError("sealed handoff object is missing or unsafe")
    target_info = target.lstat()
    if target_info.st_uid != os.geteuid() or stat.S_IMODE(target_info.st_mode) != 0o500:
        raise HandoffError("sealed handoff object must be owned by the invoking user and mode 0500")
    manifest = target / "seal-manifest.json"
    _, _, manifest_mode = _sha256_facts(manifest)
    manifest_info = manifest.lstat()
    if manifest_info.st_uid != os.geteuid() or manifest_mode != 0o400:
        raise HandoffError("sealed seal-manifest.json must be owner-read-only mode 0400")
    manifest_bytes = _read_capped(manifest)
    if hashlib.sha256(manifest_bytes).hexdigest() != object_id:
        raise HandoffError("sealed handoff object ID does not match its manifest")
    record = _load_json(manifest)
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
    if {path.name for path in target.iterdir()} != _SOURCE_FILES | publisher_names | {
        "seal-manifest.json"
    }:
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
        digest, size, mode = _sha256_facts(path)
        info = path.lstat()
        if info.st_uid != os.geteuid() or mode != 0o400:
            raise HandoffError(f"sealed {name} must be owned by the invoking user and mode 0400")
        if entry["sha256"] != digest or entry["size"] != size:
            label = "publisher wheel" if name in publisher_names else name
            raise HandoffError(f"sealed {label} does not match seal manifest")
        if name == "corpus.dump" and record["artifact_sha256"] != digest:
            raise HandoffError("sealed corpus.dump does not match source/artifact identity")
    if publisher["sha256"] != expected[publisher_name]["sha256"]:
        raise HandoffError("sealed publisher wheel does not match seal manifest")
    checksums = _parse_sums(target / "SHA256SUMS")
    for name, digest in checksums.items():
        actual, _ = _sha256(target / name)
        if actual != digest:
            raise HandoffError(f"checksum mismatch for {name}")
    embedded = verify_data_only_bundle(target, allow_extra=True)
    if (
        record["source_sha256"] != embedded["tarball_source_sha256"]
        or record["corpus_release_id"] != embedded["corpus_release_id"]
    ):
        raise HandoffError("sealed source/release identity does not match embedded manifest")
    return SealedHandoff(object_id=object_id, path=target, manifest=manifest)


from genereview_link.corpus.handoff_io import copy_regular as _copy_regular  # noqa: E402
from genereview_link.corpus.publisher_gate import (  # noqa: E402
    prepare_publish_handoff,
    verify_rights_record,
)
