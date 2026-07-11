"""FastMCP-core not-found reflection guard (Response-Envelope v1.1 fast-follow).

FastMCP core (pinned ``>=3.4.4,<4.0.0``) reflects the caller's OWN requested tool
name / resource URI / prompt name back to the caller (and to logs) BEFORE this
repo's OpenAPI response-envelope wrapping runs. This module closes that residual
with fixed, input-free messages built from CONSTANTS only, mirroring the ratified
fleet references (``mondo``/``hpo`` registry preflight, ``clinvar`` protocol
backstop, ``panelapp``/``hpo`` validation-log scrub filter).

The reflected text is *caller-supplied* (a caller self-reflection surface), so
this is materially lower-risk than the upstream-injection leak the prior sweep
closed. It is still worth closing: the reflected name/URI — with any
control/zero-width/bidi/NUL code points — lands in shared operator logs and in an
agent's tool-result context. Fixed constants remove the channel entirely.

Layers (spec §3), copied per repo (no shared runtime library exists fleet-wide);
the observed leaks on this repo's pristine ``main`` (fastmcp 3.4.4) were:

* Layer 1 — ``on_call_tool`` registry preflight: an unknown tool makes core
  *return* an ``isError`` ``CallToolResult`` whose TextContent mirror echoes
  ``Unknown tool: '<name>'`` (``structuredContent`` is ``None``). ``get_tool``
  returns ``None`` for an unknown name, so we return a fixed, name-free
  ``not_found`` envelope (``is_error=True``, ratified contract) BEFORE core
  dispatch. Never echoes ``_meta.tool``.
* Layer 2 — ``on_read_resource`` boundary: a valid-but-unknown URI makes core
  raise ``NotFoundError("Unknown resource: '<uri>'")`` (leaked ``-32002`` to the
  caller); we re-raise a fixed URI-free ``ResourceError``. This repo registers no
  author ``ResourceError`` messages, so ALL resource read failures are severed.
* Layer 3 — protocol-handler backstop: wraps the raw ``CallTool`` / ``ReadResource``
  / ``GetPrompt`` request handlers as the OUTERMOST layer. Replaces any non-envelope
  ``isError`` tool result (the unknown-tool *return* path) and re-raises fixed
  input-free messages for resource/prompt dispatch failures — the ONLY layer that
  covers the unknown-PROMPT surface (core echoed ``Unknown prompt: '<name>'`` to
  the caller).
* Layer 5 — validation-log scrub filter: FastMCP's ``Handler called: …`` DEBUG
  records, the ``Tool cache miss for <name>`` record, and the MCP SDK session's
  ``Failed to validate request`` record (malformed/forbidden URI at ``-32602``)
  echo the raw name/URI (with code points) on their own loggers/handlers. The
  filter neutralizes those records at the source logger so caller input never
  reaches a log sink at ANY level.

Layer 4 (arg-validation) is unchanged: the OpenAPI wrapper
(``error_passthrough.run_with_structured_errors``) already reshapes every tool
failure into the flat error envelope. Layer 6 (OTel span redaction) is a no-op
here: FastMCP pulls in ``opentelemetry-api`` transitively, but
``opentelemetry-sdk`` is absent, so the tracer provider is non-recording — no span
exception attributes are ever captured, so there is nothing to redact (fleet
policy: do NOT add the SDK dependency).
"""

from __future__ import annotations

import json
import logging
from typing import Any, cast

import mcp.types
from fastmcp import FastMCP
from fastmcp.exceptions import ResourceError
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.tools.base import ToolResult
from mcp.types import TextContent

from genereview_link.mcp.envelope import CAPABILITIES_VERSION, SOURCE, new_request_id

logger = logging.getLogger(__name__)

# Fixed, input-free public messages. They NEVER contain the requested name/URI
# (nor a ``_meta.tool`` echo of it): sanitation strips code points but not
# injection prose, so a fixed constant is the only safe source (prior-sweep
# lesson). ``not_found`` reuses this repo's error-code vocabulary (spec §3.1).
_UNKNOWN_TOOL_MESSAGE = "The requested tool is not available."
_UNKNOWN_TOOL_RECOVERY = (
    "List the server's tools; the requested tool does not exist. Use search_passages "
    "or search_genereviews for corpus retrieval."
)
_UNKNOWN_RESOURCE_MESSAGE = "The requested resource is not available."
_UNKNOWN_PROMPT_MESSAGE = "The requested prompt is not available."


