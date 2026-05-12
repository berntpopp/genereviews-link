"""Tests for the dedicated /license endpoint."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from genereview_link.api.routes import license as license_routes
from genereview_link.api.routes import passages as passages_routes
from genereview_link.retrieval.embeddings import FakeEmbeddingProvider
from genereview_link.retrieval.repository import LexicalPassageRow, PassageRow


@pytest.fixture
def app() -> FastAPI:
    app = FastAPI()
    app.include_router(license_routes.router)
    return app


@pytest.mark.asyncio
async def test_license_endpoint_returns_attribution(app: FastAPI) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/license")
    assert resp.status_code == 200
    body = resp.json()
    assert "copyright" in body
    assert "University of Washington" in body["copyright"]
    assert body["terms_url"].startswith("https://www.ncbi.nlm.nih.gov/")
    assert "data_source" in body
    assert "GeneReviews" in body["data_source"]


@pytest.mark.asyncio
async def test_license_endpoint_includes_spdx_and_attribution_text(app: FastAPI) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/license")
    assert resp.status_code == 200
    body = resp.json()
    assert body["license_spdx"] == "LicenseRef-GeneReviews"
    assert body["attribution_text"].startswith("GeneReviews")
    assert "University of Washington" in body["attribution_text"]
    assert "ncbi.nlm.nih.gov/books/NBK138602" in body["attribution_text"]


@pytest.mark.asyncio
async def test_license_payload_uses_literal_punctuation(app: FastAPI) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/license")
    assert response.status_code == 200
    raw = response.text
    assert "\\u00a9" not in raw.lower()
    assert "\\u2014" not in raw.lower()
    assert "©" in raw
    assert "—" in raw


@pytest.mark.asyncio
async def test_response_meta_includes_license_summary() -> None:
    """Any envelope with _meta should include license_summary."""
    from unittest.mock import AsyncMock, MagicMock

    repo = MagicMock()
    repo.search_passages = AsyncMock(
        return_value=[
            LexicalPassageRow(
                passage=PassageRow(
                    nbk_id="NBK1247",
                    passage_id="NBK1247:0001",
                    chapter_section="summary",
                    heading_path="Summary",
                    section_level=1,
                    chunk_index=1,
                    text="BRCA1 test passage.",
                    chapter_title="BRCA1 Chapter",
                    chapter_last_updated=None,
                    gene_symbols=("BRCA1",),
                ),
                phrase_rank=1.0,
                strict_rank=0.5,
                recall_rank=0.4,
                recall_overlap_count=1,
                lexical_rank=0.9,
                snippet="**BRCA1** test passage.",
            )
        ]
    )
    repo.active_embedding_table = AsyncMock(return_value="t")
    repo.dense_scores_for_passages = AsyncMock(return_value={})
    repo._dense_candidates_filtered = AsyncMock(return_value=[])
    repo.fetch_passages_by_ids = AsyncMock(return_value={})

    search_app = FastAPI()
    search_app.include_router(passages_routes.router)
    search_app.state.repository = repo
    search_app.state.embedder = FakeEmbeddingProvider(dim=384)

    async with AsyncClient(
        transport=ASGITransport(app=search_app), base_url="http://test"
    ) as client:
        resp = await client.get("/passages/search", params={"q": "BRCA1", "limit": 1})
    assert resp.status_code == 200
    meta = resp.json()["_meta"]
    assert "license_summary" in meta
    assert "genereview://license" in meta["license_summary"]
