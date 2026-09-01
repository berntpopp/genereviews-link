"""Materialise the reviewed embedding model from the read-only seed into a volume.

This runs inside the same no-egress init sidecar that restores the corpus, and for the
same reason: the serving container must not fetch anything, and the fleet deployment gate
permits exactly one bind mount -- the seed directory on the declared init service -- so a
staged artifact can only reach the server by being copied into a named volume first.

The trust root is `genereview_link.retrieval.model_identity`: every member is proven
against a digest committed in this repository before it is written, and again by the
serving process before the model is loaded. A substituted model never reaches the ONNX
parser at either end.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import stat
import tempfile
from pathlib import Path

from genereview_link.retrieval.model_identity import BGE_RUNTIME_FILES

__all__ = ["ModelSeedError", "materialize_model"]

#: Ceiling for any single model member. The ONNX graph is ~127 MiB; this bounds a
#: substituted or maliciously large file without constraining the reviewed artifact.
_MAX_MEMBER_BYTES = 512 * 1024**2


class ModelSeedError(RuntimeError):
    """The staged model artifact is absent, unbounded, or not the reviewed model."""


def _digest_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _already_materialized(destination: Path) -> bool:
    """Return whether the destination already holds exactly the reviewed model."""
    for member, expected in BGE_RUNTIME_FILES.items():
        target = destination / member
        if not target.is_file() or target.is_symlink() or _digest_of(target) != expected:
            return False
    return True


def materialize_model(seed_dir: Path, destination: Path) -> dict[str, str]:
    """Copy the reviewed model members from *seed_dir* into *destination*.

    Idempotent: a destination that already holds exactly the reviewed bytes is left
    untouched, so restarting the sidecar does not recopy 127 MiB.

    Raises:
        ModelSeedError: a member is missing, is not a bounded regular file, or does not
            match the digest committed in this repository.
    """
    if _already_materialized(destination):
        return dict(BGE_RUNTIME_FILES)

    if not seed_dir.is_dir():
        raise ModelSeedError(
            f"the model seed directory {seed_dir} is not present; stage the reviewed model "
            "release asset before starting the stack (see docs/data.md)"
        )
    destination.mkdir(parents=True, exist_ok=True)

    written: dict[str, str] = {}
    for member, expected in sorted(BGE_RUNTIME_FILES.items()):
        source = seed_dir / member
        if not source.is_file() or source.is_symlink():
            raise ModelSeedError(f"the model seed is missing a regular {member}")
        if source.stat().st_size > _MAX_MEMBER_BYTES:
            raise ModelSeedError(f"model seed member exceeds its size ceiling: {member}")

        # Verify the SOURCE before writing, so unreviewed bytes never land in the volume
        # even transiently, then verify the written copy before publishing the name.
        actual = _digest_of(source)
        if actual != expected:
            raise ModelSeedError(
                f"{member} does not match the reviewed model identity; refusing to stage it"
            )

        handle, staged_name = tempfile.mkstemp(prefix=f".{member}.", dir=destination)
        staged = Path(staged_name)
        try:
            os.close(handle)
            shutil.copyfile(source, staged)
            if _digest_of(staged) != expected:
                raise ModelSeedError(f"{member} changed while it was being staged")
            staged.chmod(stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
            staged.replace(destination / member)
            staged = None  # type: ignore[assignment]
        finally:
            if staged is not None:
                staged.unlink(missing_ok=True)
        written[member] = expected
    return written
