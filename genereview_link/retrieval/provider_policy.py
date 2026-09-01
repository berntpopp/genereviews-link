"""Fail-closed selection and honest reporting of the dense embedding provider.

A dense retriever only works when the query vector and the stored passage vectors come
from the *same* model. `FakeEmbeddingProvider` is a SHA-256 stream shaped like a vector:
its output is uncorrelated with real BGE vectors, so fusing its "dense ranks" with
lexical ranks does not degrade ranking gracefully -- it actively displaces correct
lexical hits with unrelated passages, while every response still reports the reference
model's identity. That is a silent-wrong-answer failure, and it is the reason this module
exists.

Three rules, all fail-closed:

1. **Production never runs a stub silently.** The stub is refused unless an operator sets
   ``GENEREVIEW_ALLOW_FAKE_EMBEDDINGS=true``, which is a deliberate, logged act.
2. **Never report a model that is not loaded.** ``dense_model_id`` is the live provider's
   own name, so a stub reports ``fake-embedding`` and never ``BAAI/bge-small-en-v1.5``.
3. **Query and corpus must agree.** A real provider whose model identity differs from the
   one the corpus was embedded with is refused outright, in every environment.

The flag this replaces, ``GENEREVIEW_EAGER_LOAD_BGE``, read as a performance knob ("eager
vs lazy loading") but actually selected real-vs-stub embeddings. It is still honoured for
compatibility; ``GENEREVIEW_EMBEDDING_PROVIDER`` is the name that says what it does.
"""

from __future__ import annotations

import asyncio
from typing import Any, Literal

from genereview_link.retrieval.embeddings import (
    EmbeddingProvider,
    FakeEmbeddingProvider,
    SentenceTransformerEmbeddingProvider,
)
from genereview_link.retrieval.model_identity import BGE_DIM, BGE_MODEL_NAME

#: ``onnx``  - the pinned BGE weights under ONNX Runtime. What the serving image runs.
#: ``torch`` - the same weights under sentence-transformers. Offline ingest/embedding only;
#:             PyTorch cannot fit the serving image's per-file size ceiling.
#: ``fake``  - a deterministic stub for tests. Not comparable with the corpus vectors.
ProviderKind = Literal["onnx", "torch", "fake"]

#: Accepted spellings, including the pre-ONNX alias.
_PROVIDER_ALIASES = {"onnx": "onnx", "bge": "onnx", "torch": "torch", "fake": "fake"}

#: Environments in which a stub provider is a hard error rather than a convenience.
PRODUCTION_ENVIRONMENTS = frozenset({"production", "prod"})

__all__ = [
    "PRODUCTION_ENVIRONMENTS",
    "EmbeddingPolicyError",
    "ProviderKind",
    "assert_corpus_model_agreement",
    "build_embedding_provider",
    "embedding_health",
    "is_production",
    "provider_is_real",
    "resolve_provider_kind",
]


class EmbeddingPolicyError(RuntimeError):
    """The configured embedding provider may not serve queries."""


def is_production(environment: str) -> bool:
    """Return whether *environment* names a production deployment."""
    return environment.strip().lower() in PRODUCTION_ENVIRONMENTS


def provider_is_real(provider: object) -> bool:
    """Return whether *provider* is the pinned reference embedding model.

    Both halves matter: a stub constructed with ``model_name=BGE_MODEL_NAME`` would pass
    a name check alone, and a future real provider must still carry the pinned identity.
    """
    if isinstance(provider, FakeEmbeddingProvider):
        return False
    return getattr(provider, "model_name", "") == BGE_MODEL_NAME


