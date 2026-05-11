"""Unit tests for /chapters/{nbk}/sections/{section} using TestClient + dependency overrides."""

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
from genereview_link.retrieval.repository import GeneReviewRepository, PassageRow
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


def _make_passages() -> list[PassageRow]:
    return [
        PassageRow(
            nbk_id="NBK1247",
            passage_id="p1",
            chapter_section="summary",
            heading_path="Summary",
            section_level=1,
            chunk_index=0,
            text="First chunk of the summary section.",
        ),
        PassageRow(
            nbk_id="NBK1247",
            passage_id="p2",
            chapter_section="summary",
            heading_path="Summary",
            section_level=1,
            chunk_index=1,
            text="Second chunk of the summary section.",
        ),
    ]


@pytest.fixture
def fake_repo() -> GeneReviewRepository:
    repo = AsyncMock(spec=GeneReviewRepository)
    repo.get_section.return_value = _make_passages()
    return repo


@pytest_asyncio.fixture
async def app(fake_repo: GeneReviewRepository) -> FastAPI:
    config = ServerConfig(transport="http", log_level="WARNING", enable_docs=False)
    manager = UnifiedServerManager()
    fastapi_app = manager.create_fastapi_app(config)

    async def _get_client() -> Any:
        yield FakeClient()

    async def _get_repo() -> GeneReviewRepository:
        return fake_repo

    async def _get_embedder() -> FakeEmbeddingProvider:
        return FakeEmbeddingProvider(dim=384)

    fastapi_app.dependency_overrides[get_managed_client] = _get_client
    fastapi_app.dependency_overrides[get_repository] = _get_repo
    fastapi_app.dependency_overrides[get_embedding_provider] = _get_embedder
    return fastapi_app


@pytest_asyncio.fixture
async def http_client(app: FastAPI) -> AsyncClient:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


class TestChapterSectionRoute:
    @pytest.mark.asyncio
    async def test_returns_section_with_passages(self, http_client: AsyncClient) -> None:
        resp = await http_client.get("/chapters/NBK1247/sections/summary")
        assert resp.status_code == 200
        body = resp.json()
        assert body["nbk_id"] == "NBK1247"
        assert body["chapter_section"] == "summary"
        assert len(body["passages"]) == 2
        assert "concatenated_text" in body
        assert "First chunk" in body["concatenated_text"]
        assert "Second chunk" in body["concatenated_text"]
        # License lives at the dedicated /license endpoint, not inlined here.
        assert "license" not in body

    @pytest.mark.asyncio
    async def test_returns_404_when_section_not_found(
        self, http_client: AsyncClient, fake_repo: Any
    ) -> None:
        fake_repo.get_section.return_value = []
        resp = await http_client.get("/chapters/NBK1247/sections/nonexistent")
        assert resp.status_code == 404
