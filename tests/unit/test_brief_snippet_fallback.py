"""issue #106 D5: brief mode must never return rows with BOTH text and snippet null.

Dense-only hits (lexical_score 0.0) produce no ts_headline fragment, so the row
arrived with snippet=null AND text=null — content-free. Brief mode must fall back
to a leading excerpt of the passage text.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from genereview_link.api.routes import passages as passages_routes
from genereview_link.api.routes.passages import _leading_excerpt
from genereview_link.retrieval.embeddings import FakeEmbeddingProvider
from genereview_link.retrieval.repository import LexicalPassageRow, PassageRow


def _dense_only_row() -> LexicalPassageRow:
    # A dense-only hit: lexical_rank 0.0, snippet None (no ts_headline fragment).
    return LexicalPassageRow(
        passage=PassageRow(
            nbk_id="NBK1318",
            passage_id="NBK1318:0026",
            chapter_section="management",
            heading_path="Management",
            section_level=1,
            chunk_index=26,
            text=(
                "Avoid sodium channel blockers such as carbamazepine, lamotrigine, "
                "and phenytoin in individuals with SCN1A-related Dravet syndrome. " * 6
            ),
            chapter_title="SCN1A Seizure Disorders",
            gene_symbols=("SCN1A",),
        ),
        phrase_rank=0.0,
        strict_rank=0.0,
        recall_rank=0.0,
        recall_overlap_count=0,
        lexical_rank=0.0,
        snippet=None,
    )


def _app() -> FastAPI:
    row = _dense_only_row()
    repo = MagicMock()
    repo.search_passages = AsyncMock(return_value=[row])
    repo.active_embedding_table = AsyncMock(return_value="genereview_embeddings_bge384")
    repo._dense_candidates_filtered = AsyncMock(
        return_value=[{"passage_id": row.passage.passage_id, "dense_score": 0.95}]
    )
    repo.fetch_passages_by_ids = AsyncMock(return_value={})
    app = FastAPI()
    app.include_router(passages_routes.router)
    app.state.repository = repo
    app.state.embedder = FakeEmbeddingProvider(dim=384)
    return app


def test_leading_excerpt_trims_to_word_boundary() -> None:
    text = "alpha beta gamma delta epsilon zeta eta theta"
    out = _leading_excerpt(text, 20)
    assert out.endswith("…")
    assert " " in out
    assert not out[:-1].endswith(" ")
    assert len(out) <= 22


def test_leading_excerpt_short_text_unchanged() -> None:
    assert _leading_excerpt("short", 100) == "short"


@pytest.mark.asyncio
async def test_brief_mode_dense_only_row_has_content_not_null() -> None:
    app = _app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get(
            "/passages/search",
            params={"q": "avoid sodium channel blockers", "mode": "brief"},
        )
    assert resp.status_code == 200
    rows = resp.json()["results"]
    assert rows, "expected at least one row"
    row = rows[0]
    # The dense-only row must NOT arrive with both text and snippet null.
    assert row["text"] is None  # brief mode never carries full text
    assert row["snippet"] is not None, "brief row must carry a snippet fallback"
    assert row["snippet"]["kind"] == "untrusted_text"
    assert row["snippet"]["text"].strip() != ""
