"""Bounded, no-follow admission of direct corpus release assets."""

from __future__ import annotations

import hashlib
import os
import re
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path

from genereview_link.strict_json import StrictJsonError, load_strict_json

_MAX_DUMP_BYTES = 4 * 1024**3
_MAX_CONTROL_BYTES = 1024**2
_MEMBERS = frozenset({"corpus.dump", "manifest.json", "SHA256SUMS"})


class DirectSeedError(RuntimeError):
    """A direct release-asset seed violated its exact admission contract."""


@dataclass(frozen=True)
class DirectSeed:
    root: Path
    dump: Path
    manifest: dict[str, object]
    dump_sha256: str


#: Placeholder digests, mirrored from `genereview_link.db.restore`. They are duplicated
#: rather than imported because restore.py imports THIS module; a shared constant would
#: make the cycle. `tests/unit/test_corpus_digest_placeholders.py` pins the two in sync.
_PLACEHOLDER_DIGESTS = frozenset({"0" * 64, "f" * 64, hashlib.sha256(b"").hexdigest()})


def _digest_hex(value: str, *, label: str) -> str:
    normalized = value.strip().removeprefix("sha256:").lower()
    if not re.fullmatch(r"[0-9a-f]{64}", normalized):
        raise DirectSeedError(f"{label} must be an exact SHA-256")
    if normalized in _PLACEHOLDER_DIGESTS:
        # A placeholder anchor would make every downstream comparison in this module
        # compare against a value no real asset has: the seed would be refused, but only
        # after presenting itself as pinned. Refuse the configuration itself instead.
        raise DirectSeedError(f"{label} is a placeholder, not a reviewed release identity")
    return normalized


