"""Descriptor-safe copy helpers for local handoff sealing."""

from __future__ import annotations

import os
import stat
from pathlib import Path

from genereview_link.corpus.handoff import CHUNK_BYTES, HandoffError, _open_regular


def copy_regular(
    source: Path,
    destination: Path,
    *,
    source_parent_fd: int | None = None,
    target_parent_fd: int | None = None,
) -> None:
    source_fd, source_info = _open_regular(source, parent_fd=source_parent_fd)
    try:
        if not stat.S_ISREG(source_info.st_mode):
            raise HandoffError(f"{source.name} must be a regular file")
        from genereview_link.corpus.handoff import _open_directory

        owns_parent = target_parent_fd is None
        parent_fd = _open_directory(destination.parent) if owns_parent else target_parent_fd
        assert parent_fd is not None
        try:
            destination_fd = os.open(
                destination.name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o400,
                dir_fd=parent_fd,
            )
        finally:
            if owns_parent:
                os.close(parent_fd)
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
