"""Fetch the reviewed embedding model and stage it for the init sidecar.

Offline maintainer/CI tooling: it is the only place that reaches the network for the
model, and it is pruned from the serving image. The serving container never fetches
anything -- it reads two verified files from a volume.

The trust root is `model_identity`, not the download host. Bytes are proven against the
digests committed in this repository before they are written to the staging directory, so
a tampered mirror produces a failure rather than a staged model.
"""

from __future__ import annotations

import hashlib
import tempfile
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from genereview_link.retrieval.model_identity import (
    BGE_MODEL_NAME,
    BGE_MODEL_REVISION,
    BGE_ONNX_FILE,
    BGE_ONNX_FILE_SHA256,
    BGE_TOKENIZER_FILE,
    BGE_TOKENIZER_FILE_SHA256,
)

__all__ = ["ModelStageError", "stage_model"]

#: The model host, and the CDN a Hub download redirects to. Nothing else is fetched.
_ALLOWED_HOSTS = frozenset(
    {"huggingface.co", "cdn-lfs.huggingface.co", "cdn-lfs-us-1.hf.co", "us.aws.cdn.hf.co"}
)

#: Repository-relative source paths for the two runtime members.
_SOURCES = {
    BGE_ONNX_FILE: (f"onnx/{BGE_ONNX_FILE}", BGE_ONNX_FILE_SHA256),
    BGE_TOKENIZER_FILE: (BGE_TOKENIZER_FILE, BGE_TOKENIZER_FILE_SHA256),
}

_MAX_BYTES = 512 * 1024**2


class ModelStageError(RuntimeError):
    """The model could not be staged from a trusted source with the reviewed identity."""


def _guarded_open(url: str) -> Any:
    parts = urlsplit(url)
    if parts.scheme != "https" or parts.hostname not in _ALLOWED_HOSTS:
        raise ModelStageError(f"refusing to fetch the model from {parts.hostname!r}")
    return urllib.request.urlopen(url, timeout=300)  # noqa: S310 - scheme/host checked above


def stage_model(destination: Path) -> dict[str, str]:
    """Download the pinned model members into *destination*, proving each digest.

    Returns the `{member: digest}` map actually written.

    Raises:
        ModelStageError: a download exceeded its ceiling or did not match the reviewed
            identity committed in this repository.
    """
    base = f"https://huggingface.co/{BGE_MODEL_NAME}/resolve/{BGE_MODEL_REVISION}"
    destination.mkdir(parents=True, exist_ok=True)
    written: dict[str, str] = {}

    for member, (source, expected) in sorted(_SOURCES.items()):
        target = destination / member
        if target.is_file() and hashlib.sha256(target.read_bytes()).hexdigest() == expected:
            written[member] = expected
            continue

        digest = hashlib.sha256()
        total = 0
        handle, staged_name = tempfile.mkstemp(prefix=f".{member}.", dir=destination)
        staged = Path(staged_name)
        try:
            with open(handle, "wb") as sink, _guarded_open(f"{base}/{source}") as response:
                while chunk := response.read(1024 * 1024):
                    total += len(chunk)
                    if total > _MAX_BYTES:
                        raise ModelStageError(f"{member} exceeded its download ceiling")
                    digest.update(chunk)
                    sink.write(chunk)
            if digest.hexdigest() != expected:
                raise ModelStageError(
                    f"{member} does not match the reviewed model identity "
                    f"({BGE_MODEL_NAME} @ {BGE_MODEL_REVISION}); refusing to stage it"
                )
            staged.replace(target)
            staged = None  # type: ignore[assignment]
        finally:
            if staged is not None:
                staged.unlink(missing_ok=True)
        written[member] = expected
    return written