def _unknown_tool_envelope() -> dict[str, Any]:
    """A fixed, name-free ``not_found`` flat envelope for an unknown tool.

    The requested (caller-controlled) name is NEVER echoed: ``_meta`` carries no
    ``tool`` key, and every string is a server-authored constant.
    """
    return {
        "success": False,
        "error_code": "not_found",
        "message": _UNKNOWN_TOOL_MESSAGE,
        "retryable": False,
        "recovery_action": _UNKNOWN_TOOL_RECOVERY,
        "_meta": {
            "request_id": new_request_id(),
            "source": SOURCE,
            "capabilities_version": CAPABILITIES_VERSION,
            "unsafe_for_clinical_use": True,
        },
    }


def unknown_tool_result() -> ToolResult:
    """Return a fixed, name-free unknown-tool result with the wire ``isError`` bit set.

    ``is_error=True`` is the ratified fleet contract (autopvs1/clinvar/gnomad): an
    unknown tool has no registered output schema, so an ``is_error=False`` frame
    would make the FastMCP Client validate ``structured_content`` against a
    nonexistent schema, fail, and log the hostile requested name via its ``client``
    logger. Carries BOTH ``structured_content`` and a matching TextContent mirror.
    """
    envelope = _unknown_tool_envelope()
    return ToolResult(
        content=[TextContent(type="text", text=json.dumps(envelope))],
        structured_content=envelope,
        is_error=True,
    )


class NotFoundGuard(Middleware):
    """Layer 1 (tool preflight) + Layer 2 (resource boundary)."""

    async def on_call_tool(
        self,
        context: MiddlewareContext[Any],
        call_next: CallNext[Any, ToolResult],
    ) -> ToolResult:
        """Preflight the tool NAME; an unknown name never reaches core dispatch.

        ``get_tool`` returns ``None`` (it does not raise) for an unknown or
        disabled tool, so an unknown name is caught here and answered with a
        fixed, name-free envelope. On an unexpected resolution failure we DEFER to
        the chain (the Layer-3 backstop still masks the core return path).
        """
        fctx = getattr(context, "fastmcp_context", None)
        name = getattr(getattr(context, "message", None), "name", None)
        if fctx is not None and isinstance(name, str):
            try:
                tool = await fctx.fastmcp.get_tool(name)
            except Exception:
                tool = object()  # resolution failure: defer to core, do not mask
            if tool is None:
                logger.warning("mcp_unknown_tool")
                return unknown_tool_result()
        return await call_next(context)

    async def on_read_resource(
        self,
        context: MiddlewareContext[Any],
        call_next: CallNext[Any, Any],
    ) -> Any:
        """Emit a FIXED, URI-free error for a resource not-found / read failure.

        The requested URI is caller-controlled; FastMCP core echoes it
        (``Unknown resource: '<uri>'`` / ``Error reading resource '<uri>'``) in
        both the direct exception and the protocol error. Re-raise a fixed message
        so the URI never reaches the caller/protocol. This repo registers no author
        ``ResourceError`` messages, so ALL resource read failures are severed; the
        exception CLASS only is logged (never the caller-controlled value).
        """
        try:
            return await call_next(context)
        except Exception as exc:
            logger.warning("mcp_resource_error type=%s", type(exc).__name__)
            raise ResourceError(_UNKNOWN_RESOURCE_MESSAGE) from None


# ---------------------------------------------------------------------------
# Layer 3 — protocol-handler backstop (clinvar pattern)
# ---------------------------------------------------------------------------


class ProtocolError(Exception):
    """A dispatch-level failure re-raised with a FIXED, input-free message."""


