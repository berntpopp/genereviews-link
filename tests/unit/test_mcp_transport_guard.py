"""Construction guard: assert server_manager builds the MCP app stateless+json
and mounts it at root.

TDD guard: write RED → fix code → see GREEN.
"""

from __future__ import annotations

import inspect

from genereview_link import http_security, server_manager


def test_mcp_app_is_rooted_stateless_json() -> None:
    src = inspect.getsource(server_manager) + inspect.getsource(http_security)
    assert 'http_app(path="/")' not in src, (
        "must bake the mcp_path into http_app, not pass path='/'"
    )
    assert "stateless_http=True" in src, "http_app must set stateless_http=True"
    assert "json_response=True" in src, "http_app must set json_response=True"
    assert "host_origin_protection=True" in src
    assert "allowed_hosts=settings.MCP_ALLOWED_HOSTS" in src
    assert "allowed_origins=settings.MCP_ALLOWED_ORIGINS" in src
    assert 'mount("/"' in src, "MCP app must be mounted at root with mount('/')"
