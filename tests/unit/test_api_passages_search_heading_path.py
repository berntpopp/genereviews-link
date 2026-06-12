"""Unit tests for heading_path_contains on /passages/search."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from genereview_link.api.routes import passages as passages_routes
from genereview_link.retrieval.embeddings import FakeEmbeddingProvider
from genereview_link.retrieval.repository import LexicalPassageRow, PassageRow


def _row(passage_id: str, heading_path: str) -> LexicalPassageRow:
    return LexicalPassageRow(
        passage=PassageRow(
            nbk_id="NBK1247",
            passage_id=passage_id,
            chapter_section="management",
            heading_path=heading_path,
            section_level=2,
            chunk_index=int(passage_id.split(":")[1]),
            text="mastectomy risk reduction text",
            chapter_title="BRCA Chapter",
            gene_symbols=("BRCA1",),
        ),
        phrase_rank=1.0,
        strict_rank=0.8,
        recall_rank=0.6,
        recall_overlap_count=1,
        lexical_rank=1.0,
        snippet="mastectomy risk reduction text",
    )


def _app() -> FastAPI:
    rows = [
        _row("NBK1247:0001", "Management > Prevention > Risk-Reducing Surgery"),
        _row("NBK1247:0002", "Management > Treatment of Manifestations"),
    ]

    async def search_passages(
        query: str,
        *,
        gene_symbol: str | None = None,
        nbk_id: str | None = None,
        sections: list[str] | None = None,
        heading_path_contains: str | None = None,
        limit: int = 20,
        brief: bool = False,
        snippet_max_fragments: int = 2,
        snippet_max_words: int = 30,
        gene_role: str = "any",
    ) -> list[LexicalPassageRow]:
        del query, gene_symbol, nbk_id, sections, limit, brief, snippet_max_fragments
        del snippet_max_words, gene_role
        if heading_path_contains is None:
            return rows
        needle = heading_path_contains.casefold()
        return [row for row in rows if needle in (row.passage.heading_path or "").casefold()]

    repo = MagicMock()
    repo.search_passages = AsyncMock(side_effect=search_passages)
    repo.active_embedding_table = AsyncMock(return_value="genereview_embeddings_bge384")
    repo.dense_scores_for_passages = AsyncMock(return_value={})
    repo._dense_candidates_filtered = AsyncMock(return_value=[])
    repo.fetch_passages_by_ids = AsyncMock(return_value={})

    app = FastAPI()
    app.include_router(passages_routes.router)
    app.state.repository = repo
    app.state.embedder = FakeEmbeddingProvider(dim=384)
    return app


@pytest.mark.asyncio
async def test_heading_path_contains_restricts_search_results_and_diagnostics() -> None:
    app = _app()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get(
            "/passages/search",
            params={"q": "mastectomy", "heading_path_contains": "Prevention"},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert [row["passage_id"] for row in body["results"]] == ["NBK1247:0001"]
    assert body["results"][0]["heading_path"] == ("Management > Prevention > Risk-Reducing Surgery")
    assert body["_meta"]["diagnostics"]["applied_filters"] == ["heading_path_contains=Prevention"]
