"""MCP smoke tests for q/query parameter handling on search_passages."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastmcp import Client

from genereview_link.api.routes import passages as passages_routes
from genereview_link.config import ServerConfig
from genereview_link.retrieval.embeddings import FakeEmbeddingProvider
from genereview_link.retrieval.repository import LexicalPassageRow, PassageRow
from genereview_link.server_manager import UnifiedServerManager


def _row() -> LexicalPassageRow:
    return LexicalPassageRow(
        passage=PassageRow(
            nbk_id="NBK1",
            passage_id="NBK1:0001",
            chapter_section="summary",
            heading_path="Summary",
            section_level=1,
            chunk_index=1,
            text="BRCA1 summary text",
            chapter_title="BRCA Chapter",
            gene_symbols=("BRCA1",),
        ),
        phrase_rank=1.0,
        strict_rank=0.8,
        recall_rank=0.6,
        recall_overlap_count=1,
        lexical_rank=1.0,
        snippet="**BRCA1** summary text",
        lexical_rank_position=1,
    )


async def _build_mcp() -> tuple[Any, MagicMock]:
    repo = MagicMock()
    repo.search_passages = AsyncMock(return_value=[_row()])
    repo.active_embedding_table = AsyncMock(return_value="genereview_embeddings_bge384")
    repo.dense_scores_for_passages = AsyncMock(return_value={"NBK1:0001": 0.9})
    repo._dense_candidates_filtered = AsyncMock(
        return_value=[{"passage_id": "NBK1:0001", "dense_score": 0.9}]
    )
    repo.fetch_passages_by_ids = AsyncMock(return_value={})

    app = FastAPI()
    app.include_router(passages_routes.router)
    app.state.repository = repo
    app.state.embedder = FakeEmbeddingProvider(dim=384)

    mcp = await UnifiedServerManager().create_mcp_server(app, ServerConfig())
    return mcp, repo


async def _build_production_mcp() -> Any:
    manager = UnifiedServerManager()
    config = ServerConfig(transport="http", log_level="WARNING", enable_docs=False)
    app = manager.create_fastapi_app(config)
    return await manager.create_mcp_server(app, config)


async def _call_search_passages(client: Client[Any], arguments: dict[str, Any]) -> dict[str, Any]:
    """Call search_passages and return the envelope body.

    Response-Envelope Standard v1: a collection tool's `structured_content` is
    always the flat `{"success": true, "results": [...], "_meta": {...}}` frame
    at the top level — never double-wrapped under an outer `result` key.
    """
    result = await client.call_tool("search_passages", arguments)
    assert result.structured_content is not None
    assert isinstance(result.structured_content, dict)
    assert result.structured_content["success"] is True
    assert "results" in result.structured_content
    return result.structured_content


def _release_snapshot_definition(tool: Any) -> dict[str, Any]:
    """Project a live FastMCP tool into the router's reviewed snapshot shape."""
    return {
        "name": tool.name,
        "description": tool.description or "",
        "inputSchema": _canonical_json_schema(
            tool.parameters or {"type": "object", "properties": {}}
        ),
        "outputSchema": _canonical_json_schema(tool.output_schema),
        "annotations": (
            tool.annotations.model_dump(mode="json", by_alias=True, exclude_none=False)
            if tool.annotations is not None
            else None
        ),
        "execution": (
            tool.execution.model_dump(mode="json", by_alias=True, exclude_none=False)
            if tool.execution is not None
            else None
        ),
        "tags": sorted(tool.tags or []),
    }


def _canonical_json_schema(value: Any) -> Any:
    """Match the router snapshot's representation-only schema normalization."""
    if isinstance(value, dict):
        normalized: dict[str, Any] = {}
        for key, item in value.items():
            if key == "required" and isinstance(item, list):
                if not item:
                    continue
                if all(isinstance(name, str) for name in item):
                    normalized[key] = sorted(item)
                    continue
            normalized[key] = _canonical_json_schema(item)
        return normalized
    if isinstance(value, list):
        return [_canonical_json_schema(item) for item in value]
    return value


@pytest.mark.asyncio
async def test_mcp_search_passages_accepts_q_query_and_matching_dual_query() -> None:
    mcp, repo = await _build_mcp()

    async with Client(mcp) as client:
        q_result = await _call_search_passages(client, {"q": "BRCA1"})
        query_result = await _call_search_passages(client, {"query": "BRCA1"})
        both_result = await _call_search_passages(client, {"q": "BRCA1", "query": "BRCA1"})

    top_passage_ids = [
        q_result["results"][0]["passage_id"],
        query_result["results"][0]["passage_id"],
        both_result["results"][0]["passage_id"],
    ]
    assert top_passage_ids == ["NBK1:0001", "NBK1:0001", "NBK1:0001"]
    assert [call.args[0] for call in repo.search_passages.await_args_list] == [
        "BRCA1",
        "BRCA1",
        "BRCA1",
    ]


