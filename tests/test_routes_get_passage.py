"""GET /passages/{passage_id} route behaviour."""

from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from genereview_link.api.routes import passages as passages_routes
from genereview_link.retrieval.repository import PassageRow


def _make_row(
    *,
    nbk_id: str = "NBK1247",
    passage_id: str = "NBK1247:0022",
    chapter_section: str = "management",
    heading_path: str = "Management > Other",
    section_level: int = 2,
    chunk_index: int = 22,
    text: str = "risk-reducing surgery text",
    chapter_title: str = "BRCA1- and BRCA2-Associated HBOC",
    chapter_last_updated: date = date(2025, 12, 1),
    gene_symbols: tuple[str, ...] = ("BRCA1", "BRCA2"),
    passage_role: str | None = None,
) -> PassageRow:
    return PassageRow(
        nbk_id=nbk_id,
        passage_id=passage_id,
        chapter_section=chapter_section,
        heading_path=heading_path,
        section_level=section_level,
        chunk_index=chunk_index,
        text=text,
        chapter_title=chapter_title,
        chapter_last_updated=chapter_last_updated,
        gene_symbols=gene_symbols,
        passage_role=passage_role,
    )


def _build_app(
    *,
    focal: PassageRow | None,
    before: list[PassageRow] | None = None,
    after: list[PassageRow] | None = None,
    has_more_before: bool = False,
    has_more_after: bool = False,
) -> FastAPI:
    app = FastAPI()
    app.include_router(passages_routes.router)
    repo = MagicMock()
    repo.get_passage_window = AsyncMock(
        return_value=(focal, before or [], after or [], has_more_before, has_more_after)
    )
    app.state.repository = repo
    return app


@pytest.mark.asyncio
async def test_get_passage_returns_200_with_chapter_title() -> None:
    pr = _make_row()
    app = _build_app(focal=pr)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get("/passages/NBK1247:0022")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    # New wrapper shape
    assert "passage" in body
    passage = body["passage"]
    assert passage["passage_id"] == "NBK1247:0022"
    assert passage["chapter_title"]["text"] == "BRCA1- and BRCA2-Associated HBOC"
    assert passage["chapter_last_updated"] == "2025-12-01"
    assert passage["gene_symbols"] == ["BRCA1", "BRCA2"]
    assert passage["char_count"] == len("risk-reducing surgery text")
    # Neighbor lists are empty by default
    assert body["neighbors_before"] == []
    assert body["neighbors_after"] == []
    assert body["has_more_before"] is False
    assert body["has_more_after"] is False


@pytest.mark.asyncio
async def test_get_passage_default_returns_wrapper_with_empty_neighbors() -> None:
    pr = _make_row()
    app = _build_app(focal=pr)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get("/passages/NBK1247:0022")

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert "passage" in data
    assert data["passage"]["passage_id"] == "NBK1247:0022"
    assert data["neighbors_before"] == []
    assert data["neighbors_after"] == []
    assert isinstance(data["has_more_before"], bool)
    assert isinstance(data["has_more_after"], bool)


