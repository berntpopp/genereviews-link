"""GET /chapters/{nbk_id}/tables/{table_id} route behaviour."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from genereview_link.api.routes import tables as tables_routes
from genereview_link.retrieval.repository import TableRow


def _make_table_row(
    *,
    nbk_id: str = "NBK1247",
    table_id: str = "t5",
    caption: str = "Variant classes",
    heading_path: str | None = "Diagnosis > Table 5",
    section: str = "diagnosis",
    header: list[str] | None = None,
    rows: list[list[str]] | None = None,
    passage_id: str = "NBK1247:0042",
) -> TableRow:
    return TableRow(
        nbk_id=nbk_id,
        table_id=table_id,
        caption=caption,
        heading_path=heading_path,
        section=section,
        header=header if header is not None else ["Variant", "Class"],
        rows=rows if rows is not None else [["c.1A>G", "Pathogenic"]],
        passage_id=passage_id,
    )


def _build_app(
    *,
    table: TableRow | None,
    chapter_exists: bool = True,
    known_table_ids: list[str] | None = None,
) -> FastAPI:
    app = FastAPI()
    app.include_router(tables_routes.router)

    repo = MagicMock()
    repo.get_table = AsyncMock(return_value=table)

    from genereview_link.models.sections import SECTION_NAMES
    from genereview_link.retrieval.repository import ChapterMetadataRow, SectionSummaryRow

    if chapter_exists:
        sections = tuple(
            SectionSummaryRow(section=name, passage_count=0, total_char_count=0)
            for name in SECTION_NAMES
        )
        meta_row = ChapterMetadataRow(
            nbk_id="NBK1247",
            title="BRCA1-HBOC",
            chapter_last_updated=None,
            gene_symbols=("BRCA1",),
            sections=sections,
            table_count=1,
        )
        repo.get_chapter_metadata = AsyncMock(return_value=meta_row)
    else:
        repo.get_chapter_metadata = AsyncMock(return_value=None)

    repo.list_table_ids = AsyncMock(return_value=known_table_ids or [])
    app.state.repository = repo
    return app


# ---------------------------------------------------------------------------
# 200 path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_table_returns_200_for_known_table() -> None:
    app = _build_app(table=_make_table_row())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get("/chapters/NBK1247/tables/t5")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["nbk_id"] == "NBK1247"
    assert body["table_id"] == "t5"


@pytest.mark.asyncio
async def test_get_table_canonicalizes_zero_padded_nbk() -> None:
    app = _build_app(table=_make_table_row())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get("/chapters/NBK0001247/tables/t5")

    assert resp.status_code == 200, resp.text
    app.state.repository.get_table.assert_awaited_once_with("NBK1247", "t5")


@pytest.mark.asyncio
async def test_get_table_response_shape() -> None:
    app = _build_app(table=_make_table_row())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get("/chapters/NBK1247/tables/t5")

    body = resp.json()
    assert body["caption"] == "Variant classes"
    assert body["section"] == "diagnosis"
    assert body["heading_path"] == "Diagnosis > Table 5"
    assert body["header"] == ["Variant", "Class"]
    assert body["rows"] == [["c.1A>G", "Pathogenic"]]
    assert body["passage_id"] == "NBK1247:0042"


@pytest.mark.asyncio
async def test_get_table_meta_envelope_present() -> None:
    app = _build_app(table=_make_table_row())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get("/chapters/NBK1247/tables/t5")

    body = resp.json()
    assert "_meta" in body
    assert body["_meta"]["attribution"].startswith("GeneReviews")


@pytest.mark.asyncio
async def test_get_table_meta_corpus_version_from_app_state() -> None:
    app = _build_app(table=_make_table_row())
    app.state.corpus_version = "2026-01-01"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get("/chapters/NBK1247/tables/t5")

    body = resp.json()
    assert body["_meta"]["corpus_version"] == "2026-01-01"


@pytest.mark.asyncio
async def test_get_table_null_heading_path() -> None:
    app = _build_app(table=_make_table_row(heading_path=None))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get("/chapters/NBK1247/tables/t5")

    body = resp.json()
    assert body["heading_path"] is None


# ---------------------------------------------------------------------------
# 404 path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_table_returns_404_for_unknown_table() -> None:
    app = _build_app(table=None, chapter_exists=True, known_table_ids=["t1", "t5"])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get("/chapters/NBK1247/tables/t999")

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_table_404_structured_payload() -> None:
    app = _build_app(table=None, chapter_exists=True, known_table_ids=["t1", "t5"])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get("/chapters/NBK1247/tables/t999")

    detail = resp.json()["detail"]
    assert detail["code"] == "table_not_found"
    assert detail["recovery_hint"]
    assert detail["next_commands"][0]["tool"] == "get_chapter_metadata"


@pytest.mark.asyncio
async def test_get_table_404_includes_valid_table_ids_when_chapter_exists() -> None:
    app = _build_app(table=None, chapter_exists=True, known_table_ids=["t1", "t5"])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get("/chapters/NBK1247/tables/t999")

    detail = resp.json()["detail"]
    assert len(detail["field_errors"]) == 1
    fe = detail["field_errors"][0]
    assert fe["field"] == "table_id"
    assert "t1" in fe["valid_values"]
    assert "t5" in fe["valid_values"]


@pytest.mark.asyncio
async def test_get_table_404_no_field_errors_when_chapter_missing() -> None:
    app = _build_app(table=None, chapter_exists=False, known_table_ids=[])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get("/chapters/NBK1247/tables/t999")

    detail = resp.json()["detail"]
    assert detail["field_errors"] == []


# ---------------------------------------------------------------------------
# Path validation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_table_rejects_malformed_nbk_with_422() -> None:
    app = _build_app(table=None)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get("/chapters/not-an-nbk/tables/t5")

    assert resp.status_code == 422


def test_get_table_openapi_schema_declares_table_id_pattern() -> None:
    app = _build_app(table=None)

    params = app.openapi()["paths"]["/chapters/{nbk_id}/tables/{table_id}"]["get"]["parameters"]
    table_param = next(param for param in params if param["name"] == "table_id")

    assert table_param["schema"]["pattern"] == r"^[A-Za-z0-9][A-Za-z0-9_.-]*$"
