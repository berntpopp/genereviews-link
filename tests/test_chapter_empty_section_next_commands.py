"""#36 regression: empty summary section routes to get_abstract via next_commands."""

from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from genereview_link.api.routes import chapters as chapters_routes
from genereview_link.retrieval.repository import ChapterRow


def _make_chapter(pubmed_id: str | None) -> ChapterRow:
    return ChapterRow(
        nbk_id="NBK1247",
        short_name="NBK1247",
        title="Test Chapter",
        pubmed_id=pubmed_id,
        gene_symbols=(),
        omim_ids=(),
        authors=None,
        initial_pub_date=None,
        last_updated_date=date(2025, 12, 1),
    )


def _build_app_with_chapter(chapter: ChapterRow | None) -> FastAPI:
    app = FastAPI()
    app.include_router(chapters_routes.router)
    repo = MagicMock()
    repo.get_section = AsyncMock(return_value=[])
    repo.get_chapter_by_nbk = AsyncMock(return_value=chapter)
    app.state.repository = repo
    return app


@pytest.mark.asyncio
async def test_empty_summary_section_routes_to_get_abstract() -> None:
    app = _build_app_with_chapter(_make_chapter(pubmed_id="20301425"))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get("/chapters/NBK1247/sections/summary")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["passage_count"] == 0
    assert body["passages"] == []
    assert "get_abstract" in (body.get("note") or "")
    next_commands = body.get("next_commands")
    assert next_commands is not None and len(next_commands) == 1
    assert next_commands[0]["tool"] == "get_abstract"
    assert next_commands[0]["arguments"]["pubmed_id"] == "20301425"


@pytest.mark.asyncio
async def test_empty_summary_section_omits_next_commands_when_pubmed_id_missing() -> None:
    app = _build_app_with_chapter(_make_chapter(pubmed_id=None))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get("/chapters/NBK1247/sections/summary")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["passage_count"] == 0
    # Field MUST be absent from the JSON, not present as null.
    assert "next_commands" not in body, (
        f"next_commands must not leak as null when no pubmed_id is available; got {body!r}"
    )


@pytest.mark.asyncio
async def test_non_empty_section_response_omits_next_commands_field() -> None:
    """Regression: success-path section responses must not leak `next_commands: null`.

    `next_commands` is a hint field intended for the empty-unscraped-section
    branch only. A passive `null` value in every non-empty response is a
    silent API broadening that clients which check key presence (rather than
    truthiness) would falsely treat as an actionable hint.
    """
    from genereview_link.retrieval.repository import PassageRow

    pr = PassageRow(
        nbk_id="NBK1247",
        passage_id="NBK1247:0001",
        chapter_section="diagnosis",
        heading_path="Diagnosis",
        section_level=1,
        chunk_index=0,
        text="Diagnostic text.",
        chapter_title="Test Chapter",
        chapter_last_updated=date(2025, 12, 1),
    )
    app = FastAPI()
    app.include_router(chapters_routes.router)
    repo = MagicMock()
    repo.get_section = AsyncMock(return_value=[pr])
    repo.get_chapter_by_nbk = AsyncMock(return_value=_make_chapter(pubmed_id="20301425"))
    app.state.repository = repo

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get("/chapters/NBK1247/sections/diagnosis")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # Field MUST be absent from successful non-empty responses, not present as null.
    assert "next_commands" not in body, f"next_commands leaked into success-path response: {body!r}"
