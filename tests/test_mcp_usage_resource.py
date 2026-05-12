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
        "## Diagnostics on empty results",
        "## Batch fetch",
        "## Affordances on existing tools",
        "## Table ID naming",
        "## Chapter date semantics",
        "## Latency profile",
    ):
        assert heading in USAGE_RESOURCE_MARKDOWN, f"Missing heading: {heading}"


def test_usage_resource_returns_markdown(monkeypatch: pytest.MonkeyPatch) -> None:
    """read_resource for genereview://usage must return the markdown string."""
    from genereview_link.api.resources.usage import USAGE_RESOURCE_MARKDOWN

    mcp = _build_mcp(monkeypatch)
    result = asyncio.run(mcp.read_resource("genereview://usage"))
    assert result.contents, "read_resource returned empty contents"
    first = result.contents[0]
    raw = first.content if hasattr(first, "content") else str(first)
    assert raw == USAGE_RESOURCE_MARKDOWN
