"""Unit tests for dual q/query support on /passages/search."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from genereview_link.api.routes import passages as passages_routes
from genereview_link.retrieval.embeddings import FakeEmbeddingProvider
from genereview_link.retrieval.repository import LexicalPassageRow, PassageRow


def _row() -> LexicalPassageRow:
    return LexicalPassageRow(
        passage=PassageRow(
            nbk_id="NBK1",
            passage_id="NBK1:0001",
            chapter_section="summary",
            heading_path="Summary",
            section_level=1,
            chunk_index=1,
            text="BRCA1 summary text",
            chapter_title="BRCA Chapter",
            gene_symbols=("BRCA1",),
        ),
        phrase_rank=1.0,
        strict_rank=0.8,
        recall_rank=0.6,
        recall_overlap_count=1,
        lexical_rank=1.0,
        snippet="**BRCA1** summary text",
    )


def _app() -> FastAPI:
    repo = MagicMock()
    repo.search_passages = AsyncMock(return_value=[_row()])
    repo.active_embedding_table = AsyncMock(return_value="genereview_embeddings_bge384")
    repo.dense_scores_for_passages = AsyncMock(return_value={"NBK1:0001": 0.9})
    repo._dense_candidates_filtered = AsyncMock(
        return_value=[{"passage_id": "NBK1:0001", "dense_score": 0.9}]
    )
    repo.fetch_passages_by_ids = AsyncMock(return_value={})

    app = FastAPI()
    app.include_router(passages_routes.router)
    app.state.repository = repo
    app.state.embedder = FakeEmbeddingProvider(dim=384)
    return app


def _drop_fence_timestamps(value: Any) -> Any:
    """Recursively null ``retrieved_at`` on every fenced node so two independent
    fence calls (chapter_title/heading_path/text/snippet/...) compare byte-equal.

    ``fence_untrusted_text`` stamps ``provenance.retrieved_at = datetime.now(UTC)``
    at serialization, so logically-identical requests moments apart otherwise
    never compare equal.
    """
    if isinstance(value, dict):
        if value.get("kind") == "untrusted_text" and isinstance(value.get("provenance"), dict):
            return {
                **{k: _drop_fence_timestamps(v) for k, v in value.items()},
                "provenance": {**value["provenance"], "retrieved_at": None},
            }
        return {k: _drop_fence_timestamps(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_drop_fence_timestamps(v) for v in value]
    return value


@pytest.mark.asyncio
async def test_q_query_and_matching_dual_query_return_identical_results() -> None:
    app = _app()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        q_resp = await c.get("/passages/search", params={"q": "BRCA1"})
        query_resp = await c.get("/passages/search", params={"query": "BRCA1"})
        both_resp = await c.get(
            "/passages/search",
            params={"q": "BRCA1", "query": "BRCA1"},
        )

    assert q_resp.status_code == 200
    assert query_resp.status_code == 200
    assert both_resp.status_code == 200
    assert _drop_fence_timestamps(query_resp.json()["results"]) == _drop_fence_timestamps(
        q_resp.json()["results"]
    )
    assert _drop_fence_timestamps(both_resp.json()["results"]) == _drop_fence_timestamps(
        q_resp.json()["results"]
    )
    repo = app.state.repository
    assert [call.args[0] for call in repo.search_passages.await_args_list] == [
        "BRCA1",
        "BRCA1",
        "BRCA1",
    ]


@pytest.mark.asyncio
async def test_conflicting_q_and_query_returns_structured_422() -> None:
    app = _app()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get("/passages/search", params={"q": "foo", "query": "bar"})

    assert resp.status_code == 422
    detail: dict[str, Any] = resp.json()["detail"]
    assert detail["code"] == "conflicting_query_param"
    assert detail["message"] == "both q and query supplied with different values"


@pytest.mark.asyncio
async def test_missing_q_and_query_returns_structured_422() -> None:
    app = _app()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get("/passages/search")

    assert resp.status_code == 422
    detail: dict[str, Any] = resp.json()["detail"]
    assert detail["code"] == "missing_query"
    assert detail["message"] == "one of q or query is required"
