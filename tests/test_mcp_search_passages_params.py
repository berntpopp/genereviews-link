"""MCP smoke tests for q/query parameter handling on search_passages."""

from __future__ import annotations

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
async def test_mcp_search_passages_ids_only_schema_declares_envelope_frame() -> None:
    """The declared outputSchema is the Response-Envelope frame (success/_meta)
    AND makes the fenced untrusted_text object (kind const) reachable inside the
    ``results[*].text`` list-item schema — see envelope.reshape_output_schema."""
    mcp, _repo = await _build_mcp()
    tool = next(tool for tool in await mcp.list_tools() if tool.name == "search_passages")

    schema = tool.output_schema
    assert schema["type"] == "object"
    assert set(schema["required"]) == {"success", "_meta"}
    assert schema["additionalProperties"] is True
    # v1.1: the untrusted_text `kind` const literal must be declared inside the
    # array items schema (a bare permissive array would hide it).
    item = schema["properties"]["results"]["items"]
    text_schema = item["properties"]["text"]["anyOf"][0]
    assert text_schema["properties"]["kind"]["const"] == "untrusted_text"
    assert set(text_schema["required"]) == {"kind", "text", "provenance", "raw_sha256"}


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
