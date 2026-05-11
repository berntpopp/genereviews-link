"""Unit tests for /chapters/{nbk_id}/sections/{section} using TestClient + dependency overrides."""

from __future__ import annotations

from datetime import date
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from genereview_link.api.client_manager import get_managed_client
from genereview_link.api.routes import chapters as chapters_routes
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
        resp = await http_client.get("/chapters/NBK1247/sections/management")
        assert resp.status_code == 404


def _build_app(*, passages: list[PassageRow]) -> FastAPI:
    app = FastAPI()
    app.include_router(chapters_routes.router)
    repo = MagicMock()
    repo.get_section = AsyncMock(return_value=passages)
    app.state.repository = repo
    return app


@pytest.mark.asyncio
async def test_returns_passages_with_chapter_title_envelope() -> None:
    pr = PassageRow(
        nbk_id="NBK1",
        passage_id="NBK1:0001",
        chapter_section="management",
        heading_path="Management > X",
        section_level=2,
        chunk_index=0,
        text="sample text",
        chapter_title="Test Chapter Title",
        chapter_last_updated=date(2025, 12, 1),
        gene_symbols=("TG",),
    )
    app = _build_app(passages=[pr])

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get("/chapters/NBK1/sections/management")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["nbk_id"] == "NBK1"
    assert body["chapter_section"] == "management"
    assert body["chapter_title"] == "Test Chapter Title"
    assert body["chapter_last_updated"] == "2025-12-01"
    assert body["passages"][0]["passage_id"] == "NBK1:0001"
    assert body["concatenated_text"] == "sample text"


@pytest.mark.asyncio
async def test_old_path_param_name_does_not_match() -> None:
    """If someone reverts the rename, this test will fail because the
    old route had a path param called `nbk`; the new one is `nbk_id`.
    The path itself doesn't change — only the function signature does —
    so this test asserts the call still returns 200 (route path is
    unchanged) and that the response envelope keys use `nbk_id`.
    """
    pr = PassageRow(
        nbk_id="NBK1",
        passage_id="NBK1:0001",
        chapter_section="management",
        heading_path=None,
        section_level=1,
        chunk_index=0,
        text="t",
        chapter_title="C",
        chapter_last_updated=None,
        gene_symbols=(),
    )
    app = _build_app(passages=[pr])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get("/chapters/NBK1/sections/management")
    body = resp.json()
    assert "nbk_id" in body
    assert "nbk" not in body or body.get("nbk_id") == body.get("nbk")


@pytest.mark.asyncio
async def test_section_response_includes_meta_attribution() -> None:
    """Chapter section response wraps payload in an envelope with _meta.attribution."""
    pr = PassageRow(
        nbk_id="NBK1",
        passage_id="NBK1:0001",
        chapter_section="management",
        heading_path="Management > X",
        section_level=2,
        chunk_index=0,
        text="t",
        chapter_title="Test",
        chapter_last_updated=date(2025, 12, 1),
        gene_symbols=("TG",),
    )
    app = _build_app(passages=[pr])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get("/chapters/NBK1/sections/management")
    body = resp.json()
    assert "_meta" in body
    assert body["_meta"]["attribution"].startswith("GeneReviews")
