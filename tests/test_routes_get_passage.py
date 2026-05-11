"""GET /passages/{passage_id} route behaviour."""

from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from genereview_link.api.routes import passages as passages_routes
from genereview_link.retrieval.repository import PassageRow


def _build_app(*, passage: PassageRow | None) -> FastAPI:
    app = FastAPI()
    app.include_router(passages_routes.router)
    repo = MagicMock()
    repo.get_passage = AsyncMock(return_value=passage)
    app.state.repository = repo
    return app


@pytest.mark.asyncio
async def test_get_passage_returns_200_with_chapter_title():
    pr = PassageRow(
        nbk_id="NBK1247",
        passage_id="NBK1247:0022",
        chapter_section="management",
        heading_path="Management > Other",
        section_level=2,
        chunk_index=22,
        text="risk-reducing surgery text",
        chapter_title="BRCA1- and BRCA2-Associated HBOC",
        chapter_last_updated=date(2025, 12, 1),
        gene_symbols=("BRCA1", "BRCA2"),
    )
    app = _build_app(passage=pr)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get("/passages/NBK1247:0022")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["passage_id"] == "NBK1247:0022"
    assert body["chapter_title"] == "BRCA1- and BRCA2-Associated HBOC"
    assert body["chapter_last_updated"] == "2025-12-01"
    assert body["gene_symbols"] == ["BRCA1", "BRCA2"]
    assert body["char_count"] == len("risk-reducing surgery text")


@pytest.mark.asyncio
async def test_get_passage_returns_404_for_unknown_id():
    app = _build_app(passage=None)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get("/passages/NBK9999:9999")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_passage_rejects_malformed_id_with_422():
    app = _build_app(passage=None)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get("/passages/not-a-passage-id")
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_unknown_passage_returns_structured_404():
    app = _build_app(passage=None)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get("/passages/NBK9999:9999")
    assert resp.status_code == 404
    detail = resp.json()["detail"]
    assert detail["code"] == "passage_not_found"
    assert "NBKxxxx:NNNN" in detail["recovery_hint"]
    assert detail["next_commands"][0]["tool"] == "search_passages"