def _is_structured_envelope(call_result: mcp.types.CallToolResult) -> bool:
    """True if an ``isError`` result carries one of OUR JSON envelopes.

    Distinguishes a structured genereviews error (already input-free — it has an
    ``error_code``) from a RAW FastMCP dispatch error whose plain-text message
    echoes the caller-supplied tool name (``Unknown tool: '<name>'``).
    """
    if not call_result.content:
        return False
    text = getattr(call_result.content[0], "text", None)
    if not isinstance(text, str):
        return False
    try:
        obj = json.loads(text)
    except (ValueError, TypeError):
        return False
    return isinstance(obj, dict) and "error_code" in obj


def _fixed_tool_not_found_result() -> mcp.types.ServerResult:
    """A fixed, name-free ServerResult for an unknown/failed tool dispatch."""
    envelope = _unknown_tool_envelope()
    return mcp.types.ServerResult(
        mcp.types.CallToolResult(
            content=[TextContent(type="text", text=json.dumps(envelope))],
            structuredContent=envelope,
            isError=True,
        )
    )


def install_notfound_guard(mcp_server: FastMCP) -> None:
    """Wire all not-found guard layers onto a fully-built FastMCP instance.

    Call AFTER every tool/resource/prompt is registered: attaches the Layer-5
    validation-log scrub filter (now that FastMCP's non-propagating Rich handlers
    exist), adds the Layer-1/2 :class:`NotFoundGuard` middleware, and installs the
    Layer-3 protocol backstop as the OUTERMOST wrapper on the raw handlers.
    """
    install_validation_log_filter()
    mcp_server.add_middleware(NotFoundGuard())
    install_protocol_error_handler(mcp_server)


def install_protocol_error_handler(mcp_server: FastMCP) -> None:
    """Wrap the tool/resource/prompt request handlers as the OUTERMOST layer.

    A FastMCP core not-found (or read) error can no longer reflect the
    caller-supplied name/URI (nor its code points). Install AFTER all
    tools/resources/prompts are registered so the handlers exist.
    """
    handlers = mcp_server._mcp_server.request_handlers

    call_tool = handlers.get(mcp.types.CallToolRequest)
    if call_tool is not None:

        async def wrapped_call_tool(
            request: mcp.types.CallToolRequest,
            *,
            _orig: Any = call_tool,
        ) -> mcp.types.ServerResult:
            try:
                result = cast(mcp.types.ServerResult, await _orig(request))
            except Exception:
                # A registered tool never raises here (the OpenAPI wrapper returns
                # an envelope); any exception is a dispatch-level failure whose
                # message would echo the caller name — mask it.
                logger.warning("mcp_protocol_error kind=tool")
                return _fixed_tool_not_found_result()
            # FastMCP *returns* an isError CallToolResult with a raw plain-text
            # message ("Unknown tool: '<name>'") for an unknown tool; replace any
            # isError result that is NOT one of our structured envelopes. Known-tool
            # errors are returned in-band as isError=False, so they never match.
            root = getattr(result, "root", None)
            if (
                isinstance(root, mcp.types.CallToolResult)
                and root.isError
                and not _is_structured_envelope(root)
            ):
                logger.warning("mcp_protocol_error kind=tool")
                return _fixed_tool_not_found_result()
            return result

        handlers[mcp.types.CallToolRequest] = wrapped_call_tool

    for request_type, message, kind in (
        (mcp.types.ReadResourceRequest, _UNKNOWN_RESOURCE_MESSAGE, "resource"),
        (mcp.types.GetPromptRequest, _UNKNOWN_PROMPT_MESSAGE, "prompt"),
    ):
        orig = handlers.get(request_type)
        if orig is None:
            continue

        async def wrapped(
            request: Any,
            *,
            _orig: Any = orig,
            _message: str = message,
            _kind: str = kind,
        ) -> Any:
            try:
                return await _orig(request)
            except Exception as exc:
                # Re-raise with a FIXED, input-free message so no requested
                # name/URI (or its code points) reaches the JSON-RPC error frame.
                # Log the exception CLASS only (never the caller-controlled value).
                logger.warning("mcp_protocol_error kind=%s type=%s", _kind, type(exc).__name__)
                raise ProtocolError(_message) from None

        handlers[request_type] = wrapped


