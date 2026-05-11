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
    assert passage["chapter_title"] == "BRCA1- and BRCA2-Associated HBOC"
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
