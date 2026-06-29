"""Construction guard: assert server_manager builds the MCP app stateless+json
and mounts it at root.

TDD guard: write RED → fix code → see GREEN.
"""

from __future__ import annotations

import inspect

from genereview_link import server_manager


def test_mcp_app_is_rooted_stateless_json() -> None:
    src = inspect.getsource(server_manager)
    assert 'http_app(path="/")' not in src, (
        "must bake the mcp_path into http_app, not pass path='/'"
    )
    assert "stateless_http=True" in src, "http_app must set stateless_http=True"
    assert "json_response=True" in src, "http_app must set json_response=True"
    assert 'mount("/"' in src, "MCP app must be mounted at root with mount('/')"
