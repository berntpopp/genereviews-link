"""GET /chapters/{nbk_id}/metadata route behaviour."""

from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from genereview_link.api.routes import chapters as chapters_routes
from genereview_link.models.sections import SECTION_NAMES
from genereview_link.retrieval.repository import (
    ChapterMetadataRow,
    SectionSummaryRow,
    TableSummaryRow,
)


def _make_metadata_row(
    *,
    nbk_id: str = "NBK1247",
    title: str = "BRCA1- and BRCA2-Associated HBOC",
    chapter_last_updated: date | None = date(2025, 12, 1),
    gene_symbols: tuple[str, ...] = ("BRCA1", "BRCA2"),
    table_count: int = 0,
    tables: tuple[TableSummaryRow, ...] = (),
) -> ChapterMetadataRow:
    """Build a ChapterMetadataRow with all canonical sections (matching repo behaviour)."""
    sections = tuple(
        SectionSummaryRow(
            section=name,
            passage_count=5 if name == "summary" else 0,
            total_char_count=50 if name == "summary" else 0,
        )
        for name in SECTION_NAMES
    )
    return ChapterMetadataRow(
        nbk_id=nbk_id,
        title=title,
        chapter_last_updated=chapter_last_updated,
        gene_symbols=gene_symbols,
        sections=sections,
        table_count=table_count,
        tables=tables,
    )


def _build_app(*, metadata: ChapterMetadataRow | None) -> FastAPI:
    app = FastAPI()
    app.include_router(chapters_routes.router)
    repo = MagicMock()
    repo.get_chapter_metadata = AsyncMock(return_value=metadata)
    app.state.repository = repo
    return app


# ---------------------------------------------------------------------------
# 200 path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_chapter_metadata_returns_200_for_known_nbk() -> None:
    app = _build_app(metadata=_make_metadata_row())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get("/chapters/NBK1247/metadata")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["nbk_id"] == "NBK1247"
    assert body["title"] == "BRCA1- and BRCA2-Associated HBOC"


@pytest.mark.asyncio
async def test_get_chapter_metadata_canonicalizes_zero_padded_nbk() -> None:
    app = _build_app(metadata=_make_metadata_row())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get("/chapters/NBK0001247/metadata")

    assert resp.status_code == 200, resp.text
    app.state.repository.get_chapter_metadata.assert_awaited_once_with("NBK1247")


@pytest.mark.asyncio
async def test_get_chapter_metadata_includes_gene_symbols() -> None:
    app = _build_app(metadata=_make_metadata_row())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get("/chapters/NBK1247/metadata")

    body = resp.json()
    assert "BRCA1" in body["gene_symbols"]
    assert "BRCA2" in body["gene_symbols"]


@pytest.mark.asyncio
async def test_get_chapter_metadata_sections_list_covers_all_canonical() -> None:
    """All canonical SECTION_NAMES appear in the sections list (including zero-count ones)."""
    app = _build_app(metadata=_make_metadata_row())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get("/chapters/NBK1247/metadata")

    body = resp.json()
    section_names = {s["section"] for s in body["sections"]}
    for name in SECTION_NAMES:
        assert name in section_names, f"expected section {name!r} in response"


@pytest.mark.asyncio
async def test_get_chapter_metadata_summary_has_nonzero_passage_count() -> None:
    app = _build_app(metadata=_make_metadata_row())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get("/chapters/NBK1247/metadata")

    body = resp.json()
    summary_entry = next(s for s in body["sections"] if s["section"] == "summary")
    assert summary_entry["passage_count"] == 5


@pytest.mark.asyncio
async def test_get_chapter_metadata_table_count_emitted() -> None:
    app = _build_app(metadata=_make_metadata_row(table_count=3))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get("/chapters/NBK1247/metadata")

    body = resp.json()
    assert body["table_count"] == 3


@pytest.mark.asyncio
async def test_get_chapter_metadata_chapter_last_updated_serialised() -> None:
    app = _build_app(metadata=_make_metadata_row(chapter_last_updated=date(2025, 12, 1)))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get("/chapters/NBK1247/metadata")

    body = resp.json()
    assert body["chapter_last_updated"] == "2025-12-01"


@pytest.mark.asyncio
async def test_get_chapter_metadata_meta_envelope_present() -> None:
    """Response must include a _meta envelope with attribution."""
    app = _build_app(metadata=_make_metadata_row())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get("/chapters/NBK1247/metadata")

    body = resp.json()
    assert "_meta" in body
    assert body["_meta"]["attribution"].startswith("GeneReviews")


@pytest.mark.asyncio
async def test_get_chapter_metadata_meta_corpus_version_from_app_state() -> None:
    app = _build_app(metadata=_make_metadata_row())
    app.state.corpus_version = "2026-03-10"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get("/chapters/NBK1247/metadata")

    body = resp.json()
    assert body["_meta"]["corpus_version"] == "2026-03-10"


# ---------------------------------------------------------------------------
# 404 path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_chapter_metadata_returns_404_for_unknown_nbk() -> None:
    app = _build_app(metadata=None)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get("/chapters/NBK0000000/metadata")

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_chapter_metadata_404_has_structured_payload() -> None:
    app = _build_app(metadata=None)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get("/chapters/NBK0000000/metadata")

    detail = resp.json()["detail"]
    assert detail["code"] == "chapter_not_found"
    assert detail["recovery_hint"]
    assert detail["next_commands"][0]["tool"] == "search_passages"


# ---------------------------------------------------------------------------
# Path validation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_chapter_metadata_rejects_malformed_nbk_with_422() -> None:
    """NBK IDs that don't match ^NBK[0-9]+$ must be rejected at route level."""
    app = _build_app(metadata=None)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get("/chapters/not-an-nbk/metadata")

    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_chapter_metadata_returns_tables_list() -> None:
    """tables[] in the response maps TableSummaryRow entries in order."""
    table_rows = (
        TableSummaryRow(
            table_id="mgmt.T.first",
            caption="Table 1 — Risk-reducing surgery",
            section="management",
            heading_path="Management > Table 1",
            passage_id="NBKTBL:0001",
        ),
        TableSummaryRow(
            table_id="mgmt.T.second",
            caption="Table 2 — Followup",
            section="management",
            heading_path="Management > Table 2",
            passage_id="NBKTBL:0002",
        ),
    )
    app = _build_app(
        metadata=_make_metadata_row(
            nbk_id="NBK9999",
            title="Tables Test Chapter",
            chapter_last_updated=None,
            gene_symbols=("TBTG",),
            table_count=2,
            tables=table_rows,
        )
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get("/chapters/NBK9999/metadata")

    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data["tables"], list)
    assert len(data["tables"]) == 2
    assert data["tables"][0]["table_id"] == "mgmt.T.first"
    assert data["tables"][0]["section"] == "management"
    assert data["tables"][0]["heading_path"].startswith("Management")
    assert data["tables"][0]["passage_id"].startswith("NBKTBL:")
    assert data["tables"][1]["table_id"] == "mgmt.T.second"


@pytest.mark.asyncio
async def test_chapter_metadata_tables_empty_list_when_none() -> None:
    """tables[] is an empty list when the chapter has no table passages."""
    app = _build_app(metadata=_make_metadata_row())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get("/chapters/NBK1247/metadata")

    assert resp.status_code == 200
    data = resp.json()
    assert data["tables"] == []
