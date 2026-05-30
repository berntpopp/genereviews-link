"""Tests for structured FastAPI error passthrough to MCP tool errors."""

from __future__ import annotations

import json

import httpx
import pytest
from fastmcp.exceptions import ToolError

from genereview_link.mcp.error_passthrough import raise_structured_tool_error


def _wrapped_http_status_error(response: httpx.Response) -> ValueError:
    status_error = httpx.HTTPStatusError(
        "not found",
        request=response.request,
        response=response,
    )
    try:
        raise ValueError("HTTP error 404: Not Found") from status_error
    except ValueError as exc:
        return exc


def test_raise_structured_tool_error_extracts_structured_detail() -> None:
    request = httpx.Request("GET", "http://fastapi/chapters/NBK999/metadata")
    response = httpx.Response(
        404,
        request=request,
        json={
            "detail": {
                "code": "chapter_not_found",
                "message": "chapter 'NBK999' not in corpus",
                "recovery_hint": "check the NBK ID",
                "field_errors": [],
                "next_commands": [
                    {"tool": "search_passages", "arguments": {"q": "<gene symbol or term>"}}
                ],
            }
        },
    )

    with pytest.raises(ToolError) as exc_info:
        raise_structured_tool_error(_wrapped_http_status_error(response))

    payload = json.loads(str(exc_info.value))
    assert payload["code"] == "chapter_not_found"
    assert payload["message"] == "chapter 'NBK999' not in corpus"
    assert payload["recovery_hint"] == "check the NBK ID"
    assert payload["next_commands"][0]["tool"] == "search_passages"


def test_raise_structured_tool_error_reraises_unstructured_error() -> None:
    request = httpx.Request("GET", "http://fastapi/plain")
    response = httpx.Response(500, request=request, text="plain failure")
    original = _wrapped_http_status_error(response)

    with pytest.raises(ValueError) as exc_info:
        raise_structured_tool_error(original)

    assert exc_info.value is original
