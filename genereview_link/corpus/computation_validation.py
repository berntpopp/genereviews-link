"""Strict validation of recorded ingest and embedding computation provenance."""

from __future__ import annotations

import hashlib
import json
import re

from genereview_link.corpus.pg_client import PG18_IMAGE
from genereview_link.retrieval.model_identity import (
    BGE_MODEL_FILES,
    BGE_MODEL_NAME,
    BGE_MODEL_REVISION,
)

_BASE_FIELDS = {
    "schema",
    "source",
    "uv_lock_sha256",
    "environment",
    "database",
    "model",
    "determinism",
    "embedding",
}
_ENVIRONMENT_FIELDS = {
    "installed_distributions",
    "installed_distributions_sha256",
    "uv_version",
    "python",
    "os",
    "kernel",
    "libc",
    "cpu",
    "blas",
    "device",
    "gpu",
    "cuda",
    "cudnn",
    "torch",
    "sentence_transformers",
    "transformers",
    "build_backend",
}


def validate_computation_provenance(
    value: object,
    *,
    app_git_sha: str,
    source_capture: dict[str, object] | None = None,
) -> dict[str, object]:
    """Require the complete exact reviewed computation contract."""
    fields = _BASE_FIELDS | ({"source_capture"} if source_capture is not None else set())
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError("computation provenance fields are incomplete")
    source = value["source"]
    environment = value["environment"]
    database = value["database"]
    determinism = value["determinism"]
    if (
        value["schema"] != "genereviews-computation-v2"
        or not re.fullmatch(r"[0-9a-f]{64}", str(value["uv_lock_sha256"]))
        or not isinstance(source, dict)
        or set(source) != {"app_git_sha", "builder_identity"}
        or source["app_git_sha"] != app_git_sha
        or not isinstance(source["builder_identity"], str)
        or not source["builder_identity"]
        or value["model"]
        != {"name": BGE_MODEL_NAME, "revision": BGE_MODEL_REVISION, "files": BGE_MODEL_FILES}
        or value["embedding"]
        != {
            "model_name": BGE_MODEL_NAME,
            "model_revision": BGE_MODEL_REVISION,
            "table": "genereview_embeddings_bge384",
        }
    ):
        raise ValueError("computation source/model identity is invalid")
    if source_capture is not None and value["source_capture"] != source_capture:
        raise ValueError("ingest provenance source capture is not exact")
    if not isinstance(environment, dict) or set(environment) != _ENVIRONMENT_FIELDS:
        raise ValueError("computation environment identity is incomplete")
    distributions = environment["installed_distributions"]
    if (
        not isinstance(distributions, list)
        or distributions != sorted(distributions)
        or not distributions
        or not all(isinstance(item, str) and "==" in item for item in distributions)
        or hashlib.sha256(
            (json.dumps(distributions, sort_keys=True, separators=(",", ":")) + "\n").encode()
        ).hexdigest()
        != environment["installed_distributions_sha256"]
        or any(
            not isinstance(environment[name], str) or not environment[name]
            for name in _ENVIRONMENT_FIELDS - {"installed_distributions"}
        )
        or environment["device"] not in {"cpu", "cuda"}
    ):
        raise ValueError("computation environment identity is invalid")
    if (
        not isinstance(database, dict)
        or set(database)
        != {"client_image", "client_major", "server_version_num", "server_major", "pgvector"}
        or database["client_image"] != PG18_IMAGE
        or database["client_major"] != "18"
        or database["server_major"] != "18"
        or database["pgvector"] != "0.8.2"
        or not re.fullmatch(r"18[0-9]{4}", str(database["server_version_num"]))
    ):
        raise ValueError("computation PostgreSQL identity is invalid")
    if (
        not isinstance(determinism, dict)
        or set(determinism)
        != {
            "normalize_embeddings",
            "python_seed",
            "numpy_seed",
            "torch_seed",
            "batch_size",
        }
        or determinism["normalize_embeddings"] is not True
        or determinism["python_seed"] != 0
        or determinism["numpy_seed"] != 0
        or determinism["torch_seed"] != 0
        or type(determinism["batch_size"]) is not int
        or determinism["batch_size"] <= 0
    ):
        raise ValueError("computation determinism identity is invalid")
    return value


__all__ = ["validate_computation_provenance"]
