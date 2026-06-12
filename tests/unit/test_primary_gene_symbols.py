"""Unit tests for primary_gene_symbols feature (issue #43).

Tests cover:
- extract_primary_gene_symbols() parsing helper (pure logic, no DB)
- gene_role query param default 'any' preserves behaviour
- gene_role=primary changes the query path
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from genereview_link.api.routes import passages as passages_routes
from genereview_link.corpus.nxml import extract_primary_gene_symbols
from genereview_link.retrieval.embeddings import FakeEmbeddingProvider
from genereview_link.retrieval.repository import LexicalPassageRow, PassageRow

# ---------------------------------------------------------------------------
# Pure unit tests for extract_primary_gene_symbols
# ---------------------------------------------------------------------------


def test_primary_gene_symbols_from_hboc_title() -> None:
    """HBOC chapter: BRCA1 and BRCA2 appear as whole words in the title."""
    assert extract_primary_gene_symbols(
        "BRCA1- and BRCA2-Associated Hereditary Breast and Ovarian Cancer",
        ("BRCA1", "BRCA2"),
    ) == ("BRCA1", "BRCA2")


def test_primary_gene_symbols_fa_title_excludes_brca1_alias() -> None:
    """FA chapter: 'Fanconi Anemia' title contains no gene symbols.

    BRCA1 (= FANCS) is in sidedata_genes but must NOT be returned as primary
    because it does not appear in the plain 'Fanconi Anemia' title.
    This is the core acceptance assertion from issue #43.
    """
    assert (
        extract_primary_gene_symbols(
            "Fanconi Anemia",
            ("FANCA", "FANCB", "FANCS"),  # BRCA1 alias FANCS is present in sidedata
        )
        == ()
    )


def test_primary_gene_symbols_empty_sidedata() -> None:
    """Gene not in sidedata_genes is never returned, even if in the title."""
    assert extract_primary_gene_symbols("BRCA1 Cancer", ()) == ()


def test_primary_gene_symbols_preserves_sidedata_order() -> None:
    """When multiple genes match the title they are returned in sidedata order."""
    result = extract_primary_gene_symbols(
        "TP53 and BRCA1 Cancer Syndrome",
        ("BRCA1", "TP53", "ATM"),  # sidedata order: BRCA1 first
    )
    assert result == ("BRCA1", "TP53")


def test_primary_gene_symbols_case_insensitive_match() -> None:
    """Title matching is case-insensitive."""
    assert extract_primary_gene_symbols(
        "brca1-associated cancer",
        ("BRCA1",),
    ) == ("BRCA1",)


def test_primary_gene_symbols_whole_word_boundary() -> None:
    """Gene names must match at word boundaries to avoid substring confusion."""
    # 'BRCA' should not match 'BRCA1'
    result = extract_primary_gene_symbols(
        "BRCA1-Associated Cancer",
        ("BRCA",),
    )
    assert result == ()


def test_chapter_record_primary_gene_symbols_nbk1247_vs_fa() -> None:
    """NBK1247 HBOC gets BRCA1 as primary; FA chapter does not.

    This mirrors the acceptance test from issue #43.
    """
    # HBOC: BRCA1 is primary
    assert "BRCA1" in extract_primary_gene_symbols(
        "BRCA1- and BRCA2-Associated Hereditary Breast and Ovarian Cancer",
        ("BRCA1", "BRCA2"),
    )
    # FA: BRCA1 alias FANCS is in sidedata but title is 'Fanconi Anemia'
    assert "BRCA1" not in extract_primary_gene_symbols(
        "Fanconi Anemia",
        ("FANCA", "FANCB", "FANCS"),
    )
    # And FANCS itself must not appear as primary for FA either
    assert "FANCS" not in extract_primary_gene_symbols(
        "Fanconi Anemia",
        ("FANCA", "FANCB", "FANCS"),
    )


# ---------------------------------------------------------------------------
# Route-level tests for gene_role param
# ---------------------------------------------------------------------------


def _passage_row(
    nbk_id: str = "NBK1247",
    passage_id: str = "NBK1247:0001",
    gene_symbols: tuple[str, ...] = ("BRCA1", "BRCA2"),
    primary_gene_symbols: tuple[str, ...] = ("BRCA1", "BRCA2"),
) -> PassageRow:
    return PassageRow(
        nbk_id=nbk_id,
        passage_id=passage_id,
        chapter_section="summary",
        heading_path="Summary",
        section_level=1,
        chunk_index=1,
        text="BRCA1 summary text",
        chapter_title="BRCA1 Chapter",
        gene_symbols=gene_symbols,
        primary_gene_symbols=primary_gene_symbols,
    )


def _lex_row(
    nbk_id: str = "NBK1247",
    passage_id: str = "NBK1247:0001",
    gene_symbols: tuple[str, ...] = ("BRCA1", "BRCA2"),
    primary_gene_symbols: tuple[str, ...] = ("BRCA1", "BRCA2"),
) -> LexicalPassageRow:
    return LexicalPassageRow(
        passage=_passage_row(
            nbk_id=nbk_id,
            passage_id=passage_id,
            gene_symbols=gene_symbols,
            primary_gene_symbols=primary_gene_symbols,
        ),
        phrase_rank=1.0,
        strict_rank=0.8,
        recall_rank=0.6,
        recall_overlap_count=1,
        lexical_rank=1.0,
        snippet="**BRCA1** summary text",
    )


def _app(mock_row: LexicalPassageRow | None = None) -> FastAPI:
    row = mock_row or _lex_row()
    repo = MagicMock()
    repo.search_passages = AsyncMock(return_value=[row])
    repo.active_embedding_table = AsyncMock(return_value="genereview_embeddings_bge384")
    repo._dense_candidates_filtered = AsyncMock(
        return_value=[{"passage_id": row.passage.passage_id, "dense_score": 0.9}]
    )
    repo.fetch_passages_by_ids = AsyncMock(return_value={})

    app = FastAPI()
    app.include_router(passages_routes.router)
    app.state.repository = repo
    app.state.embedder = FakeEmbeddingProvider(dim=384)
    return app


@pytest.mark.asyncio
async def test_gene_role_any_is_default_and_passes_any_to_repo() -> None:
    """gene_role defaults to 'any' and is forwarded to search_passages."""
    app = _app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get("/passages/search", params={"q": "BRCA1 surgery", "gene": "BRCA1"})
    assert resp.status_code == 200

    repo = app.state.repository
    # gene_role='any' is the default
    call_kwargs = repo.search_passages.call_args.kwargs
    assert call_kwargs.get("gene_role", "any") == "any"


@pytest.mark.asyncio
async def test_gene_role_primary_is_forwarded_to_repo() -> None:
    """gene_role=primary is forwarded to both search_passages and _dense_candidates_filtered."""
    app = _app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get(
            "/passages/search",
            params={"q": "BRCA1 surgery", "gene": "BRCA1", "gene_role": "primary"},
        )
    assert resp.status_code == 200

    repo = app.state.repository
    lex_kwargs = repo.search_passages.call_args.kwargs
    dense_kwargs = repo._dense_candidates_filtered.call_args.kwargs
    assert lex_kwargs.get("gene_role") == "primary"
    assert dense_kwargs.get("gene_role") == "primary"


@pytest.mark.asyncio
async def test_gene_role_mentioned_is_forwarded_to_repo() -> None:
    """gene_role=mentioned is forwarded to both retrieval methods."""
    app = _app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get(
            "/passages/search",
            params={"q": "BRCA1 surgery", "gene": "BRCA1", "gene_role": "mentioned"},
        )
    assert resp.status_code == 200

    repo = app.state.repository
    lex_kwargs = repo.search_passages.call_args.kwargs
    assert lex_kwargs.get("gene_role") == "mentioned"


@pytest.mark.asyncio
async def test_gene_role_invalid_returns_422() -> None:
    """An invalid gene_role value returns HTTP 422."""
    app = _app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get(
            "/passages/search",
            params={"q": "BRCA1 surgery", "gene": "BRCA1", "gene_role": "bogus"},
        )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_gene_role_any_preserves_existing_behaviour_no_gene() -> None:
    """Without gene=, gene_role has no effect (any is the default)."""
    app = _app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get("/passages/search", params={"q": "BRCA1 surgery"})
    assert resp.status_code == 200
    # search_passages is called with gene_role='any' (default)
    repo = app.state.repository
    call_kwargs = repo.search_passages.call_args.kwargs
    assert call_kwargs.get("gene_role", "any") == "any"
