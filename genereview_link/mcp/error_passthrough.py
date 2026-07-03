"""Structured error passthrough for FastMCP OpenAPI-generated tools.

Wraps every OpenAPI-generated tool so its ``run()`` always returns a
Response-Envelope Standard v1 frame (see ``genereview_link.mcp.envelope``) as
``structuredContent`` — the flat success banner on the happy path, the flat
in-band error frame on failure. Also attaches canonical domain tags
(Tool-Naming Standard v1, rule 6) and neutralizes the FastMCP OpenAPI
provider's non-object-schema ``{"result": ...}`` wrap so declared
``outputSchema`` and the runtime payload agree.
"""

from __future__ import annotations

import time
from typing import Any

import httpx
from fastmcp.server.providers.openapi import OpenAPITool
from fastmcp.tools.base import ToolResult

from genereview_link.mcp import envelope
from genereview_link.mcp.annotations import READ_ONLY_OPEN_WORLD

# Canonical domain tags per tool (GeneFoundry Tool-Naming Standard v1, rule 6) so
# the gateway can filter/curate the surfaced toolset. ``gene``/``literature`` for
# gene-keyed lookups, ``literature`` for corpus passage/chapter retrieval, ``meta``
# for static reference material.
DOMAIN_TAGS: dict[str, frozenset[str]] = {
    "search_genereviews": frozenset({"gene", "literature"}),
    "get_genereview_summary": frozenset({"gene", "literature"}),
    "get_abstract": frozenset({"gene", "literature"}),
    "get_fulltext": frozenset({"gene", "literature"}),
    "get_links": frozenset({"gene", "literature"}),
    "search_passages": frozenset({"literature"}),
    "search_passages_batch": frozenset({"literature"}),
    "get_passage": frozenset({"literature"}),
    "get_passages_batch": frozenset({"literature"}),
    "get_chapter_section": frozenset({"literature"}),
    "get_chapter_metadata": frozenset({"literature"}),
    "get_table": frozenset({"literature"}),
    "get_license": frozenset({"meta"}),
}


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


def _fallback_message(response: httpx.Response) -> str:
    """Best-effort human-readable message when no structured detail body exists."""
    try:
        body = response.json()
    except ValueError:
        text = response.text.strip()
        return text[:300] if text else f"HTTP {response.status_code}"
    if isinstance(body, dict):
        for key in ("hint", "message", "error", "detail"):
            value = body.get(key)
            if isinstance(value, str) and value:
                return value[:300]
    return f"HTTP {response.status_code}"


def _build_error_envelope_from_exception(
    tool_name: str, exc: Exception, elapsed_ms: float
) -> dict[str, Any]:
    """Convert any exception raised during a tool's REST call into the flat error frame."""
    response = _find_http_status_response(exc)
    request_id = envelope.new_request_id()
    if response is None:
        # No HTTP response to inspect (e.g. a connection error, or a ValueError
        # raised while building the request). Treat as internal/unavailable.
        return envelope.build_error_envelope(
            tool_name,
            status_code=500,
            detail=None,
            fallback_message=f"{type(exc).__name__}: {exc}"[:300],
            request_id=request_id,
            elapsed_ms=elapsed_ms,
        )
    detail = _structured_detail(response)
    return envelope.build_error_envelope(
        tool_name,
        status_code=response.status_code,
        detail=detail,
        fallback_message=_fallback_message(response),
        request_id=request_id,
        elapsed_ms=elapsed_ms,
    )


def wrap_structured_error_tools(route: Any, component: Any) -> None:
    """Wrap generated OpenAPI tools so every result is a Response-Envelope Standard
    v1 frame, and attach canonical domain tags (Tool-Naming Standard v1, rule 6)."""
    if not isinstance(component, OpenAPITool):
        return

    domain_tags = DOMAIN_TAGS.get(component.name)
    if domain_tags:
        object.__setattr__(component, "tags", set(component.tags) | domain_tags)

    # Every genereview-link tool is a read-only NCBI GeneReviews/Bookshelf
    # lookup against an externally-evolving corpus — none mutate state.
    object.__setattr__(component, "annotations", READ_ONLY_OPEN_WORLD)

    # Neutralize FastMCP's non-object-schema `{"result": ...}` wrap (the root
    # cause of the historical `{"result": {"results": [...]}}` double-wrap on
    # search_passages) and declare an envelope-shaped outputSchema. See
    # envelope.reshape_output_schema for why this must happen before any call.
    object.__setattr__(
        component, "output_schema", envelope.reshape_output_schema(component.output_schema)
    )

    original_run = component.run
    tool_name = component.name

    async def run_with_structured_errors(arguments: dict[str, Any]) -> ToolResult:
        start = time.perf_counter()
        try:
            result = await original_run(arguments)
        except Exception as exc:
            elapsed_ms = (time.perf_counter() - start) * 1000
            error_envelope = _build_error_envelope_from_exception(tool_name, exc, elapsed_ms)
            return ToolResult(structured_content=error_envelope)

        elapsed_ms = (time.perf_counter() - start) * 1000
        raw = result.structured_content if isinstance(result.structured_content, dict) else {}
        success_envelope = envelope.build_success_envelope(
            tool_name,
            raw,
            request_id=envelope.new_request_id(),
            elapsed_ms=elapsed_ms,
        )
        return ToolResult(structured_content=success_envelope)

    object.__setattr__(component, "run", run_with_structured_errors)