def resolve_provider_kind(settings: Any) -> ProviderKind:
    """Return which provider to build, honouring the explicit setting first.

    With no explicit choice, production resolves to the real model -- so the safe path is
    the default exactly where being wrong is expensive -- while other environments keep
    the historical stub default from ``GENEREVIEW_EAGER_LOAD_BGE`` so development and the
    test suite need no embedding stack.
    """
    configured = str(getattr(settings, "GENEREVIEW_EMBEDDING_PROVIDER", "")).strip().lower()
    if configured:
        resolved = _PROVIDER_ALIASES.get(configured)
        if resolved is None:
            raise EmbeddingPolicyError(
                "GENEREVIEW_EMBEDDING_PROVIDER must be 'onnx', 'torch' or 'fake', "
                f"not {configured!r}"
            )
        return resolved  # type: ignore[return-value]
    if is_production(settings.ENVIRONMENT):
        return "onnx"
    return "torch" if settings.GENEREVIEW_EAGER_LOAD_BGE else "fake"


async def build_embedding_provider(settings: Any) -> tuple[EmbeddingProvider, ProviderKind]:
    """Build the configured provider, refusing a stub where it must not run.

    In production the real model is loaded HERE rather than on the first query: an image
    that cannot load it must fail to start, visibly, instead of answering requests from a
    provider nobody chose.

    Raises:
        EmbeddingPolicyError: a stub provider is configured in production without an
            explicit opt-in.
        EmbeddingProviderUnavailableError: the real model or its runtime is not installed.
    """
    kind = resolve_provider_kind(settings)
    if kind == "fake":
        if is_production(settings.ENVIRONMENT) and not settings.GENEREVIEW_ALLOW_FAKE_EMBEDDINGS:
            raise EmbeddingPolicyError(
                "refusing to serve production traffic with the stub embedding provider: its "
                "query vectors are uncorrelated with the corpus vectors, so semantic ranking "
                "would be random while reporting the reference model. Set "
                "GENEREVIEW_EMBEDDING_PROVIDER=bge with the model available, or set "
                "GENEREVIEW_ALLOW_FAKE_EMBEDDINGS=true to knowingly serve lexical-only search."
            )
        return FakeEmbeddingProvider(dim=BGE_DIM), "fake"

    provider: EmbeddingProvider
    if kind == "onnx":
        from genereview_link.retrieval.onnx_embeddings import OnnxBgeEmbeddingProvider

        provider = OnnxBgeEmbeddingProvider(settings.MODEL_DIR)
    else:
        provider = SentenceTransformerEmbeddingProvider(device=settings.INGEST_EMBED_DEVICE)
    if is_production(settings.ENVIRONMENT):
        # Verify and load now. A missing or substituted model artifact must stop the
        # deployment, not surface on the first search after it reports itself healthy.
        await asyncio.to_thread(provider.ensure_ready)
    return provider, kind


def assert_corpus_model_agreement(provider: object, corpus_model: str | None) -> None:
    """Refuse to serve when a real provider disagrees with the corpus's model identity.

    Only real providers are checked: a stub is already barred from the dense path, so its
    (expected) disagreement is not a mismatch to act on.

    Raises:
        EmbeddingPolicyError: the corpus was embedded with a different model.
    """
    if not provider_is_real(provider) or not corpus_model:
        return
    provider_model = getattr(provider, "model_name", "")
    if corpus_model != provider_model:
        raise EmbeddingPolicyError(
            "embedding model mismatch: the corpus was embedded with "
            f"{corpus_model!r} but the server loaded {provider_model!r}. Query and passage "
            "vectors from different models are not comparable; refusing to serve."
        )


def embedding_health(state: Any) -> dict[str, Any]:
    """Summarise the live embedding provider for ``/health``.

    Defaults assume a real provider so that an app assembled outside the normal lifespan
    (unit tests, embedded use) is not reported as degraded on missing state alone.
    """
    real = bool(getattr(state, "embedding_provider_real", True))
    return {
        "provider": getattr(state, "embedding_provider_kind", "bge"),
        "model": getattr(state, "dense_model_id", None),
        "reference_model": BGE_MODEL_NAME,
        "is_reference_model": real,
        "dense_ranking": (
            "enabled" if getattr(state, "dense_ranking_enabled", True) else "disabled"
        ),
    }