@pytest.mark.asyncio
async def test_mcp_search_passages_conflicting_q_and_query_returns_structured_error() -> None:
    mcp, _repo = await _build_mcp()

    async with Client(mcp) as client:
        result = await client.call_tool(
            "search_passages",
            {"q": "foo", "query": "bar"},
            raise_on_error=False,
        )

    body = result.structured_content
    assert body is not None
    assert body["success"] is False
    assert body["error_code"] == "invalid_input"
    assert body["retryable"] is False
    assert body["message"] == "both q and query supplied with different values"
    assert body["recovery_action"] == "pass only one of q or query, or pass the same string in both"


@pytest.mark.asyncio
async def test_mcp_search_passages_output_schema_suppressed_but_fence_on_wire() -> None:
    """Tool-Surface Budget v1: outputSchema is suppressed (None). The v1.1
    untrusted_text fence must still appear ON THE WIRE in structuredContent
    (v1.1a amendment: the `kind` literal must be present in the served payload,
    not in a declared schema)."""
    mcp, _repo = await _build_mcp()
    tool = next(tool for tool in await mcp.list_tools() if tool.name == "search_passages")
    # outputSchema is suppressed to cut the tool surface.
    assert tool.output_schema is None
    # ...but `q` is advertised as required so the behaviour gate can probe the tool.
    assert "q" in (tool.parameters.get("required") or [])

    async with Client(mcp) as client:
        body = await _call_search_passages(client, {"q": "BRCA1"})

    row = body["results"][0]
    # chapter_title is always fenced; the untrusted_text object rides on the wire.
    assert row["chapter_title"]["kind"] == "untrusted_text"
    assert {"kind", "text", "provenance", "raw_sha256"} <= set(row["chapter_title"])
    # the fenced snippet/text carrier is also an untrusted_text object.
    snippet = row.get("snippet")
    assert snippet is not None and snippet["kind"] == "untrusted_text"


@pytest.mark.asyncio
async def test_mcp_search_passages_fits_surface_budget_without_weakening_input_schema() -> None:
    mcp, _repo = await _build_mcp()
    tool = next(tool for tool in await mcp.list_tools() if tool.name == "search_passages")
    properties = tool.parameters["properties"]

    definition = _release_snapshot_definition(tool)
    assert len(json.dumps(definition)) // 4 <= 1_200

    assert tool.output_schema is None
    assert tool.parameters["required"] == ["q"]
    assert set(properties) == {
        "q",
        "query",
        "gene",
        "nbk_id",
        "sections",
        "heading_path_contains",
        "mode",
        "limit",
        "exclude",
        "include",
        "snippet_chars",
        "rerank",
    }
    assert all(prop.get("description") for prop in properties.values())
    assert properties["q"]["examples"] == ["breast cancer surveillance"]
    assert properties["gene"]["examples"] == ["BRCA1"]
    assert properties["nbk_id"]["examples"] == ["NBK1247"]
    assert properties["sections"]["examples"] == [["management"]]
    assert properties["exclude"]["examples"] == [["score_breakdown"]]
    assert properties["include"]["examples"] == [["score_breakdown"]]
    assert properties["sections"]["anyOf"][0]["items"]["enum"] == [
        "summary",
        "diagnosis",
        "clinical_features",
        "management",
        "genetic_counseling",
        "molecular_genetics",
        "resources",
        "other",
        "references",
    ]
    assert properties["mode"]["enum"] == ["brief", "full", "ids_only"]
    assert properties["rerank"]["enum"] == ["rrf", "lexical", "off"]


@pytest.mark.asyncio
async def test_mcp_full_production_registry_fits_surface_budgets() -> None:
    mcp = await _build_production_mcp()
    tools = await mcp.list_tools()
    definitions = [_release_snapshot_definition(tool) for tool in tools]

    assert len(tools) == 13
    assert all(len(json.dumps(definition)) // 4 <= 1_200 for definition in definitions)
    assert len(json.dumps(definitions)) // 4 <= 10_000


@pytest.mark.asyncio
async def test_mcp_search_passages_description_qualifies_ids_only_projection() -> None:
    mcp = await _build_production_mcp()
    tool = next(tool for tool in await mcp.list_tools() if tool.name == "search_passages")

    assert "highlighted triage snippets" not in tool.description
    assert "include/exclude do not apply to `ids_only`" in tool.description
    assert "`ids_only` omits `recommended_citation`" in tool.description


@pytest.mark.asyncio
async def test_mcp_search_passages_ids_only_mode_returns_slim_rows() -> None:
    mcp, _repo = await _build_mcp()

    async with Client(mcp) as client:
        result = await _call_search_passages(client, {"q": "BRCA1", "mode": "ids_only"})

    row = result["results"][0]
    assert set(row) == {
        "passage_id",
        "nbk_id",
        "chapter_section",
        "rrf_score",
        "lexical_rank_position",
    }
    assert "chapter_title" not in row
    assert "char_count" not in row
    assert "recommended_citation" not in row
    assert "source_url" not in row
