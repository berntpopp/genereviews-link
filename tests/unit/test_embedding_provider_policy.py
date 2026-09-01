"""The embedding provider must fail closed, and must never misreport what it loaded."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI

from genereview_link.config import ServerConfig
from genereview_link.retrieval.embeddings import (
    FakeEmbeddingProvider,
    SentenceTransformerEmbeddingProvider,
)
from genereview_link.retrieval.model_identity import BGE_DIM, BGE_MODEL_NAME
from genereview_link.retrieval.provider_policy import (
    EmbeddingPolicyError,
    assert_corpus_model_agreement,
    build_embedding_provider,
    embedding_health,
    provider_is_real,
    resolve_provider_kind,
)
from genereview_link.server_manager import UnifiedServerManager


def _settings(**overrides: Any) -> SimpleNamespace:
    base = {
        "ENVIRONMENT": "development",
        "GENEREVIEW_EMBEDDING_PROVIDER": "",
        "GENEREVIEW_ALLOW_FAKE_EMBEDDINGS": False,
        "GENEREVIEW_EAGER_LOAD_BGE": False,
        "INGEST_EMBED_DEVICE": "cpu",
        "MODEL_DIR": "/var/lib/genereview/models",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


# --- provider identity ------------------------------------------------------------


def test_a_stub_is_never_real_even_wearing_the_reference_name() -> None:
    """Name alone must not certify a provider: the class is checked too."""
    disguised = FakeEmbeddingProvider(dim=BGE_DIM, model_name=BGE_MODEL_NAME)
    assert not provider_is_real(disguised)
    assert not provider_is_real(FakeEmbeddingProvider(dim=BGE_DIM))
    assert provider_is_real(SentenceTransformerEmbeddingProvider())


# --- selection --------------------------------------------------------------------


def test_production_defaults_to_the_real_model_without_any_configuration() -> None:
    """The safe path is the default exactly where being wrong is expensive."""
    assert resolve_provider_kind(_settings(ENVIRONMENT="production")) == "onnx"


def test_development_keeps_the_historical_stub_default() -> None:
    assert resolve_provider_kind(_settings()) == "fake"
    assert resolve_provider_kind(_settings(GENEREVIEW_EAGER_LOAD_BGE=True)) == "torch"


def test_the_pre_onnx_spelling_still_selects_the_real_model() -> None:
    """`bge` named the real model before there were two ways to run it."""
    assert resolve_provider_kind(_settings(GENEREVIEW_EMBEDDING_PROVIDER="bge")) == "onnx"


def test_the_explicit_setting_wins_over_the_legacy_flag() -> None:
    settings = _settings(GENEREVIEW_EMBEDDING_PROVIDER="fake", GENEREVIEW_EAGER_LOAD_BGE=True)
    assert resolve_provider_kind(settings) == "fake"


def test_an_unknown_provider_name_is_refused_rather_than_guessed() -> None:
    with pytest.raises(EmbeddingPolicyError, match="must be 'onnx', 'torch' or 'fake'"):
        resolve_provider_kind(_settings(GENEREVIEW_EMBEDDING_PROVIDER="bge-large"))


# --- fail closed ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_production_refuses_to_start_with_a_stub_provider() -> None:
    settings = _settings(ENVIRONMENT="production", GENEREVIEW_EMBEDDING_PROVIDER="fake")
    with pytest.raises(EmbeddingPolicyError, match="refusing to serve production traffic"):
        await build_embedding_provider(settings)


@pytest.mark.asyncio
async def test_production_admits_a_stub_only_on_an_explicit_opt_in() -> None:
    settings = _settings(
        ENVIRONMENT="production",
        GENEREVIEW_EMBEDDING_PROVIDER="fake",
        GENEREVIEW_ALLOW_FAKE_EMBEDDINGS=True,
    )
    provider, kind = await build_embedding_provider(settings)
    assert kind == "fake"
    assert not provider_is_real(provider)


@pytest.mark.asyncio
async def test_development_may_use_a_stub_without_ceremony() -> None:
    provider, kind = await build_embedding_provider(_settings())
    assert kind == "fake"
    assert isinstance(provider, FakeEmbeddingProvider)
    assert provider.dim == BGE_DIM


@pytest.mark.asyncio
async def test_production_loads_the_real_model_at_startup_not_on_first_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stripped image must fail to START, not answer requests from a stub instead."""
    loaded: list[str] = []

    def _record(self: SentenceTransformerEmbeddingProvider) -> None:
        loaded.append(self.model_name)

    monkeypatch.setattr(SentenceTransformerEmbeddingProvider, "ensure_ready", _record)
    settings = _settings(ENVIRONMENT="production", GENEREVIEW_EMBEDDING_PROVIDER="torch")

    provider, kind = await build_embedding_provider(settings)

    assert kind == "torch"
    assert loaded == [BGE_MODEL_NAME]
    assert provider_is_real(provider)


