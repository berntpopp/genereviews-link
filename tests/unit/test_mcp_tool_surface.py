"""Regression test: MCP tool surface keeps canonical names after the
identity-mapped ``mcp_custom_names`` dict is removed (#19)."""

from __future__ import annotations

import pytest
from fastapi import FastAPI

from genereview_link.api.routes import abstract, fulltext, genereview, links, search
from genereview_link.api.routes import chapters as chapters_routes
from genereview_link.api.routes import license as license_routes
from genereview_link.api.routes import passages as passages_routes
from genereview_link.api.routes import tables as tables_routes
from genereview_link.config import ServerConfig
from genereview_link.retrieval.embeddings import FakeEmbeddingProvider

CANONICAL_TOOLS = {
    "search_passages",
    "get_chapter_metadata",
    "get_chapter_section",
    "get_passage",
    "get_table",
    "get_passages_batch",
    "get_genereview_summary",
    "search_genereviews",
    "get_abstract",
    "get_links",
    "get_fulltext",
    "get_license",
}


def _build_app_with_state() -> FastAPI:
    """Stand up a minimal FastAPI app with the routes the MCP server walks."""
    app = FastAPI()
    app.include_router(search.router)
    app.include_router(abstract.router)
    app.include_router(links.router)
    app.include_router(fulltext.router)
    app.include_router(genereview.router)
    app.include_router(passages_routes.router)
    app.include_router(chapters_routes.router)
    app.include_router(tables_routes.router)
    app.include_router(license_routes.router)
    app.state.repository = None
    app.state.pool = None
    app.state.embedder = FakeEmbeddingProvider(dim=384)
    app.state.gene_index = None
    app.state.corpus_version = None
    app.state.dense_model_id = "test"
    app.state.embedding_dim = 384
    return app


@pytest.mark.asyncio
async def test_mcp_tools_keep_canonical_names_after_dict_removal() -> None:
    """The dead ``mcp_custom_names`` dict mapped every key to itself.
    Removing it must not rename or drop any canonical tool."""
    from genereview_link.server_manager import UnifiedServerManager

    app = _build_app_with_state()
    mgr = UnifiedServerManager()
    mcp = await mgr.create_mcp_server(app, ServerConfig())

    tools = await mcp.list_tools()
    tool_names = {tool.name for tool in tools}

    missing = CANONICAL_TOOLS - tool_names
    assert not missing, f"canonical tools missing after #19: {missing}"


def test_server_manager_no_longer_defines_mcp_custom_names() -> None:
    """Grep guard: the dead dict must not be reintroduced."""
    from pathlib import Path

    source = Path("genereview_link/server_manager.py").read_text()
    assert "mcp_custom_names" not in source, (
        "mcp_custom_names dict is dead code (every key mapped to itself); do not reintroduce it"
    )
