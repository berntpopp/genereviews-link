"""Private immutable-by-admission copies for one offline ingest invocation."""

from __future__ import annotations

import os
import stat
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from genereview_link.corpus.archive import MAX_LISTING_BYTES, MAX_TARBALL_BYTES
from genereview_link.corpus.source_assets import GENESIS_SOURCE_ASSETS, SOURCE_ASSETS
from genereview_link.corpus.source_identity import SIDEDATA_FILES

CHUNK_BYTES = 1 << 20
MAX_SIDEDATA_BYTES = 64 * 1024 * 1024
MAX_CONTROL_BYTES = 4 * 1024 * 1024


class SourceSnapshotError(ValueError):
    """Offline source bytes could not be admitted into a stable private snapshot."""


@dataclass(frozen=True, slots=True)
class OfflineSourceSnapshot:
    root: Path
    archive: Path
    side_data_dir: Path
    source_metadata: Path
    file_list: Path
    prior_manifest: Path | None


def _open_directory(path: Path) -> int:
    current = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.absolute().parts[1:]:
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


def _copy_stable_regular(source: Path, destination: Path, *, max_bytes: int) -> None:
    source_parent = _open_directory(source.parent)
    destination_parent = _open_directory(destination.parent)
    source_fd: int | None = None
    destination_fd: int | None = None
    try:
        source_fd = os.open(source.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=source_parent)
        before = os.fstat(source_fd)
        if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= max_bytes:
            raise SourceSnapshotError(f"{source.name} is not a bounded regular source file")
        destination_fd = os.open(
            destination.name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=destination_parent,
        )
        copied = 0
        while chunk := os.read(source_fd, CHUNK_BYTES):
            copied += len(chunk)
            if copied > max_bytes:
                raise SourceSnapshotError(f"{source.name} changed beyond its reviewed bound")
            view = memoryview(chunk)
            while view:
                view = view[os.write(destination_fd, view) :]
        after = os.fstat(source_fd)
        if copied != before.st_size or (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
        ) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
            raise SourceSnapshotError(f"{source.name} changed during source admission")
        os.fsync(destination_fd)
        admitted = os.fstat(destination_fd)
        if not stat.S_ISREG(admitted.st_mode) or admitted.st_size != copied:
            raise SourceSnapshotError(f"{source.name} snapshot admission is incomplete")
        os.fchmod(destination_fd, 0o400)
    except OSError as error:
        raise SourceSnapshotError(f"{source.name} could not be admitted safely") from error
    finally:
        if destination_fd is not None:
            os.close(destination_fd)
        if source_fd is not None:
            os.close(source_fd)
        os.close(destination_parent)
        os.close(source_parent)


@contextmanager
def admit_offline_source(
    *,
    archive: Path,
    side_data_dir: Path,
    source_metadata: Path,
    prior_manifest: Path | None,
) -> Iterator[OfflineSourceSnapshot]:
    """Yield private copies used by both provenance validation and parsing.

    A genesis admission (no prior manifest) copies exactly the same inventory
    minus the single prior-artifact file; the presence or absence of that one
    file is the whole difference between the two shapes.
    """
    genesis = prior_manifest is None
    expected = GENESIS_SOURCE_ASSETS if genesis else SOURCE_ASSETS
    sources = {
        "source-capture.json": source_metadata,
        "file_list.csv": source_metadata.with_name("file_list.csv"),
        "gene_NBK1116.tar.gz": archive,
        **{name: side_data_dir / name for name in SIDEDATA_FILES},
    }
    limits = {
        "source-capture.json": MAX_CONTROL_BYTES,
        "file_list.csv": MAX_LISTING_BYTES,
        "gene_NBK1116.tar.gz": MAX_TARBALL_BYTES,
        **dict.fromkeys(SIDEDATA_FILES, MAX_SIDEDATA_BYTES),
    }
    if prior_manifest is not None:
        sources["prior-manifest.json"] = prior_manifest
        limits["prior-manifest.json"] = MAX_CONTROL_BYTES
    if set(sources) != expected or set(limits) != expected:
        raise SourceSnapshotError("offline source inventory differs from the exact asset contract")
    with tempfile.TemporaryDirectory(prefix="genereviews-admitted-source-") as temporary:
        root = Path(temporary)
        os.chmod(root, 0o700)
        for name in sorted(expected):
            _copy_stable_regular(sources[name], root / name, max_bytes=limits[name])
        yield OfflineSourceSnapshot(
            root=root,
            archive=root / "gene_NBK1116.tar.gz",
            side_data_dir=root,
            source_metadata=root / "source-capture.json",
            file_list=root / "file_list.csv",
            prior_manifest=None if genesis else root / "prior-manifest.json",
        )


__all__ = [
    "OfflineSourceSnapshot",
    "SourceSnapshotError",
    "admit_offline_source",
]
