"""Embedding provider for BGE-small-en-v1.5.

Lifted from pubtator-link/pubtator_link/services/review_context/embeddings.py
with project renames.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from typing import Any, Protocol, cast

logger = logging.getLogger(__name__)

BGE_QUERY_PREFIX = "Represent this sentence for searching relevant passages: "


class EmbeddingProviderUnavailableError(RuntimeError):
    """Raised when optional embedding deps are not installed."""


class EmbeddingProvider(Protocol):
    model_name: str
    dim: int

    async def embed_query(self, text: str) -> list[float]: ...
    async def embed_passages(self, texts: list[str]) -> list[list[float]]: ...


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def bge_query_text(text: str) -> str:
    return f"{BGE_QUERY_PREFIX}{text}"


def bge_passage_text(
    text: str,
    *,
    passage_type: str = "narrative",
    max_tokens: int = 480,
) -> str:
    """Prepare a passage for BGE embedding input.

    For ``passage_type == "table"`` the text is truncated so that the
    caption, header row, and separator are preserved while body rows are
    kept only up to the token budget (approximated as ``max_tokens * 4``
    characters).  All other passage types are returned unchanged — they
    are already token-budgeted at chunk time.
    """
    if passage_type != "table":
        return text

    lines = text.split("\n")
    # Need at least: caption (0), blank (1), header (2), separator (3).
    if len(lines) < 4:
        return text

    caption = lines[0]
    header = lines[2]
    separator = lines[3]
    body_rows = lines[4:]

    # Conservative char-proxy: ~4 chars per token.
    budget_chars = max_tokens * 4
    keep: list[str] = [caption, "", header, separator]
    used = sum(len(line) for line in keep)
    for row in body_rows:
        if used + len(row) > budget_chars:
            break
        keep.append(row)
        used += len(row)

    result = "\n".join(keep)
    # Only return the truncated version if it is actually shorter.
    if len(result) >= len(text):
        return text
    return result


class FakeEmbeddingProvider:
    """Deterministic fake — for tests."""

    def __init__(self, *, dim: int, model_name: str = "fake-embedding") -> None:
        self.model_name = model_name
        self.dim = dim

    async def embed_query(self, text: str) -> list[float]:
        return self._embed_one(bge_query_text(text))

    async def embed_passages(self, texts: list[str]) -> list[list[float]]:
        return [self._embed_one(bge_passage_text(t)) for t in texts]

    def _embed_one(self, text: str) -> list[float]:
        seed = hashlib.sha256(text.encode("utf-8")).digest()
        values: list[float] = []
        counter = 0
        while len(values) < self.dim:
            digest = hashlib.sha256(seed + counter.to_bytes(4, "big")).digest()
            values.extend((byte / 127.5) - 1.0 for byte in digest)
            counter += 1
        return values[: self.dim]


class SentenceTransformerEmbeddingProvider:
    """Real BGE-small provider — lazy-loaded."""

    def __init__(
        self,
        *,
        model_name: str = "BAAI/bge-small-en-v1.5",
        dim: int = 384,
        device: str = "auto",
    ) -> None:
        self.model_name = model_name
        self.dim = dim
        self.device = device
        self._model: Any | None = None
        self._np: Any | None = None

    async def embed_query(self, text: str) -> list[float]:
        vectors = await self._encode([bge_query_text(text)])
        return vectors[0]

    async def embed_passages(self, texts: list[str]) -> list[list[float]]:
        return await self._encode([bge_passage_text(t) for t in texts])

    async def _encode(self, texts: list[str]) -> list[list[float]]:
        model, np = self._ensure_model()

        def encode() -> list[list[float]]:
            vectors = model.encode(texts, normalize_embeddings=True)
            return cast(list[list[float]], np.asarray(vectors, dtype=float).tolist())

        return await asyncio.to_thread(encode)

    def _ensure_model(self) -> tuple[Any, Any]:
        if self._model is not None and self._np is not None:
            return self._model, self._np
        try:
            import numpy as np
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise EmbeddingProviderUnavailableError(
                "Install sentence-transformers + numpy to use BGE embeddings."
            ) from exc
        self._np = np
        device = None if self.device == "auto" else self.device
        self._model = SentenceTransformer(self.model_name, device=device)
        resolved = getattr(self._model, "device", None)
        logger.info(
            "loaded embedding model %s on device=%s (requested=%s)",
            self.model_name,
            resolved,
            self.device,
        )
        return self._model, self._np