@pytest.mark.asyncio
async def test_a_missing_embedding_runtime_is_a_startup_failure_in_production(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """This is the live image's actual state: torch and sentence-transformers are pruned."""
    from genereview_link.retrieval.embeddings import EmbeddingProviderUnavailableError

    def _unavailable(self: SentenceTransformerEmbeddingProvider) -> None:
        raise EmbeddingProviderUnavailableError("sentence-transformers is not installed")

    monkeypatch.setattr(SentenceTransformerEmbeddingProvider, "ensure_ready", _unavailable)
    settings = _settings(ENVIRONMENT="production", GENEREVIEW_EMBEDDING_PROVIDER="torch")

    with pytest.raises(EmbeddingProviderUnavailableError):
        await build_embedding_provider(settings)


# --- query/corpus model agreement -------------------------------------------------


def test_a_real_provider_disagreeing_with_the_corpus_is_refused() -> None:
    with pytest.raises(EmbeddingPolicyError, match="embedding model mismatch"):
        assert_corpus_model_agreement(
            SentenceTransformerEmbeddingProvider(), "BAAI/bge-base-en-v1.5"
        )


def test_agreement_passes_for_the_pinned_model() -> None:
    assert_corpus_model_agreement(SentenceTransformerEmbeddingProvider(), BGE_MODEL_NAME)


def test_an_unknown_corpus_model_is_not_treated_as_a_mismatch() -> None:
    """Absent evidence is not evidence of disagreement; the corpus gate covers absence."""
    assert_corpus_model_agreement(SentenceTransformerEmbeddingProvider(), None)
    assert_corpus_model_agreement(SentenceTransformerEmbeddingProvider(), "")


def test_a_stub_is_not_subject_to_the_mismatch_check() -> None:
    """Its dense path is already disabled, so its expected disagreement is not news."""
    assert_corpus_model_agreement(FakeEmbeddingProvider(dim=BGE_DIM), BGE_MODEL_NAME)


# --- health -----------------------------------------------------------------------


def test_health_reports_degraded_while_a_stub_is_active() -> None:
    state = SimpleNamespace(
        embedding_provider_kind="fake",
        embedding_provider_real=False,
        dense_ranking_enabled=False,
        dense_model_id="fake-embedding",
    )
    health = embedding_health(state)
    assert health["is_reference_model"] is False
    assert health["dense_ranking"] == "disabled"
    assert health["model"] == "fake-embedding"
    assert health["model"] != BGE_MODEL_NAME


def _health_with_provider(**state: Any) -> dict[str, Any]:
    """GET /health from a live app whose provider state is set inside the lifespan."""
    from fastapi.testclient import TestClient

    config = ServerConfig(transport="http", log_level="WARNING", enable_docs=False)
    app: FastAPI = UnifiedServerManager().create_fastapi_app(config)
    with TestClient(app) as client:
        # After startup, so the real lifespan's own selection does not overwrite it.
        for key, value in state.items():
            setattr(app.state, key, value)
        body: dict[str, Any] = client.get("/health").json()
    return body


def test_the_health_endpoint_degrades_rather_than_reporting_ok() -> None:
    """A green health check while ranking is random is why this survived for months."""
    body = _health_with_provider(
        embedding_provider_kind="fake",
        embedding_provider_real=False,
        dense_ranking_enabled=False,
        dense_model_id="fake-embedding",
    )

    assert body["status"] == "degraded"
    assert body["embeddings"]["is_reference_model"] is False
    assert body["embeddings"]["dense_ranking"] == "disabled"
    assert body["embeddings"]["model"] != BGE_MODEL_NAME


def test_the_health_endpoint_stays_healthy_with_a_real_provider() -> None:
    body = _health_with_provider(
        embedding_provider_kind="bge",
        embedding_provider_real=True,
        dense_ranking_enabled=True,
        dense_model_id=BGE_MODEL_NAME,
    )

    assert body["status"] == "healthy"
    assert body["embeddings"]["model"] == BGE_MODEL_NAME
    assert body["embeddings"]["dense_ranking"] == "enabled"


@pytest.mark.asyncio
async def test_production_selects_the_onnx_provider_and_verifies_the_artifact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Production runs the ONNX provider, and a missing model artifact stops startup."""
    from genereview_link.retrieval.onnx_embeddings import (
        ModelArtifactError,
        OnnxBgeEmbeddingProvider,
    )

    settings = _settings(ENVIRONMENT="production", MODEL_DIR="/nonexistent/models")
    assert resolve_provider_kind(settings) == "onnx"

    seen: list[str] = []

    def _record(self: OnnxBgeEmbeddingProvider) -> None:
        seen.append(str(self.model_dir))

    monkeypatch.setattr(OnnxBgeEmbeddingProvider, "ensure_ready", _record)
    provider, kind = await build_embedding_provider(settings)
    assert kind == "onnx"
    assert seen == ["/nonexistent/models"]
    assert provider_is_real(provider)

    # ...and without the monkeypatch, the absent artifact is a hard startup failure.
    monkeypatch.undo()
    with pytest.raises(ModelArtifactError):
        await build_embedding_provider(settings)
