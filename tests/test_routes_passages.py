"""Unit tests for /passages/search using TestClient + dependency overrides."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from genereview_link.api.client_manager import get_managed_client
from genereview_link.api.routes.passages import get_embedding_provider, get_repository
from genereview_link.config import ServerConfig
from genereview_link.retrieval.embeddings import FakeEmbeddingProvider
from genereview_link.retrieval.repository import (
    GeneReviewRepository,
    LexicalPassageRow,
    PassageRow,
)
from genereview_link.server_manager import UnifiedServerManager


class FakeClient:
    async def search_genereviews(self, *a: Any, **kw: Any) -> dict:
        return {"count": 0, "retmax": 20, "retstart": 0, "ids": [], "webenv": "", "querykey": ""}

    async def fetch_abstract(self, *a: Any, **kw: Any) -> dict:
        return {}

    async def get_all_links(self, *a: Any, **kw: Any) -> dict:
        return {"urls": []}

    async def scrape_genereview_comprehensive(self, *a: Any, **kw: Any) -> dict:
        return {"nbk_id": "1", "url": "", "title": "", "sections": {}, "metadata": {}}


def _make_passage_row(passage_id: str = "p1") -> PassageRow:
    return PassageRow(
        nbk_id="NBK1",
        passage_id=passage_id,
        chapter_section="summary",
        heading_path="Summary",
        section_level=1,
        chunk_index=0,
        text="This is a test passage about BRCA1.",
    )


def _make_lexical_row(passage_id: str = "p1") -> LexicalPassageRow:
    return LexicalPassageRow(
        passage=_make_passage_row(passage_id),
        phrase_rank=0.5,
        strict_rank=0.4,
        recall_rank=0.3,
        recall_overlap_count=2,
        lexical_rank=0.6,
        gene_symbols=("BRCA1",),
    )


@pytest.fixture
def fake_repo() -> GeneReviewRepository:
    repo = AsyncMock(spec=GeneReviewRepository)
    repo.search_passages.return_value = [_make_lexical_row()]
    repo.active_embedding_table.return_value = "genereview_embeddings_bge384"
    repo.dense_scores_for_passages.return_value = {"p1": 0.85}
    return repo


@pytest.fixture
def fake_embedder() -> FakeEmbeddingProvider:
    return FakeEmbeddingProvider(dim=384)


@pytest_asyncio.fixture
async def app(fake_repo: GeneReviewRepository, fake_embedder: FakeEmbeddingProvider) -> FastAPI:
    config = ServerConfig(transport="http", log_level="WARNING", enable_docs=False)
    manager = UnifiedServerManager()
    fastapi_app = manager.create_fastapi_app(config)

    async def _get_client() -> Any:
        yield FakeClient()

    async def _get_repo() -> GeneReviewRepository:
        return fake_repo

    async def _get_embedder() -> FakeEmbeddingProvider:
        return fake_embedder

    fastapi_app.dependency_overrides[get_managed_client] = _get_client
    fastapi_app.dependency_overrides[get_repository] = _get_repo
    fastapi_app.dependency_overrides[get_embedding_provider] = _get_embedder
    return fastapi_app


@pytest_asyncio.fixture
async def http_client(app: FastAPI) -> AsyncClient:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


class TestPassagesSearchRoute:
    @pytest.mark.asyncio
    async def test_returns_ranked_passage(self, http_client: AsyncClient) -> None:
        resp = await http_client.get("/passages/search?q=BRCA1+diagnosis")
        assert resp.status_code == 200
        body = resp.json()
        assert isinstance(body, list)
        assert len(body) == 1
        p = body[0]
        assert p["passage_id"] == "p1"
        assert p["nbk_id"] == "NBK1"
        assert p["chapter_section"] == "summary"
        assert "score_breakdown" in p
        assert p["score_breakdown"]["final_position"] == 1

    @pytest.mark.asyncio
    async def test_missing_q_returns_422(self, http_client: AsyncClient) -> None:
        resp = await http_client.get("/passages/search")
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_limit_param(self, http_client: AsyncClient, fake_repo: Any) -> None:
        fake_repo.search_passages.return_value = [_make_lexical_row(f"p{i}") for i in range(10)]
        fake_repo.dense_scores_for_passages.return_value = {
            f"p{i}": 0.9 - i * 0.05 for i in range(10)
        }
        resp = await http_client.get("/passages/search?q=test&limit=3")
        assert resp.status_code == 200
        assert len(resp.json()) <= 3

    @pytest.mark.asyncio
    async def test_rerank_lexical(self, http_client: AsyncClient) -> None:
        resp = await http_client.get("/passages/search?q=BRCA1&rerank=lexical")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_503_when_repository_not_set(
        self, app: FastAPI, http_client: AsyncClient
    ) -> None:
        # Remove override to simulate missing repository
        async def _no_repo() -> None:
            from fastapi import HTTPException

            raise HTTPException(
                status_code=503,
                detail="DATABASE_URL not configured — Postgres repository unavailable",
            )

        app.dependency_overrides[get_repository] = _no_repo
        resp = await http_client.get("/passages/search?q=test")
        assert resp.status_code == 503
