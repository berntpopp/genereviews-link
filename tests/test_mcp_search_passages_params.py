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

    app = FastAPI()
    app.include_router(passages_routes.router)
    app.state.repository = repo
    app.state.embedder = FakeEmbeddingProvider(dim=384)

    mcp = await UnifiedServerManager().create_mcp_server(app, ServerConfig())
    return mcp, repo


async def _call_search_passages(client: Client[Any], arguments: dict[str, Any]) -> dict[str, Any]:
    result = await client.call_tool("search_passages", arguments)
    assert result.structured_content is not None
    assert isinstance(result.structured_content, dict)
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

    assert result.is_error is True
    assert result.structured_content is None
    assert result.content
    error_text = result.content[0].text
    assert "conflicting_query_param" in error_text
    assert "both q and query supplied with different values" in error_text
