"""Deadline- and output-bounded subprocess execution for archive inspection."""

from __future__ import annotations

import os
import selectors
import subprocess
from collections.abc import Sequence
from time import monotonic


class BoundedProcessError(RuntimeError):
    """A child exceeded its reviewed time or output budget."""


def run_bounded_process(
    arguments: Sequence[str],
    *,
    pass_fds: tuple[int, ...] = (),
    timeout_seconds: float = 60.0,
    max_output_bytes: int = 4 * 1024 * 1024,
) -> subprocess.CompletedProcess[str]:
    """Run without shell while draining both pipes under one total byte/deadline cap."""
    process = subprocess.Popen(  # noqa: S603 - caller supplies an explicit absolute executable
        list(arguments),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        pass_fds=pass_fds,
    )
    assert process.stdout is not None and process.stderr is not None
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ, "stdout")
    selector.register(process.stderr, selectors.EVENT_READ, "stderr")
    output = {"stdout": bytearray(), "stderr": bytearray()}
    total = 0
    deadline = monotonic() + timeout_seconds
    try:
        while selector.get_map():
            remaining = deadline - monotonic()
            if remaining <= 0:
                raise BoundedProcessError("subprocess exceeded its monotonic deadline")
            events = selector.select(remaining)
            if not events:
                raise BoundedProcessError("subprocess exceeded its monotonic deadline")
            for key, _mask in events:
                chunk = os.read(key.fd, 64 * 1024)
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                total += len(chunk)
                if total > max_output_bytes:
                    raise BoundedProcessError("subprocess output exceeded its byte ceiling")
                output[str(key.data)].extend(chunk)
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise BoundedProcessError("subprocess exceeded its monotonic deadline")
        returncode = process.wait(timeout=remaining)
    except (subprocess.TimeoutExpired, BoundedProcessError) as error:
        process.kill()
        process.wait()
        if isinstance(error, BoundedProcessError):
            raise
        raise BoundedProcessError("subprocess exceeded its monotonic deadline") from error
    finally:
        selector.close()
        process.stdout.close()
        process.stderr.close()
        if process.poll() is None:
            process.kill()
            process.wait()
    return subprocess.CompletedProcess(
        list(arguments),
        returncode,
        stdout=output["stdout"].decode("utf-8", "replace"),
        stderr=output["stderr"].decode("utf-8", "replace"),
    )


__all__ = ["BoundedProcessError", "run_bounded_process"]