def _stage_member(
    seed_fd: int,
    destination: Path,
    name: str,
    *,
    size_ceiling: int,
    retain_content: bool,
) -> tuple[Path, str, bytes | None]:
    """Stream one no-follow seed asset to a private staged file."""
    staged_path: Path | None = None
    try:
        source_fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=seed_fd)
    except OSError as error:
        raise DirectSeedError(f"direct corpus seed is missing {name}") from error
    try:
        before = os.fstat(source_fd)
        if not stat.S_ISREG(before.st_mode) or before.st_size > size_ceiling:
            raise DirectSeedError(f"direct corpus seed member is not a bounded file: {name}")
        staged_fd, staged_name = tempfile.mkstemp(prefix=f".{name}.", dir=destination)
        staged_path = Path(staged_name)
        digest = hashlib.sha256()
        retained = bytearray() if retain_content else None
        total = 0
        with os.fdopen(staged_fd, "wb") as staged:
            while chunk := os.read(source_fd, 1024 * 1024):
                total += len(chunk)
                if total > size_ceiling:
                    raise DirectSeedError(
                        f"direct corpus seed member is not a bounded file: {name}"
                    )
                digest.update(chunk)
                staged.write(chunk)
                if retained is not None:
                    retained.extend(chunk)
            staged.flush()
            os.fsync(staged.fileno())
        after = os.fstat(source_fd)
        stable_identity = (
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
        if not stable_identity or total != before.st_size:
            raise DirectSeedError(f"direct corpus seed changed while reading: {name}")
        result = (
            staged_path,
            digest.hexdigest(),
            bytes(retained) if retained is not None else None,
        )
        staged_path = None
        return result
    finally:
        os.close(source_fd)
        if staged_path is not None:
            staged_path.unlink(missing_ok=True)


def extract_direct_seed(
    seed: Path,
    destination: Path,
    *,
    expected_dump_sha256: str,
    expected_manifest_sha256: str | None,
    expected_checksums_sha256: str | None,
) -> DirectSeed:
    """Admit the exact three direct release assets from a read-only seed directory."""
    if expected_manifest_sha256 is None or expected_checksums_sha256 is None:
        raise DirectSeedError("direct corpus seed requires manifest and SHA256SUMS digest anchors")
    dump_anchor = _digest_hex(expected_dump_sha256, label="corpus.dump digest anchor")
    manifest_anchor = _digest_hex(expected_manifest_sha256, label="manifest.json digest anchor")
    sums_anchor = _digest_hex(expected_checksums_sha256, label="SHA256SUMS digest anchor")
    try:
        seed_fd = os.open(seed, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError as error:
        raise DirectSeedError("direct corpus seed directory is unavailable") from error
    staged: dict[str, Path] = {}
    created: list[tuple[Path, tuple[int, int]]] = []
    try:
        if set(os.listdir(seed_fd)) != _MEMBERS:
            raise DirectSeedError(
                "direct corpus seed must contain exactly corpus.dump, manifest.json, SHA256SUMS"
            )
        destination.mkdir(parents=True, exist_ok=True)
        for name in _MEMBERS:
            target = destination / name
            if target.exists() or target.is_symlink():
                raise DirectSeedError(f"restore seed destination already contains {name}")

        manifest_path, manifest_digest, manifest_bytes = _stage_member(
            seed_fd,
            destination,
            "manifest.json",
            size_ceiling=_MAX_CONTROL_BYTES,
            retain_content=True,
        )
        staged["manifest.json"] = manifest_path
        sums_path, sums_digest, sums_bytes = _stage_member(
            seed_fd,
            destination,
            "SHA256SUMS",
            size_ceiling=_MAX_CONTROL_BYTES,
            retain_content=True,
        )
        staged["SHA256SUMS"] = sums_path
        assert manifest_bytes is not None and sums_bytes is not None
        if manifest_digest != manifest_anchor:
            raise DirectSeedError("manifest.json digest does not match the reviewed release asset")
        if sums_digest != sums_anchor:
            raise DirectSeedError("SHA256SUMS digest does not match the reviewed release asset")
        expected_sums = f"{dump_anchor}  corpus.dump\n{manifest_digest}  manifest.json\n".encode()
        if sums_bytes != expected_sums:
            raise DirectSeedError("SHA256SUMS does not bind the exact direct release assets")

        dump_path, dump_digest, _ = _stage_member(
            seed_fd,
            destination,
            "corpus.dump",
            size_ceiling=_MAX_DUMP_BYTES,
            retain_content=False,
        )
        staged["corpus.dump"] = dump_path
        if dump_digest != dump_anchor:
            raise DirectSeedError("corpus.dump digest does not match the reviewed release asset")
        try:
            manifest = load_strict_json(manifest_bytes, max_bytes=_MAX_CONTROL_BYTES)
        except StrictJsonError as error:
            raise DirectSeedError("manifest.json is not valid JSON") from error
        if not isinstance(manifest, dict):
            raise DirectSeedError("manifest.json must be a JSON object")

        for name in ("corpus.dump", "manifest.json", "SHA256SUMS"):
            target = destination / name
            try:
                os.link(staged[name], target, follow_symlinks=False)
            except OSError as error:
                raise DirectSeedError(f"restore seed destination could not admit {name}") from error
            finally:
                # A BaseException can arrive after link(2) succeeded but before Python
                # returns. Admit ownership only when the target is our staged inode;
                # cleanup must never unlink a pre-existing attacker-controlled path.
                try:
                    source_info = staged[name].stat()
                    target_info = target.stat(follow_symlinks=False)
                except OSError:
                    pass
                else:
                    identity = (source_info.st_dev, source_info.st_ino)
                    if identity == (target_info.st_dev, target_info.st_ino) and not any(
                        owned_target == target for owned_target, _ in created
                    ):
                        created.append((target, identity))
        return DirectSeed(
            root=destination,
            dump=destination / "corpus.dump",
            manifest=manifest,
            dump_sha256=dump_digest,
        )
    except BaseException:
        for target, identity in created:
            try:
                current = target.stat(follow_symlinks=False)
                if (current.st_dev, current.st_ino) == identity:
                    target.unlink()
            except FileNotFoundError:
                pass
        raise
    finally:
        os.close(seed_fd)
        for path in staged.values():
            path.unlink(missing_ok=True)


__all__ = ["DirectSeedError", "extract_direct_seed"]