@pytest.mark.asyncio
async def test_get_passage_neighbors_returns_window() -> None:
    focal = _make_row(chunk_index=22)
    before_row = _make_row(passage_id="NBK1247:0021", chunk_index=21, text="before text")
    after_row1 = _make_row(passage_id="NBK1247:0023", chunk_index=23, text="after text 1")
    after_row2 = _make_row(passage_id="NBK1247:0024", chunk_index=24, text="after text 2")
    app = _build_app(
        focal=focal,
        before=[before_row],
        after=[after_row1, after_row2],
        has_more_before=False,
        has_more_after=True,
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get("/passages/NBK1247:0022", params={"neighbors": 2})

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert len(data["neighbors_before"]) <= 2
    assert len(data["neighbors_after"]) <= 2
    assert data["neighbors_before"][0]["passage_id"] == "NBK1247:0021"
    assert data["neighbors_after"][0]["passage_id"] == "NBK1247:0023"
    assert data["has_more_after"] is True
    assert data["has_more_before"] is False


@pytest.mark.asyncio
async def test_get_passage_cross_sections_smoke() -> None:
    pr = _make_row()
    app = _build_app(focal=pr)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get(
            "/passages/NBK1247:0022", params={"neighbors": 1, "cross_sections": "true"}
        )

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert "passage" in data
    # repo was called with cross_sections=True
    app.state.repository.get_passage_window.assert_awaited_once_with(
        "NBK1247:0022", before=1, after=1, cross_sections=True
    )


@pytest.mark.asyncio
async def test_get_passage_returns_404_for_unknown_id() -> None:
    app = _build_app(focal=None)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get("/passages/NBK9999:9999")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_passage_rejects_malformed_id_with_422() -> None:
    app = _build_app(focal=None)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get("/passages/not-a-passage-id")
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_unknown_passage_returns_structured_404() -> None:
    app = _build_app(focal=None)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get("/passages/NBK9999:9999")
    assert resp.status_code == 404
    detail = resp.json()["detail"]
    assert detail["code"] == "passage_not_found"
    assert "NBKxxxx:NNNN" in detail["recovery_hint"]
    assert detail["next_commands"][0]["tool"] == "search_passages"


@pytest.mark.asyncio
async def test_get_passage_exposes_passage_type_narrative() -> None:
    """GET /passages/{id} exposes passage_type='narrative' for standard passages."""
    pr = _make_row()
    app = _build_app(focal=pr)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get("/passages/NBK1247:0022")
    assert resp.status_code == 200
    assert resp.json()["passage"]["passage_type"] == "narrative"


@pytest.mark.asyncio
async def test_get_passage_exposes_passage_type_table() -> None:
    """GET /passages/{id} exposes passage_type='table' when the row has that type."""
    pr = PassageRow(
        nbk_id="NBK1247",
        passage_id="NBK1247:0099",
        chapter_section="management",
        heading_path="Management > Table 1",
        section_level=2,
        chunk_index=99,
        text="Table cell content",
        chapter_title="BRCA1- and BRCA2-Associated HBOC",
        chapter_last_updated=date(2025, 12, 1),
        gene_symbols=("BRCA1",),
        passage_type="table",
    )
    app = _build_app(focal=pr)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get("/passages/NBK1247:0099")
    assert resp.status_code == 200
    assert resp.json()["passage"]["passage_type"] == "table"


@pytest.mark.asyncio
async def test_get_passage_propagates_passage_role() -> None:
    """GET /passages/{id} preserves passage_role from the repository row."""
    pr = _make_row(passage_role="definition")
    app = _build_app(focal=pr)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get("/passages/NBK1247:0022")

    assert resp.status_code == 200
    assert resp.json()["passage"]["passage_role"] == "definition"


@pytest.mark.asyncio
async def test_get_passage_normalizes_unknown_passage_role_to_none() -> None:
    """Unexpected DB passage_role strings must not make the route return 500."""
    pr = _make_row(passage_role="unexpected_role")
    app = _build_app(focal=pr)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get("/passages/NBK1247:0022")

    assert resp.status_code == 200
    assert resp.json()["passage"]["passage_role"] is None


# ---------------------------------------------------------------------------
# heading_path_array opt-in tests (Task 11 — Spec H1)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_passage_heading_path_array_absent_by_default() -> None:
    """heading_path_array is absent from the focal passage unless opted in."""
    pr = _make_row(passage_id="NBK1247:0010", chunk_index=10, heading_path="A > B > C")
    app = _build_app(focal=pr)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get("/passages/NBK1247:0010")
    assert resp.status_code == 200
    assert resp.json()["passage"].get("heading_path_array") is None


@pytest.mark.asyncio
async def test_get_passage_heading_path_is_v1_1_fenced() -> None:
    """heading_path is a v1.1-fenced untrusted_text object (heading_path_array dropped)."""
    pr = _make_row(passage_id="NBK1247:0010", chunk_index=10, heading_path="A > B > C")
    app = _build_app(focal=pr)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get("/passages/NBK1247:0010")
    assert resp.status_code == 200
    passage = resp.json()["passage"]
    assert passage["heading_path"]["kind"] == "untrusted_text"
    assert passage["heading_path"]["text"] == "A > B > C"
    assert "heading_path_array" not in passage


# ---------------------------------------------------------------------------
# recommended_citation tests (Task 12 — Spec I1)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_passage_recommended_citation_present() -> None:
    """recommended_citation is identifiers/date only (title lives fenced on chapter_title)."""
    pr = _make_row(
        passage_id="NBK1247:0020",
        chunk_index=20,
        chapter_title="HBOC",
        chapter_last_updated=date(2026, 3, 25),
    )
    app = _build_app(focal=pr)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get("/passages/NBK1247:0020")
    assert resp.status_code == 200
    passage = resp.json()["passage"]
    assert passage["recommended_citation"] == "NBK1247. Updated 2026-03-25. Passage NBK1247:0020."
    # The title is NOT in the citation (no prose duplication); it is fenced.
    assert "HBOC" not in passage["recommended_citation"]
    assert passage["chapter_title"]["text"] == "HBOC"


# ---------------------------------------------------------------------------
# source_url tests (Pass-3-A)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_passage_carries_source_url() -> None:
    """source_url present on PassageDetail and points at the chapter URL."""
    pr = _make_row(
        passage_id="NBK1247:0010",
        chunk_index=10,
        nbk_id="NBK1247",
    )
    app = _build_app(focal=pr)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get("/passages/NBK1247:0010")
    assert resp.status_code == 200
    body = resp.json()
    assert body["passage"]["source_url"] == "https://www.ncbi.nlm.nih.gov/books/NBK1247/"


# ---------------------------------------------------------------------------
# include=table_data opt-in tests (#44)
# ---------------------------------------------------------------------------


def _make_table_row(
    *,
    passage_id: str = "NBK1247:0099",
    chunk_index: int = 99,
    table_data: dict | None = None,
) -> PassageRow:
    return PassageRow(
        nbk_id="NBK1247",
        passage_id=passage_id,
        chapter_section="management",
        heading_path="Management > Table 1",
        section_level=2,
        chunk_index=chunk_index,
        text="| Gene | Phenotype |\n| --- | --- |\n| BRCA1 | HBOC |",
        chapter_title="BRCA1- and BRCA2-Associated HBOC",
        chapter_last_updated=date(2025, 12, 1),
        gene_symbols=("BRCA1",),
        passage_type="table",
        table_data=table_data,
    )


@pytest.mark.asyncio
async def test_get_passage_table_data_absent_by_default() -> None:
    """header/rows/markdown_table absent from response without include=table_data."""
    pr = _make_table_row(
        table_data={
            "caption": "T1",
            "header": ["Gene", "Phenotype"],
            "rows": [["BRCA1", "HBOC"]],
        }
    )
    app = _build_app(focal=pr)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get("/passages/NBK1247:0099")
    assert resp.status_code == 200
    passage = resp.json()["passage"]
    assert passage.get("header") is None
    assert passage.get("rows") is None
    assert passage.get("markdown_table") is None


@pytest.mark.asyncio
async def test_get_passage_table_data_opt_in_populates_fields() -> None:
    """include=table_data populates v1.1-fenced header/rows for a table passage."""
    pr = _make_table_row(
        table_data={
            "caption": "Gene-phenotype correlations",
            "header": ["Gene", "Phenotype"],
            "rows": [["BRCA1", "HBOC"], ["BRCA2", "HBOC/PC"]],
        }
    )
    app = _build_app(focal=pr)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get("/passages/NBK1247:0099", params={"include": "table_data"})
    assert resp.status_code == 200
    passage = resp.json()["passage"]
    assert [c["text"] for c in passage["header"]] == ["Gene", "Phenotype"]
    assert passage["header"][0]["kind"] == "untrusted_text"
    assert [[c["text"] for c in r] for r in passage["rows"]] == [
        ["BRCA1", "HBOC"],
        ["BRCA2", "HBOC/PC"],
    ]
    # markdown_table was dropped (duplicated the now-fenced cells).
    assert "markdown_table" not in passage


@pytest.mark.asyncio
async def test_get_passage_table_data_narrative_always_null() -> None:
    """include=table_data on a narrative passage → header/rows both None."""
    pr = _make_row(passage_id="NBK1247:0010", chunk_index=10)
    app = _build_app(focal=pr)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get("/passages/NBK1247:0010", params={"include": "table_data"})
    assert resp.status_code == 200
    passage = resp.json()["passage"]
    assert passage.get("header") is None
    assert passage.get("rows") is None
    assert "markdown_table" not in passage
