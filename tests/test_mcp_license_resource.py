"""Tests for the genereview://license MCP resource.

Verifies that:
- The resource is registered on the MCP server (genereview://license present).
- The MCP tool get_license is NOT exposed (replaced by the resource).
- The resource payload matches the REST /license route shape exactly.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from fastapi import FastAPI

from genereview_link.models.genereview_models import LicenseNotice


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
        # Return a plain FastMCP so resource/prompt registration still works.
        return FastMCP(name=kwargs.get("name", "test"), instructions=kwargs.get("instructions"))

    monkeypatch.setattr(FastMCP, "from_fastapi", staticmethod(patched_from_fastapi))

    mgr = UnifiedServerManager()
    app = FastAPI()
    mcp: FastMCP = asyncio.run(mgr.create_mcp_server(app, ServerConfig()))
    return mcp


def test_license_resource_registered(monkeypatch: pytest.MonkeyPatch) -> None:
    """genereview://license must appear in the MCP resource list."""
    mcp = _build_mcp(monkeypatch)
    resources = asyncio.run(mcp.list_resources())
    uris = [str(r.uri) for r in resources]
    assert "genereview://license" in uris, f"Expected genereview://license in {uris}"


def test_get_license_tool_not_exposed(monkeypatch: pytest.MonkeyPatch) -> None:
    """get_license must NOT appear in the MCP tool list (promoted to resource)."""
    mcp = _build_mcp(monkeypatch)
    tools = asyncio.run(mcp.list_tools())
    tool_names = [t.name for t in tools]
    assert "get_license" not in tool_names, (
        f"get_license should be a resource, not a tool; found tools: {tool_names}"
    )


def test_license_resource_payload_matches_rest_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    """Resource JSON payload must contain the same fields as the REST /license response."""
    mcp = _build_mcp(monkeypatch)
    result = asyncio.run(mcp.read_resource("genereview://license"))
    # read_resource returns a ResourceResult with a contents list of ResourceContent items.
    assert result.contents, "read_resource returned empty contents"
    first = result.contents[0]
    raw = first.content if hasattr(first, "content") else str(first)
    payload = json.loads(raw)

    expected_notice = LicenseNotice()
    assert payload["copyright"] == expected_notice.copyright
    assert payload["terms_url"] == expected_notice.terms_url
    assert payload["data_source"] == expected_notice.data_source
    assert payload["data_source_url"] == expected_notice.data_source_url
    assert payload["notes"] == expected_notice.notes
    assert "University of Washington" in payload["copyright"]
