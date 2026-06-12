"""Tests for POST /passages/search/batch (issue #45).

Wiring follows the patterns in test_routes_passages.py:
- Dependency overrides for get_repository and get_embedding_provider.
- UnifiedServerManager to build the full FastAPI app (ensures router is registered).
- FakeEmbeddingProvider + AsyncMock GeneReviewRepository.
"""

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

# ---------------------------------------------------------------------------
# Helpers — mirrors test_routes_passages.py
# ---------------------------------------------------------------------------


class FakeClient:
    async def search_genereviews(self, *a: Any, **kw: Any) -> dict:
        return {"count": 0, "retmax": 20, "retstart": 0, "ids": [], "webenv": "", "querykey": ""}

    async def fetch_abstract(self, *a: Any, **kw: Any) -> dict:
        return {}

    async def get_all_links(self, *a: Any, **kw: Any) -> dict:
        return {"urls": []}

    async def scrape_genereview_comprehensive(self, *a: Any, **kw: Any) -> dict:
        return {"nbk_id": "1", "url": "", "title": "", "sections": {}, "metadata": {}}


def _make_passage_row(
    passage_id: str = "NBK1247:0001",
    chapter_section: str = "management",
    text: str = "management passage",
) -> PassageRow:
    return PassageRow(
        nbk_id="NBK1247",
        passage_id=passage_id,
        chapter_section=chapter_section,
        heading_path="Management > Treatment",
        section_level=1,
        chunk_index=1,
        text=text,
    )


def _make_lexical_row(
    passage_id: str = "NBK1247:0001",
    chapter_section: str = "management",
    text: str = "management passage",
) -> LexicalPassageRow:
    return LexicalPassageRow(
        passage=_make_passage_row(passage_id, chapter_section, text),
        phrase_rank=0.5,
        strict_rank=0.4,
        recall_rank=0.3,
        recall_overlap_count=2,
        lexical_rank=0.6,
    )


def _make_fake_repo(rows_by_call: list[list[LexicalPassageRow]]) -> GeneReviewRepository:
    """Build a fake repo whose search_passages returns successive row lists per call."""
    repo = AsyncMock(spec=GeneReviewRepository)
    repo.search_passages.side_effect = rows_by_call
    repo.active_embedding_table.return_value = "genereview_embeddings_bge384"
    repo.dense_scores_for_passages.return_value = {}
    # Dense path returns empty so we fall back to lexical-only RRF
    repo._dense_candidates_filtered.return_value = []
    repo.fetch_passages_by_ids.return_value = {}
    return repo


