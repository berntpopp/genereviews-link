"""MCP integration tests for structured REST error passthrough."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastmcp import Client

from genereview_link.api.routes import chapters as chapters_routes
from genereview_link.api.routes import passages as passages_routes
from genereview_link.config import ServerConfig
from genereview_link.retrieval.embeddings import FakeEmbeddingProvider
from genereview_link.server_manager import UnifiedServerManager


async def _build_mcp() -> Any:
    repo = MagicMock()
    repo.get_chapter_metadata = AsyncMock(return_value=None)
    repo.search_passages = AsyncMock(return_value=[])
    repo.active_embedding_table = AsyncMock(return_value="genereview_embeddings_bge384")
    repo.dense_scores_for_passages = AsyncMock(return_value={})
    repo._dense_candidates_filtered = AsyncMock(return_value=[])
    repo.fetch_passages_by_ids = AsyncMock(return_value={})

    app = FastAPI()
    app.include_router(chapters_routes.router)
    app.include_router(passages_routes.router)
    app.state.repository = repo
    app.state.embedder = FakeEmbeddingProvider(dim=384)

    return await UnifiedServerManager().create_mcp_server(app, ServerConfig())


def _error_payload(result: Any) -> dict[str, Any]:
    """Response-Envelope Standard v1: errors carry structured_content in-band
    (success: false), not just an opaque content[] text blob. See
    docs/RESPONSE-ENVELOPE-STANDARD-v1.md and genereview_link.mcp.envelope."""
    assert result.structured_content is not None
    payload = result.structured_content
    assert payload["success"] is False
    assert result.content
    text = result.content[0].text
    assert "HTTPStatusError" not in text
    assert "Traceback" not in text
    return payload


@pytest.mark.asyncio
async def test_mcp_get_chapter_metadata_returns_structured_chapter_not_found() -> None:
    mcp = await _build_mcp()

    async with Client(mcp) as client:
        result = await client.call_tool(
            "get_chapter_metadata",
            {"nbk_id": "NBK999999"},
            raise_on_error=False,
        )

    payload = _error_payload(result)
    assert payload["error_code"] == "not_found"
    assert payload["message"] == "chapter 'NBK999999' not in corpus"
    assert payload["recovery_action"]
    assert payload["_meta"]["next_commands"][0]["tool"] == "search_passages"


@pytest.mark.asyncio
async def test_mcp_search_passages_error_uses_same_structured_shape() -> None:
    mcp = await _build_mcp()

    async with Client(mcp) as client:
        result = await client.call_tool(
            "search_passages",
            {"q": "BRCA1", "query": "TP53"},
            raise_on_error=False,
        )

    payload = _error_payload(result)
    assert payload["error_code"] == "invalid_input"
    assert payload["message"] == "both q and query supplied with different values"
    assert payload["recovery_action"] == (
        "pass only one of q or query, or pass the same string in both"
    )
    assert payload["_meta"]["next_commands"] == []
