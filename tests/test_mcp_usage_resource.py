"""Verify the genereview://usage MCP resource is registered and returns the markdown content."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from fastapi import FastAPI


def _build_mcp(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Build a real FastMCP instance using create_mcp_server.

    We stub out FastMCP.from_fastapi to avoid spinning up full FastAPI
    routing (and its DB/service dependencies), then let create_mcp_server
    run the resource + prompt registration logic against the real FastMCP
    instance that from_fastapi would normally return.
    """
    from fastmcp import FastMCP

    from genereview_link.config import ServerConfig
    from genereview_link.server_manager import UnifiedServerManager

    def patched_from_fastapi(*args: Any, **kwargs: Any) -> FastMCP:
        return FastMCP(name=kwargs.get("name", "test"), instructions=kwargs.get("instructions"))

    monkeypatch.setattr(FastMCP, "from_fastapi", staticmethod(patched_from_fastapi))

    mgr = UnifiedServerManager()
    app = FastAPI()
    mcp: FastMCP = asyncio.run(mgr.create_mcp_server(app, ServerConfig()))
    return mcp


def test_usage_resource_registered(monkeypatch: pytest.MonkeyPatch) -> None:
    """genereview://usage must appear in the MCP resource list."""
    mcp = _build_mcp(monkeypatch)
    resources = asyncio.run(mcp.list_resources())
    uris = [str(r.uri) for r in resources]
    assert "genereview://usage" in uris, f"Expected genereview://usage in {uris}"


def test_usage_resource_content_has_expected_sections(monkeypatch: pytest.MonkeyPatch) -> None:
    """The usage resource content must contain all required headings."""
    from genereview_link.api.resources.usage import USAGE_RESOURCE_MARKDOWN

    for heading in (
        "# GeneReview-Link Usage Guide",
        "## Pipeline",
        "## Filters",
        "## Rerank modes",
        "## Response modes",
        "## `snippet_chars` (brief mode only)",
        "## Diagnostics",
        "## Batch fetch",
        "## Affordances on existing tools",
        "## Table ID naming",
        "## Chapter date semantics",
        "## Latency profile",
    ):
        assert heading in USAGE_RESOURCE_MARKDOWN, f"Missing heading: {heading}"


def test_usage_resource_documents_search_score_and_diagnostic_semantics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The usage resource must document the score visibility and diagnostics contract."""
    from genereview_link.api.resources.usage import USAGE_RESOURCE_MARKDOWN

    normalized = " ".join(USAGE_RESOURCE_MARKDOWN.split())
    expected_fragments = (
        "`rrf_score`, `lexical_score`, `lexical_rank_position`, and `dense_rank_position`",
        "`score_breakdown` remains the opt-in deep view",
        "`lexical_candidate_count` and `dense_candidate_count` are post-filter",
        "`dense_candidate_count` is `null` when dense retrieval is not run",
        "`unfiltered_lexical_count` is normally `null`",
        "populated on empty filtered responses after the second unfiltered lexical probe",
        "A non-zero `unfiltered_lexical_count` plus suggestion codes indicates filters "
        "likely dropped candidates",
        '"section-filter-drops-all"',
        "`gene-filter-drops-all`, `broaden-query`, `section-filter-drops-all`, "
        "`nbk-id-filter-drops-all`",
    )
    for fragment in expected_fragments:
        assert fragment in normalized, f"Missing usage text: {fragment}"


def test_usage_resource_documents_ids_only_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    """The usage resource must document the ids_only lean row fields."""
    from genereview_link.api.resources.usage import USAGE_RESOURCE_MARKDOWN

    normalized = " ".join(USAGE_RESOURCE_MARKDOWN.split())
    expected = "{passage_id, rrf_score, lexical_rank_position, chapter_section}"
    assert expected in normalized


def test_usage_resource_documents_search_aliases_and_heading_filter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The usage resource must document query aliases and heading path filtering."""
    from genereview_link.api.resources.usage import USAGE_RESOURCE_MARKDOWN

    normalized = " ".join(USAGE_RESOURCE_MARKDOWN.split())
    expected_fragments = (
        "`search_passages` accepts both `q` and `query`",
        "Omit both and the API returns a structured 422",
        "`missing_query`",
        "Providing both with different values returns a structured 422",
        "`conflicting_query_param`",
        "`heading_path_contains` on `search_passages`",
        "`heading_path_contains` also applies to `get_chapter_section`",
    )
    for fragment in expected_fragments:
        assert fragment in normalized, f"Missing usage text: {fragment}"


def test_usage_resource_returns_markdown(monkeypatch: pytest.MonkeyPatch) -> None:
    """read_resource for genereview://usage must return the markdown string."""
    from genereview_link.api.resources.usage import USAGE_RESOURCE_MARKDOWN

    mcp = _build_mcp(monkeypatch)
    result = asyncio.run(mcp.read_resource("genereview://usage"))
    assert result.contents, "read_resource returned empty contents"
    first = result.contents[0]
    raw = first.content if hasattr(first, "content") else str(first)
    assert raw == USAGE_RESOURCE_MARKDOWN


def test_server_instructions_manifests_both_resources(monkeypatch: pytest.MonkeyPatch) -> None:
    """instructions= must reference both resource URIs and fit within the 1000-char budget."""
    mcp = _build_mcp(monkeypatch)
    instr = mcp.instructions or ""
    assert "genereview://license" in instr
    assert "genereview://usage" in instr
    # Length check — the whole point of Task 15 is to keep instructions tight.
    assert len(instr) < 1000, f"instructions length {len(instr)} exceeds 1000-char budget"