@pytest_asyncio.fixture
async def app_factory() -> Any:
    """Return a factory that builds a FastAPI app with configurable fake repo."""

    async def _make(rows_by_call: list[list[LexicalPassageRow]]) -> FastAPI:
        config = ServerConfig(transport="http", log_level="WARNING", enable_docs=False)
        manager = UnifiedServerManager()
        fastapi_app = manager.create_fastapi_app(config)

        fake_repo = _make_fake_repo(rows_by_call)
        fake_embedder = FakeEmbeddingProvider(dim=384)

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

    return _make


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestSearchBatchRoute:
    """Tests for POST /passages/search/batch."""

    @pytest.mark.asyncio
    async def test_3_specs_return_3_results_partitioned_by_query_index(
        self, app_factory: Any
    ) -> None:
        """A 3-spec batch returns exactly 3 result items, one per spec."""
        row_mgmt = _make_lexical_row("NBK1247:0001", "management", "management text")
        row_counsel = _make_lexical_row("NBK1247:0002", "genetic_counseling", "counseling text")
        row_explore = _make_lexical_row("NBK1247:0003", "summary", "exploratory text")

        app = await app_factory([[row_mgmt], [row_counsel], [row_explore]])

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            resp = await c.post(
                "/passages/search/batch",
                json={
                    "specs": [
                        {"q": "management options", "sections": ["management"]},
                        {"q": "counseling recommendations", "sections": ["genetic_counseling"]},
                        {"q": "overview"},
                    ]
                },
            )

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert "results" in body
        assert "_meta" in body
        results = body["results"]
        assert len(results) == 3

        # Each result has the right query_index
        for i, result in enumerate(results):
            assert result["query_index"] == i

    @pytest.mark.asyncio
    async def test_hits_correspond_to_own_spec(self, app_factory: Any) -> None:
        """Each result's hits correspond to its own q/sections."""
        row_mgmt = _make_lexical_row("NBK1247:0001", "management", "management text")
        row_counsel = _make_lexical_row("NBK1247:0002", "genetic_counseling", "counseling text")
        row_explore = _make_lexical_row("NBK1247:0003", "summary", "exploratory text")

        app = await app_factory([[row_mgmt], [row_counsel], [row_explore]])

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            resp = await c.post(
                "/passages/search/batch",
                json={
                    "specs": [
                        {"q": "management options", "sections": ["management"]},
                        {"q": "counseling recommendations", "sections": ["genetic_counseling"]},
                        {"q": "overview"},
                    ]
                },
            )

        body = resp.json()
        results = body["results"]

        # Result 0 — management
        assert results[0]["q"] == "management options"
        assert results[0]["sections"] == ["management"]
        assert len(results[0]["hits"]) == 1
        assert results[0]["hits"][0]["passage_id"] == "NBK1247:0001"

        # Result 1 — genetic_counseling
        assert results[1]["q"] == "counseling recommendations"
        assert results[1]["sections"] == ["genetic_counseling"]
        assert len(results[1]["hits"]) == 1
        assert results[1]["hits"][0]["passage_id"] == "NBK1247:0002"

        # Result 2 — exploratory
        assert results[2]["q"] == "overview"
        assert results[2]["sections"] is None
        assert len(results[2]["hits"]) == 1
        assert results[2]["hits"][0]["passage_id"] == "NBK1247:0003"

    @pytest.mark.asyncio
    async def test_batch_size_exceeding_5_is_rejected_with_422(self, app_factory: Any) -> None:
        """A request with more than 5 specs must be rejected with 422."""
        app = await app_factory([[] for _ in range(6)])  # 6 empty return sets (never used)

        specs = [{"q": f"query {i}"} for i in range(6)]
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            resp = await c.post("/passages/search/batch", json={"specs": specs})

        assert resp.status_code == 422, resp.text

    @pytest.mark.asyncio
    async def test_batch_size_0_is_rejected_with_422(self, app_factory: Any) -> None:
        """An empty specs list must be rejected with 422 (min_length=1)."""
        app = await app_factory([])

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            resp = await c.post("/passages/search/batch", json={"specs": []})

        assert resp.status_code == 422, resp.text

    @pytest.mark.asyncio
    async def test_single_spec_returns_single_result(self, app_factory: Any) -> None:
        """A single spec works like a regular search call."""
        row = _make_lexical_row("NBK1247:0001", "management", "single result text")
        app = await app_factory([[row]])

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            resp = await c.post(
                "/passages/search/batch",
                json={"specs": [{"q": "management options"}]},
            )

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert len(body["results"]) == 1
        assert body["results"][0]["query_index"] == 0
        assert len(body["results"][0]["hits"]) == 1

    @pytest.mark.asyncio
    async def test_cross_query_duplicate_is_annotated(self, app_factory: Any) -> None:
        """When the same passage_id appears in multiple results it is annotated."""
        shared_row = _make_lexical_row("NBK1247:0001", "management", "shared passage")

        # Both specs return the same passage
        app = await app_factory([[shared_row], [shared_row]])

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            resp = await c.post(
                "/passages/search/batch",
                json={
                    "specs": [
                        {"q": "query A"},
                        {"q": "query B"},
                    ]
                },
            )

        assert resp.status_code == 200, resp.text
        body = resp.json()
        results = body["results"]
        # query_index=0 canonical: no also_matched_query_indices (or absent)
        hit_0 = results[0]["hits"][0]
        assert (
            hit_0.get("also_matched_query_indices") is None
            or "also_matched_query_indices" not in hit_0
        )

        # query_index=1 non-canonical: annotated with [0]
        hit_1 = results[1]["hits"][0]
        assert "also_matched_query_indices" in hit_1
        assert 0 in hit_1["also_matched_query_indices"]

    @pytest.mark.asyncio
    async def test_existing_single_search_passages_untouched(self, app_factory: Any) -> None:
        """GET /passages/search still works after the batch router is registered."""
        row = _make_lexical_row("NBK1247:0001", "management", "single passage text")
        app = await app_factory([[row]])

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            resp = await c.get("/passages/search?q=management+options")

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert "results" in body
        assert len(body["results"]) == 1
        assert body["results"][0]["passage_id"] == "NBK1247:0001"

    @pytest.mark.asyncio
    async def test_meta_carries_attribution_and_corpus_version(self, app_factory: Any) -> None:
        """_meta contains attribution and corpus_version."""
        row = _make_lexical_row()
        app = await app_factory([[row]])
        app.state.corpus_version = "2026-06-01"

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            resp = await c.post(
                "/passages/search/batch",
                json={"specs": [{"q": "test query"}]},
            )

        assert resp.status_code == 200, resp.text
        meta = resp.json()["_meta"]
        assert "attribution" in meta
        assert meta["attribution"].startswith("GeneReviews")
