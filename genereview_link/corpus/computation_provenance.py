"""Exact local computation provenance for a publishable corpus."""

from __future__ import annotations

import hashlib
import importlib.metadata
import os
import platform
from pathlib import Path

from genereview_link.retrieval.model_identity import (
    BGE_MODEL_FILE,
    BGE_MODEL_FILE_SHA256,
    BGE_MODEL_NAME,
    BGE_MODEL_REVISION,
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def collect_computation_provenance(*, app_git_sha: str) -> dict[str, object]:
    """Fail unless the exact reviewed model snapshot is already present and intact."""
    import torch
    from huggingface_hub import snapshot_download

    snapshot = Path(
        snapshot_download(
            BGE_MODEL_NAME,
            revision=BGE_MODEL_REVISION,
            local_files_only=True,
        )
    )
    model_file = snapshot / BGE_MODEL_FILE
    if _sha256(model_file) != BGE_MODEL_FILE_SHA256:
        raise RuntimeError("reviewed embedding model file digest mismatch")
    root = Path(__file__).resolve().parents[2]
    uv_lock = root / "uv.lock"
    device = "cuda" if torch.cuda.is_available() else "cpu"
    builder_identity = (
        f"github-actions:{os.environ.get('GITHUB_REPOSITORY', '')}:"
        f"{os.environ.get('GITHUB_RUN_ID', '')}:{os.environ.get('GITHUB_RUN_ATTEMPT', '')}"
        if os.getenv("GITHUB_ACTIONS") == "true"
        else f"local:{platform.node()}"
    )
    return {
        "uv_lock_sha256": _sha256(uv_lock),
        "model": {
            "name": BGE_MODEL_NAME,
            "revision": BGE_MODEL_REVISION,
            "files": {BGE_MODEL_FILE: BGE_MODEL_FILE_SHA256},
        },
        "runtime": {
            "python": platform.python_version(),
            "torch": str(torch.__version__),
            "sentence_transformers": importlib.metadata.version("sentence-transformers"),
            "transformers": importlib.metadata.version("transformers"),
            "device": device,
        },
        "determinism": {
            "normalize_embeddings": True,
            "python_seed": 0,
            "numpy_seed": 0,
            "torch_seed": 0,
            "batch_size": int(os.getenv("INGEST_EMBED_BATCH_SIZE", "64")),
        },
        "builder": {"source_sha": app_git_sha, "identity": builder_identity},
        "embedding": {
            "model_name": BGE_MODEL_NAME,
            "model_revision": BGE_MODEL_REVISION,
            "table": "genereview_embeddings_bge384",
        },
    }


__all__ = ["collect_computation_provenance"]
