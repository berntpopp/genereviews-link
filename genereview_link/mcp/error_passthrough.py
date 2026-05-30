"""Structured error passthrough for FastMCP OpenAPI-generated tools."""

from __future__ import annotations

import json
from typing import Any, NoReturn

import httpx
from fastmcp.exceptions import ToolError
from fastmcp.server.providers.openapi import OpenAPITool
from fastmcp.tools.base import ToolResult


def _find_http_status_response(exc: BaseException) -> httpx.Response | None:
    """Find an httpx response in an exception cause or context chain."""
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, httpx.HTTPStatusError):
            return current.response
        response = getattr(current, "response", None)
        if isinstance(response, httpx.Response):
            return response
        current = current.__cause__ or current.__context__
    return None


def _structured_detail(response: httpx.Response) -> dict[str, Any] | None:
    """Extract the repository's StructuredHTTPException detail body."""
    try:
        body = response.json()
    except ValueError:
        return None
    if not isinstance(body, dict):
        return None
    detail = body.get("detail")
    if not isinstance(detail, dict):
        return None
    code = detail.get("code")
    message = detail.get("message")
    recovery_hint = detail.get("recovery_hint")
    if not all(isinstance(value, str) for value in (code, message, recovery_hint)):
        return None

    field_errors = detail.get("field_errors", [])
    next_commands = detail.get("next_commands", [])
    if not isinstance(field_errors, list) or not isinstance(next_commands, list):
        return None

    return {
        "code": code,
        "message": message,
        "recovery_hint": recovery_hint,
        "field_errors": field_errors,
        "next_commands": next_commands,
    }


def raise_structured_tool_error(exc: Exception) -> NoReturn:
    """Raise ToolError with structured JSON when a FastAPI detail body exists."""
    response = _find_http_status_response(exc)
    if response is None:
        raise exc
    detail = _structured_detail(response)
    if detail is None:
        raise exc
    raise ToolError(json.dumps(detail, separators=(",", ":"), sort_keys=True)) from exc


def wrap_structured_error_tools(route: Any, component: Any) -> None:
    """Wrap generated OpenAPI tools so structured REST errors reach MCP clients."""
    if not isinstance(component, OpenAPITool):
        return

    original_run = component.run

    async def run_with_structured_errors(arguments: dict[str, Any]) -> ToolResult:
        try:
            return await original_run(arguments)
        except Exception as exc:
            raise_structured_tool_error(exc)

    object.__setattr__(component, "run", run_with_structured_errors)
