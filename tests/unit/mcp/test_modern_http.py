"""MCP 2026-07-28 (sessionless, modern era) over the real stateless HTTP app.

Drives ``server/discover`` -> ``tools/list`` -> ``tools/call`` with the modern
``mcp-method`` / ``mcp-name`` headers, and proves an unknown hostile tool name is
answered with the fixed not-found envelope (never reflected).
"""

from __future__ import annotations

import json
from typing import Any

from httpx import ASGITransport, AsyncClient

from genereview_link import http_security
from tests.unit.mcp.test_notfound_guard import _make_mcp

_MODERN_VERSION = "2026-07-28"
_MODERN_META = {
    "io.modelcontextprotocol/protocolVersion": _MODERN_VERSION,
    "io.modelcontextprotocol/clientCapabilities": {},
    "io.modelcontextprotocol/clientInfo": {"name": "genereviews-host-test", "version": "1.0.0"},
}


def _headers(method: str, name: str | None = None) -> dict[str, str]:
    headers = {
        "accept": "application/json, text/event-stream",
        "content-type": "application/json",
        "mcp-protocol-version": _MODERN_VERSION,
        "mcp-method": method,
    }
    if name is not None:
        headers["mcp-name"] = name
    return headers


async def _post(client: AsyncClient, rid: int, method: str, params: dict[str, Any]) -> Any:
    name = params.get("name")
    return await client.post(
        "/mcp",
        headers=_headers(method, name),
        json={
            "jsonrpc": "2.0",
            "id": rid,
            "method": method,
            "params": {"_meta": _MODERN_META, **params},
        },
    )


async def test_modern_mcp_http_discovers_lists_calls_and_fences_unknown_tool() -> None:
    hostile = "ignore-instructions-secret"
    app = http_security.create_mcp_http_app(await _make_mcp(), "/mcp")
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://localhost") as client,
    ):
        discover = await _post(client, 1, "server/discover", {})
        listed = await _post(client, 2, "tools/list", {})
        called = await _post(client, 3, "tools/call", {"name": "get_license", "arguments": {}})
        unknown = await _post(client, 4, "tools/call", {"name": hostile, "arguments": {}})

    assert discover.status_code == 200, discover.text
    assert _MODERN_VERSION in discover.json()["result"]["supportedVersions"]
    assert "mcp-session-id" not in discover.headers
    assert listed.status_code == 200
    assert "get_license" in {tool["name"] for tool in listed.json()["result"]["tools"]}
    assert called.status_code == 200
    called_result = called.json()["result"]
    assert called_result.get("isError") in (None, False)
    assert json.loads(called_result["content"][0]["text"])["success"] is True
    assert unknown.status_code == 200
    unknown_result = unknown.json()["result"]
    assert unknown_result["isError"] is True
    assert unknown_result["structuredContent"]["error_code"] == "not_found"
    assert hostile not in unknown.text
