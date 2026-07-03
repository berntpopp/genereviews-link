"""GeneFoundry Response-Envelope Standard v1 conformance tests.

Pins the flat-banner frame for genereviews-link MCP tools:

- Success (single-item tool): ``{"success": true, "result": {...}, "_meta": {...}}``
- Success (collection tool): ``{"success": true, "results": [...], "_meta": {...}}``
  at the TOP LEVEL of ``structuredContent`` — no ``{"result": {"results": [...]}}``
  double-wrap.
- Failure: a flat, in-band error frame (``error_code``/``retryable``/``recovery_action``)
  returned as ``structuredContent`` (not just an opaque text blob), so the LLM can
  branch on a structured failure.

See docs/RESPONSE-ENVELOPE-STANDARD-v1.md (genefoundry-router-standards) for the
normative frame.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastmcp import Client

from genereview_link.api.routes import chapters as chapters_routes
from genereview_link.api.routes import license as license_routes
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
    app.include_router(license_routes.router)
    app.include_router(chapters_routes.router)
    app.include_router(passages_routes.router)
    app.state.repository = repo
    app.state.embedder = FakeEmbeddingProvider(dim=384)

    return await UnifiedServerManager().create_mcp_server(app, ServerConfig())


@pytest.mark.asyncio
async def test_get_license_success_envelope_has_banner_keys() -> None:
    """A single-item tool returns {success, result, _meta} at the top level."""
    mcp = await _build_mcp()

    async with Client(mcp) as client:
        result = await client.call_tool("get_license", {})

    body = result.structured_content
    assert body is not None
    assert body["success"] is True
    assert "result" in body, "single-item tool must nest its payload under 'result'"
    assert "results" not in body
    assert body["result"]["data_source"] == "NCBI Bookshelf — GeneReviews"

    meta = body["_meta"]
    assert meta["tool"] == "get_license"
    assert meta["unsafe_for_clinical_use"] is True
    assert "request_id" in meta


@pytest.mark.asyncio
async def test_search_passages_success_envelope_top_level_results() -> None:
    """A collection tool returns top-level 'results' — no {"result": {"results": [...]}}."""
    mcp = await _build_mcp()

    async with Client(mcp) as client:
        result = await client.call_tool("search_passages", {"q": "BRCA1"})

    body = result.structured_content
    assert body is not None
    assert body["success"] is True
    assert body["results"] == [], "results must be promoted to the top level"
    assert "result" not in body, "collection tools must not double-wrap under 'result'"

    meta = body["_meta"]
    assert meta["tool"] == "search_passages"
    assert meta["unsafe_for_clinical_use"] is True


@pytest.mark.asyncio
async def test_forced_failure_has_flat_error_shape() -> None:
    """A not-found error surfaces as an in-band flat envelope, not an opaque text blob."""
    mcp = await _build_mcp()

    async with Client(mcp) as client:
        result = await client.call_tool(
            "get_chapter_metadata",
            {"nbk_id": "NBK999999"},
            raise_on_error=False,
        )

    body = result.structured_content
    assert body is not None, "errors must carry structured_content, not just content[].text"
    assert body["success"] is False
    assert body["error_code"] == "not_found"
    assert isinstance(body["retryable"], bool)
    assert body["retryable"] is False
    assert body["recovery_action"]
    assert "chapter 'NBK999999'" in body["message"]

    meta = body["_meta"]
    assert meta["tool"] == "get_chapter_metadata"
    assert meta["unsafe_for_clinical_use"] is True


@pytest.mark.asyncio
async def test_all_tools_declare_read_only_open_world_annotations() -> None:
    """Every genereview-link tool is a read-only lookup against an
    externally-evolving corpus; the standard requires READ_ONLY_OPEN_WORLD."""
    mcp = await _build_mcp()

    tools = await mcp.list_tools()
    assert tools
    for tool in tools:
        assert tool.annotations is not None, f"{tool.name} missing annotations"
        assert tool.annotations.readOnlyHint is True
        assert tool.annotations.openWorldHint is True