# ---------------------------------------------------------------------------
# Layer 5 — validation-log scrub filter (panelapp/hpo pattern)
# ---------------------------------------------------------------------------
#
# Each entry is a substring that appears in the ``record.msg`` (f-string prefix or
# %-template) of a FastMCP-core / MCP-SDK record that reflects the caller-supplied
# name/URI — carried in ``record.args`` or interpolated into ``record.msg``.
# Matching on ``msg`` covers both forms because the scrub replaces the message AND
# clears the args.
_REFLECTION_MARKERS: tuple[str, ...] = (
    "Handler called: call_tool",
    "Handler called: read_resource",
    "Handler called: get_prompt",
    "Tool cache miss for",
    "Invalid arguments for tool",
    "Error calling tool",
    "Error reading resource",
    "Failed to validate request",
    "Failed to validate notification",
    "Message that failed validation",
)
_SCRUBBED_MESSAGE = "MCP request detail omitted (caller input redacted)."

#: Framework logger-name prefixes for the WARNING+ args-clearing fallback.
_SCRUBBED_LOGGERS = ("fastmcp", "mcp")

#: The SOURCE loggers on which those records are CREATED. Attach the filter
#: directly to each (root covers ``mcp.shared.session``'s bare ``logging.warning``);
#: ``fastmcp`` is FastMCP's non-propagating parent (its own Rich handlers), so a
#: root-only filter would silently miss records emitted in the fastmcp subtree.
_SOURCE_LOGGERS = (
    "",  # root — mcp.shared.session request-validation failures
    "fastmcp",
    "fastmcp.server.server",
    "fastmcp.server.mixins.mcp_operations",
    "mcp",
    "mcp.server.lowlevel.server",
)


class ValidationLogScrubFilter(logging.Filter):
    """Scrub caller-supplied name/URI from FastMCP/MCP framework log records.

    Replaces the record payload with fixed metadata (clearing ``args`` /
    ``exc_info`` / ``exc_text`` / ``stack_info``) so the caller-chosen name/URI —
    and any control/zero-width/bidi/NUL code points it carries — can never reach a
    log or telemetry sink at ANY level. Always returns ``True``: the (now
    input-free) record is still emitted for operational visibility.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.msg if isinstance(record.msg, str) else ""
        # Records that reflect the caller-supplied name/URI (any logger, any level):
        # replace the whole message and clear the interpolated args/traceback.
        if any(marker in msg for marker in _REFLECTION_MARKERS):
            record.msg = _SCRUBBED_MESSAGE
            record.args = ()
            record.exc_info = None
            record.exc_text = None
            record.stack_info = None
            return True
        # Fallback: other FastMCP/MCP framework WARNING+ records may carry
        # caller-derived detail in their interpolated args — drop it.
        if record.levelno < logging.WARNING:
            return True
        if not record.name.startswith(_SCRUBBED_LOGGERS):
            return True
        record.args = ()
        record.exc_info = None
        record.exc_text = None
        return True


#: One shared filter instance so idempotent installs don't stack duplicates.
_SHARED_FILTER = ValidationLogScrubFilter()


def _has_filter(target: logging.Logger | logging.Handler) -> bool:
    return any(isinstance(existing, ValidationLogScrubFilter) for existing in target.filters)


def install_validation_log_filter() -> None:
    """Attach the scrub filter to every SOURCE logger (and its handlers), idempotently.

    A logging filter runs only for records emitted on the logger it is attached to
    (ancestor logger-level filters are skipped during propagation), so the filter is
    attached directly to each originating logger — including the ROOT logger (where
    ``mcp.shared.session`` emits request-validation failures via a bare
    ``logging.warning``) and FastMCP's own non-propagating ``fastmcp`` logger. Also
    attach to each logger's existing handlers (handler-level filters DO run during
    propagation) as belt-and-braces. Call AFTER the FastMCP facade is built so the
    framework handlers already exist.
    """
    for name in _SOURCE_LOGGERS:
        target = logging.getLogger(name)
        if not _has_filter(target):
            target.addFilter(_SHARED_FILTER)
        for handler in target.handlers:
            if not _has_filter(handler):
                handler.addFilter(_SHARED_FILTER)
