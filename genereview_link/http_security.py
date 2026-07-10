"""Shared strict Host and Origin protection for HTTP transports."""

from typing import Any

from fastapi import FastAPI
from fastmcp import FastMCP
from fastmcp.server.http import HostOriginGuardMiddleware

from genereview_link.config import settings


def add_host_origin_guard(app: FastAPI) -> None:
    """Protect every FastAPI route before request processing."""
    app.add_middleware(
        HostOriginGuardMiddleware,
        allowed_hosts=settings.MCP_ALLOWED_HOSTS,
        allowed_origins=settings.MCP_ALLOWED_ORIGINS,
        mode="strict",
    )


def create_mcp_http_app(mcp: FastMCP, path: str) -> Any:
    """Build the MCP app with the same strict policy at its native layer."""
    return mcp.http_app(
        path=path,
        stateless_http=True,
        json_response=True,
        host_origin_protection=True,
        allowed_hosts=settings.MCP_ALLOWED_HOSTS,
        allowed_origins=settings.MCP_ALLOWED_ORIGINS,
    )
