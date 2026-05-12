"""Regression test: MCP tool dispatch must hit the same FastAPI app that serves HTTP.

When FastMCP is constructed against a "discovery-only" FastAPI app whose
lifespan never runs, ``app.state.repository`` stays ``None`` and the
``search_passages`` / ``get_chapter_section`` routes return 503 — even though
direct HTTP requests to the serving app return 200. This test pins the
behaviour by simulating an MCP tool call through the same path FastMCP uses:
an in-process HTTP call into the FastAPI app's router.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from genereview_link.api.routes import chapters as chapters_routes
from genereview_link.api.routes import passages as passages_routes
from genereview_link.retrieval.embeddings import FakeEmbeddingProvider


def _build_app_with_state() -> FastAPI:
    """Stand up a tiny FastAPI app and seed app.state with a working repo
    + embedder, simulating what the real lifespan would do."""
    app = FastAPI()
    app.include_router(passages_routes.router)
    app.include_router(chapters_routes.router)

    fake_repo = MagicMock()
    fake_repo.search_passages = AsyncMock(return_value=[])
    fake_repo.active_embedding_table = AsyncMock(return_value="genereview_embeddings_bge384")
    fake_repo.dense_scores_for_passages = AsyncMock(return_value={})
    from genereview_link.retrieval.repository import PassageRow

    fake_repo.get_section = AsyncMock(
        return_value=[
            PassageRow(
                nbk_id="NBK1",
                passage_id="NBK1:0001",
                chapter_section="summary",
                heading_path="Summary",
                section_level=1,
                chunk_index=0,
                text="seeded",
                chapter_title="Test",
                chapter_last_updated=None,
                gene_symbols=(),
            )
        ]
    )
    app.state.repository = fake_repo
    app.state.embedder = FakeEmbeddingProvider(dim=384)
    return app


@pytest.mark.asyncio
async def test_passages_search_uses_app_state_repository() -> None:
    """/passages/search must read app.state.repository at request time."""
    app = _build_app_with_state()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get(
            "/passages/search", params={"q": "anything", "limit": 5, "rerank": "off"}
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["results"] == []
    assert "_meta" in body


@pytest.mark.asyncio
async def test_passages_search_503_when_repository_missing() -> None:
    """When app.state.repository is None the route MUST 503, not crash."""
    app = FastAPI()
    app.include_router(passages_routes.router)
    app.state.repository = None
    app.state.embedder = FakeEmbeddingProvider(dim=384)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get("/passages/search", params={"q": "x"})
    assert resp.status_code == 503
    assert "Postgres repository unavailable" in resp.text


@pytest.mark.asyncio
async def test_chapter_section_uses_app_state_repository() -> None:
    """/chapters/{nbk}/sections/{section} must read app.state.repository at request time."""
    app = _build_app_with_state()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get("/chapters/NBK1/sections/summary")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["nbk_id"] == "NBK1"
    assert body["chapter_section"] == "summary"
    assert body["passages"][0]["text"] == "seeded"


def test_unified_server_uses_single_app_instance(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression for the 503-via-MCP bug: ``start_unified_server`` must build
    FastMCP against the *same* FastAPI instance it serves on. If a separate
    discovery app is used, FastMCP dispatches tool calls to it instead of the
    one with the populated app.state — yielding 503s for repository routes.
    """
    from genereview_link.server_manager import UnifiedServerManager

    mgr = UnifiedServerManager()
    created_apps: list[FastAPI] = []
    real_create = mgr.create_fastapi_app

    def tracking_create(config: Any) -> FastAPI:
        app = real_create(config)
        created_apps.append(app)
        return app

    monkeypatch.setattr(mgr, "create_fastapi_app", tracking_create)

    captured: dict[str, Any] = {}

    async def fake_create_mcp(app: FastAPI, _config: Any) -> MagicMock:
        captured["mcp_built_against"] = app
        fake_mcp = MagicMock()
        fake_mcp.http_app = MagicMock(
            return_value=MagicMock(
                lifespan=lambda _app: _DummyCtx(),
            )
        )
        return fake_mcp

    monkeypatch.setattr(mgr, "create_mcp_server", fake_create_mcp)

    async def fake_serve() -> None:
        return None

    class _Server:
        def __init__(self, *a: Any, **kw: Any) -> None: ...
        async def serve(self) -> None:
            return None

    import uvicorn

    monkeypatch.setattr(uvicorn, "Server", _Server)

    import asyncio

    from genereview_link.config import ServerConfig

    asyncio.run(mgr.start_unified_server(ServerConfig()))

    # Exactly ONE FastAPI app must be created — and it must be the one
    # passed to create_mcp_server (i.e. the serving app).
    assert len(created_apps) == 1, f"Expected 1 FastAPI app, got {len(created_apps)}"
    assert captured["mcp_built_against"] is created_apps[0], (
        "FastMCP must be constructed against the serving FastAPI app so tool "
        "calls hit the request-time app.state."
    )
    assert mgr.app is created_apps[0]


class _DummyCtx:
    async def __aenter__(self) -> None: ...
    async def __aexit__(self, *_a: Any) -> None: ...


@pytest.mark.asyncio
async def test_get_passage_uses_app_state_repository() -> None:
    """GET /passages/{passage_id} reads app.state.repository at request time."""
    from datetime import date

    from genereview_link.retrieval.repository import PassageRow

    app = _build_app_with_state()
    pr = PassageRow(
        nbk_id="NBK1",
        passage_id="NBK1:0001",
        chapter_section="management",
        heading_path="Management > X",
        section_level=2,
        chunk_index=1,
        text="seeded passage",
        chapter_title="Test",
        chapter_last_updated=date(2025, 12, 1),
        gene_symbols=("TG",),
    )
    app.state.repository.get_passage_window = AsyncMock(return_value=(pr, [], [], False, False))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get("/passages/NBK1:0001")
    assert resp.status_code == 200, resp.text
    assert resp.json()["passage"]["chapter_title"] == "Test"


def test_server_instructions_are_set(monkeypatch: pytest.MonkeyPatch) -> None:
    """create_mcp_server passes instructions to FastMCP via from_fastapi kwargs."""
    import asyncio

    from fastmcp import FastMCP

    from genereview_link.server_manager import UnifiedServerManager

    captured: dict[str, object] = {}

    def fake_from_fastapi(*args: Any, **kwargs: Any) -> MagicMock:
        captured["instructions"] = kwargs.get("instructions")
        captured["name"] = kwargs.get("name")
        return MagicMock()

    monkeypatch.setattr(FastMCP, "from_fastapi", staticmethod(fake_from_fastapi))

    from genereview_link.config import ServerConfig

    mgr = UnifiedServerManager()
    app = mgr.create_fastapi_app(ServerConfig())
    asyncio.run(mgr.create_mcp_server(app, ServerConfig()))

    assert captured["instructions"] is not None
    instructions = captured["instructions"]
    assert isinstance(instructions, str)
    assert "Canonical pipeline" in instructions
    assert "search_passages" in instructions
    assert "Research use only" in instructions


def test_find_in_section_prompt_is_registered() -> None:
    """find_in_section returns a usable prompt string."""
    from genereview_link.mcp.prompts import find_in_section

    text = find_in_section(gene_symbol="BRCA1", section="management")
    assert "BRCA1" in text
    assert "management" in text
    assert "search_passages" in text
