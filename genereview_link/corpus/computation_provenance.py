"""Capture immutable computation provenance when embeddings are computed."""

from __future__ import annotations

import contextlib
import hashlib
import importlib.metadata
import io
import json
import os
import platform
import shutil
import subprocess
from pathlib import Path
from typing import Any, cast

from genereview_link.corpus.pg_client import PG18_IMAGE
from genereview_link.retrieval.model_identity import (
    BGE_MODEL_FILES,
    BGE_MODEL_NAME,
    BGE_MODEL_REVISION,
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _command_version(executable: str) -> str:
    path = shutil.which(executable)
    if path is None:
        return "unavailable"
    result = subprocess.run(  # noqa: S603 - resolved executable, fixed version argument
        [path, "--version"], capture_output=True, text=True, check=False, timeout=10
    )
    return (result.stdout or result.stderr).strip() or "unavailable"


def _cpu_identity() -> str:
    try:
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            if line.casefold().startswith("model name"):
                return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return platform.processor() or platform.machine()


def collect_computation_provenance(*, app_git_sha: str) -> dict[str, object]:
    """Fail unless the complete reviewed runtime/model identity can be captured."""
    import numpy as np
    import torch
    from huggingface_hub import snapshot_download

    snapshot = Path(
        snapshot_download(BGE_MODEL_NAME, revision=BGE_MODEL_REVISION, local_files_only=True)
    )
    actual_files = {
        path.relative_to(snapshot).as_posix(): _sha256(path)
        for path in snapshot.rglob("*")
        if path.is_file()
    }
    if actual_files != BGE_MODEL_FILES:
        raise RuntimeError("reviewed embedding model snapshot file identity mismatch")
    root = Path(__file__).resolve().parents[2]
    distributions = sorted(
        {
            f"{distribution.metadata['Name'].casefold()}=={distribution.version}"
            for distribution in importlib.metadata.distributions()
            if distribution.metadata.get("Name")
        }
    )
    blas = io.StringIO()
    with contextlib.redirect_stdout(blas):
        np.show_config()
    cuda = str(torch.version.cuda or "none")
    cudnn_version: Any = torch.backends.cudnn.version
    cudnn = str(cast(int | None, cudnn_version()) or "none")
    device = "cuda" if torch.cuda.is_available() else "cpu"
    gpu = torch.cuda.get_device_name(0) if torch.cuda.is_available() else "none"
    builder_identity = (
        f"github-actions:{os.environ.get('GITHUB_REPOSITORY', '')}:"
        f"{os.environ.get('GITHUB_RUN_ID', '')}:{os.environ.get('GITHUB_RUN_ATTEMPT', '')}"
        if os.getenv("GITHUB_ACTIONS") == "true"
        else f"local:{platform.node()}"
    )
    return {
        "schema": "genereviews-computation-v2",
        "source": {"app_git_sha": app_git_sha, "builder_identity": builder_identity},
        "uv_lock_sha256": _sha256(root / "uv.lock"),
        "environment": {
            "installed_distributions": distributions,
            "installed_distributions_sha256": hashlib.sha256(_canonical(distributions)).hexdigest(),
            "uv_version": _command_version("uv"),
            "python": platform.python_version(),
            "os": platform.platform(),
            "kernel": platform.release(),
            "libc": " ".join(platform.libc_ver()),
            "cpu": _cpu_identity(),
            "blas": blas.getvalue().strip() or "unknown",
            "device": device,
            "gpu": gpu,
            "cuda": cuda,
            "cudnn": cudnn,
            "torch": str(torch.__version__),
            "sentence_transformers": importlib.metadata.version("sentence-transformers"),
            "transformers": importlib.metadata.version("transformers"),
            "build_backend": f"hatchling=={importlib.metadata.version('hatchling')}",
        },
        "database": {"client_image": PG18_IMAGE, "client_major": "18"},
        "model": {
            "name": BGE_MODEL_NAME,
            "revision": BGE_MODEL_REVISION,
            "files": BGE_MODEL_FILES,
        },
        "determinism": {
            "normalize_embeddings": True,
            "python_seed": 0,
            "numpy_seed": 0,
            "torch_seed": 0,
            "batch_size": int(os.getenv("INGEST_EMBED_BATCH_SIZE", "64")),
        },
        "embedding": {
            "model_name": BGE_MODEL_NAME,
            "model_revision": BGE_MODEL_REVISION,
            "table": "genereview_embeddings_bge384",
        },
    }


__all__ = ["collect_computation_provenance"]
