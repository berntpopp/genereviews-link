"""BGE-small-en-v1.5 inference for the serving container, via ONNX Runtime.

The corpus is embedded with `BAAI/bge-small-en-v1.5` under sentence-transformers, which
needs PyTorch. PyTorch cannot be in the serving image: its wheel is 526 MB and a single
`libtorch_cpu.so` far exceeds the fleet OCI content policy's 64 MiB per-file ceiling.
That constraint is real, but it only bars *PyTorch* -- not the model.

This module runs the **same weights** through ONNX Runtime, whose largest file is 30 MB.
The weights themselves (133 MB) are far too large for the image too, so they arrive the way
every other large artifact in this fleet arrives: as a digest-pinned release asset, staged
on the host and mounted read-only. Nothing is downloaded at runtime.

Parity with the reference implementation is measured, not assumed:
`tests/unit/test_onnx_embedding_parity.py` compares both paths and requires
cosine >= 0.999999 -- observed minimum 0.999999999796, max per-dimension delta 1.75e-07.

Two properties are load-bearing:

* **The artifact cannot change how it is interpreted.** CLS pooling, L2 normalisation, the
  512-token limit and the query prefix are pinned in reviewed code. Only tensor weights
  come from the file.
* **The artifact is proven before it is opened.** Both members are checked against the
  digests in `model_identity` first, so a substituted model never reaches the ONNX parser.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import logging
from pathlib import Path
from typing import Any

from genereview_link.retrieval.embeddings import (
    EmbeddingProviderUnavailableError,
    bge_passage_text,
    bge_query_text,
)
from genereview_link.retrieval.model_identity import (
    BGE_DIM,
    BGE_MAX_SEQ_LENGTH,
    BGE_MODEL_NAME,
    BGE_MODEL_REVISION,
    BGE_ONNX_FILE,
    BGE_RUNTIME_FILES,
    BGE_TOKENIZER_FILE,
)

logger = logging.getLogger(__name__)

__all__ = ["ModelArtifactError", "OnnxBgeEmbeddingProvider", "verify_model_dir"]


class ModelArtifactError(RuntimeError):
    """The staged model artifact is absent, incomplete, or not the reviewed model."""


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_model_dir(model_dir: Path) -> dict[str, str]:
    """Prove the staged model is exactly the reviewed one, or refuse it.

    Returns the verified `{member: digest}` map so a caller can log or report it.

    Raises:
        ModelArtifactError: a member is missing, is not a regular file, or its digest does
            not match the identity pinned in `model_identity`.
    """
    if not model_dir.is_dir():
        raise ModelArtifactError(
            f"the model directory {model_dir} is not present; stage the reviewed model "
            "release asset and mount it read-only (see docs/data.md)"
        )
    verified: dict[str, str] = {}
    for member, expected in sorted(BGE_RUNTIME_FILES.items()):
        path = model_dir / member
        if not path.is_file() or path.is_symlink():
            raise ModelArtifactError(f"the model artifact is missing a regular {member}")
        actual = _sha256_file(path)
        if actual != expected:
            raise ModelArtifactError(
                f"{member} does not match the reviewed model identity "
                f"({BGE_MODEL_NAME} @ {BGE_MODEL_REVISION}); refusing to load it"
            )
        verified[member] = actual
    return verified


class OnnxBgeEmbeddingProvider:
    """The pinned BGE model, run offline through ONNX Runtime."""

    def __init__(self, model_dir: str | Path) -> None:
        self.model_dir = Path(model_dir)
        self.model_name = BGE_MODEL_NAME
        self.model_revision = BGE_MODEL_REVISION
        self.dim = BGE_DIM
        self._session: Any | None = None
        self._tokenizer: Any | None = None
        self._np: Any | None = None
        self._token_type_ids = False

    def ensure_ready(self) -> None:
        """Verify and load the model now, so a bad artifact fails at startup.

        Blocking and synchronous; callers in an event loop should use a worker thread.

        Raises:
            ModelArtifactError: the staged artifact is not the reviewed model.
            EmbeddingProviderUnavailableError: the ONNX runtime is not installed.
        """
        self._load()

    def _load(self) -> tuple[Any, Any, Any]:
        if self._session is not None and self._tokenizer is not None and self._np is not None:
            return self._session, self._tokenizer, self._np
        try:
            import numpy as np
            import onnxruntime as ort
            from tokenizers import Tokenizer
        except ImportError as exc:  # pragma: no cover - exercised by the packaging gate
            raise EmbeddingProviderUnavailableError(
                "install onnxruntime + tokenizers to run the BGE embedding model"
            ) from exc

        verified = verify_model_dir(self.model_dir)

        tokenizer = Tokenizer.from_file(str(self.model_dir / BGE_TOKENIZER_FILE))
        tokenizer.enable_truncation(max_length=BGE_MAX_SEQ_LENGTH)
        tokenizer.enable_padding(pad_id=0, pad_token="[PAD]")  # noqa: S106

        # ONNX Runtime otherwise tries to persist a telemetry device id, which a
        # read-only rootfs refuses noisily. Turning it off keeps the no-egress posture
        # unambiguous and the logs clean.
        with contextlib.suppress(AttributeError, RuntimeError):
            ort.disable_telemetry_events()

        options = ort.SessionOptions()
        # One process, one model: keep threads bounded so the container honours its CPU cap.
        options.intra_op_num_threads = 1
        options.inter_op_num_threads = 1
        session = ort.InferenceSession(
            str(self.model_dir / BGE_ONNX_FILE),
            sess_options=options,
            providers=["CPUExecutionProvider"],
        )
        self._token_type_ids = any(i.name == "token_type_ids" for i in session.get_inputs())

        self._session, self._tokenizer, self._np = session, tokenizer, np
        logger.info(
            "loaded verified ONNX embedding model %s @ %s (%s)",
            BGE_MODEL_NAME,
            BGE_MODEL_REVISION,
            ", ".join(f"{name}={digest[:12]}" for name, digest in sorted(verified.items())),
        )
        return session, tokenizer, np

    async def embed_query(self, text: str) -> list[float]:
        vectors = await self._encode([bge_query_text(text)])
        return vectors[0]

    async def embed_passages(self, texts: list[str]) -> list[list[float]]:
        return await self._encode([bge_passage_text(t) for t in texts])

    async def _encode(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        return await asyncio.to_thread(self._encode_sync, texts)

    def _encode_sync(self, texts: list[str]) -> list[list[float]]:
        session, tokenizer, np = self._load()
        encoded = tokenizer.encode_batch(texts)
        feed: dict[str, Any] = {
            "input_ids": np.array([e.ids for e in encoded], dtype=np.int64),
            "attention_mask": np.array([e.attention_mask for e in encoded], dtype=np.int64),
        }
        if self._token_type_ids:
            feed["token_type_ids"] = np.array([e.type_ids for e in encoded], dtype=np.int64)
        hidden = session.run(None, feed)[0]
        # CLS pooling then L2 normalisation -- pinned in reviewed code, never read from the
        # artifact. `1_Pooling/config.json` sets pooling_mode_cls_token and `modules.json`
        # appends 2_Normalize; both are asserted in the parity test.
        pooled = hidden[:, 0, :].astype(np.float32)
        norms = np.linalg.norm(pooled, axis=1, keepdims=True)
        normalized = pooled / np.maximum(norms, 1e-12)
        return [row.tolist() for row in normalized]
