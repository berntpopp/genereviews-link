"""Descriptor-safe copy helpers for local handoff sealing."""

from __future__ import annotations

import os
import stat
from pathlib import Path

from genereview_link.corpus.handoff import CHUNK_BYTES, HandoffError, _open_regular


def copy_regular(source: Path, destination: Path) -> None:
    source_fd, source_info = _open_regular(source)
    try:
        if not stat.S_ISREG(source_info.st_mode):
            raise HandoffError(f"{source.name} must be a regular file")
        parent_fd = os.open(destination.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            destination_fd = os.open(
                destination.name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o400,
                dir_fd=parent_fd,
            )
        finally:
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
