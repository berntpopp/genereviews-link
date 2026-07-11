"""GeneFoundry Response-Envelope Standard v1 — the flat banner frame.

Reshapes genereviews-link's OpenAPI-generated MCP tool responses into the
fleet-wide envelope (see ``docs/RESPONSE-ENVELOPE-STANDARD-v1.md`` on the
``genefoundry-router-standards`` repo):

- Success, collection tool: ``{"success": true, "results": [...], "_meta": {...}}``
- Success, single-item tool: ``{"success": true, "result": {...}, "_meta": {...}}``
- Failure (flat, in-band): ``{"success": false, "error_code": ..., "message": ...,
  "retryable": ..., "recovery_action": ..., "_meta": {...}}``

This module is intentionally REST-agnostic: it operates only on the plain JSON
dict a FastAPI route already returns (as extracted by
``genereview_link.mcp.error_passthrough``). The REST API surface is untouched —
this is an MCP `structuredContent` contract, not a REST response-body contract.

Mirrors the fleet's de-facto conformant exemplar (clingen-link's
``clingen_link/mcp/errors.py``): errors are RETURNED as structured content
(``success: false`` in-band) rather than raised as an opaque ``ToolError`` text
blob. The installed FastMCP 3.2.4 / mcp SDK give no supported way to combine a
wire-level ``isError: true`` with a populated ``structuredContent`` on the
success-return path (raising loses ``structuredContent`` entirely — see the
low-level ``mcp.server.lowlevel.server._make_error_result`` helper, which only
carries a text message) so, like the rest of the fleet, we rely on the in-band
``success`` flag rather than the wire ``isError`` bit.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Literal

# Bump when the tool surface or envelope shape changes in a way a warm client
# should re-fetch metadata for. No capabilities-negotiation tool exists yet on
# this server, so this is a static provenance stamp rather than a live value.
CAPABILITIES_VERSION = "1"

SOURCE = "genereviews"

# Closed error-code enum (Response-Envelope Standard v1 §2), harmonized with
# codes already used fleet-wide (e.g. clingen-link's `internal_error`, not the
# doc's shorthand `internal`).
ErrorCode = Literal[
    "invalid_input",
    "not_found",
    "ambiguous_query",
    "upstream_unavailable",
    "rate_limited",
    "internal_error",
]


@dataclass(frozen=True)
class _ToolSpec:
    """How to reshape one tool's raw REST JSON body into the envelope frame."""

    kind: Literal["single", "collection"]
    # For "collection" tools whose raw payload key differs from "results"
    # (e.g. get_passages_batch's "passages"), name the source key here. None
    # means the raw payload already uses "results" (search_passages,
    # search_passages_batch) — pass through unchanged.
    source_key: str | None = None


# One entry per MCP tool generated from genereview_link/api/routes/*.py.
# "single" tools nest their whole raw payload (minus `_meta`) under `result`.
# "collection" tools promote `source_key` (default "results") to the top level.
PRIMARY_KEY_MAP: dict[str, _ToolSpec] = {
    "search_genereviews": _ToolSpec(kind="collection", source_key="ids"),
    "get_genereview_summary": _ToolSpec(kind="single"),
    "get_abstract": _ToolSpec(kind="single"),
    "get_fulltext": _ToolSpec(kind="single"),
    "get_links": _ToolSpec(kind="single"),
    "search_passages": _ToolSpec(kind="collection", source_key="results"),
    "search_passages_batch": _ToolSpec(kind="collection", source_key="results"),
    "get_passage": _ToolSpec(kind="single"),
    "get_passages_batch": _ToolSpec(kind="collection", source_key="passages"),
    "get_chapter_section": _ToolSpec(kind="single"),
    "get_chapter_metadata": _ToolSpec(kind="single"),
    "get_table": _ToolSpec(kind="single"),
    "get_license": _ToolSpec(kind="single"),
}

_DEFAULT_SPEC = _ToolSpec(kind="single")

# genereview_link's internal StructuredHTTPException `code` -> fleet error_code.
# See genereview_link/api/orchestration_errors.py and the `code=` call sites in
# api/routes/{chapters,passages,tables}.py for the source of these values.
_ERROR_CODE_MAP: dict[str, tuple[ErrorCode, bool]] = {
    "gene_not_found": ("not_found", False),
    "pmid_resolver_failed": ("upstream_unavailable", True),
    "upstream_ncbi_unavailable": ("upstream_unavailable", True),
    "abstract_not_found": ("not_found", False),
    "invalid_pubmed_id": ("invalid_input", False),
    "invalid_nbk_id": ("invalid_input", False),
    "fulltext_scrape_failed": ("not_found", False),
    "internal_error": ("internal_error", False),
    "chapter_not_found": ("not_found", False),
    "section_empty_for_chapter": ("not_found", False),
    "conflicting_query_param": ("invalid_input", False),
    "missing_query": ("invalid_input", False),
    "gene_not_indexed": ("not_found", False),
    "passage_not_found": ("not_found", False),
    "batch_size_exceeded": ("invalid_input", False),
    "table_not_found": ("not_found", False),
    "query_must_be_string": ("invalid_input", False),
    "not_yet_indexed": ("not_found", False),
    "response_too_large": ("invalid_input", False),
}

# Fallback classification by HTTP status when no known `code` is present
# (e.g. a bare FastAPI/pydantic validation error, or an unmodeled exception).
_STATUS_CODE_FALLBACK: dict[int, tuple[ErrorCode, bool]] = {
    400: ("invalid_input", False),
    404: ("not_found", False),
    409: ("ambiguous_query", False),
    413: ("invalid_input", False),
    422: ("invalid_input", False),
    429: ("rate_limited", True),
    502: ("upstream_unavailable", True),
    503: ("upstream_unavailable", True),
    504: ("upstream_unavailable", True),
}

_GENERIC_RECOVERY_ACTION: dict[ErrorCode, str] = {
    "invalid_input": "Reformulate the request; the argument shape or value was rejected.",
    "not_found": "Confirm the identifier, or call search_passages / search_genereviews "
    "to discover valid identifiers.",
    "ambiguous_query": "Narrow the query so it resolves to a single result.",
    "upstream_unavailable": "Retry with backoff; the upstream NCBI service was unavailable.",
    "rate_limited": "Retry after backing off; the request rate exceeded a limit.",
    "internal_error": "Retry once; if the error persists, use search_passages or "
    "get_chapter_metadata for indexed corpus retrieval.",
}


def new_request_id() -> str:
    """Return a fresh opaque request id for one MCP tool invocation."""
    return uuid.uuid4().hex


def _augment_meta(
    meta: dict[str, Any],
    *,
    tool_name: str,
    request_id: str,
    elapsed_ms: float,
) -> dict[str, Any]:
    """Merge envelope-required provenance into an existing `_meta` block."""
    augmented = dict(meta)
    augmented["tool"] = tool_name
    augmented["request_id"] = request_id
    augmented["elapsed_ms"] = round(elapsed_ms, 3)
    augmented["source"] = SOURCE
    augmented["capabilities_version"] = CAPABILITIES_VERSION
    augmented["unsafe_for_clinical_use"] = True
    return augmented


def build_success_envelope(
    tool_name: str,
    raw: dict[str, Any],
    *,
    request_id: str,
    elapsed_ms: float,
) -> dict[str, Any]:
    """Reshape a raw REST JSON body into the success frame.

    ``raw`` is the tool's already-unwrapped REST response body (a plain dict —
    see ``error_passthrough.reshape_output_schema`` for how the OpenAPI-tool
    ``x-fastmcp-wrap-result`` artifact that used to double-wrap
    ``search_passages`` is neutralized before this function ever sees it).
    """
    spec = PRIMARY_KEY_MAP.get(tool_name, _DEFAULT_SPEC)
    working = dict(raw)
    meta = working.pop("_meta", None)
    if not isinstance(meta, dict):
        meta = {}

    envelope: dict[str, Any] = {"success": True}
    if spec.kind == "collection":
        source_key = spec.source_key or "results"
        items = working.pop(source_key, [])
        envelope["results"] = items
        # Remaining domain keys (e.g. missing_ids, count, recovery_hint) ride
        # beside `results` per Rule 1: "MAY add domain keys beside results/result".
        envelope.update(working)
    else:
        envelope["result"] = working

    envelope["_meta"] = _augment_meta(
        meta, tool_name=tool_name, request_id=request_id, elapsed_ms=elapsed_ms
    )
    return envelope


def build_error_envelope(
    tool_name: str,
    *,
    status_code: int,
    detail: dict[str, Any] | None,
    fallback_message: str,
    request_id: str,
    elapsed_ms: float,
) -> dict[str, Any]:
    """Build the flat, in-band error frame (Response-Envelope Standard v1 §2).

    ``detail`` is the parsed ``StructuredHTTPException`` payload
    (``{code, message, recovery_hint, field_errors, next_commands}``) when one
    was found; ``None`` falls back to a generic classification by HTTP status.
    """
    internal_code = detail.get("code") if detail else None
    error_code, retryable = _classify(internal_code, status_code)

    message = (detail or {}).get("message") or fallback_message
    recovery_action = (detail or {}).get("recovery_hint") or _GENERIC_RECOVERY_ACTION[error_code]
    next_commands = (detail or {}).get("next_commands") or []
    field_errors = (detail or {}).get("field_errors") or []

    envelope: dict[str, Any] = {
        "success": False,
        "error_code": error_code,
        "message": message,
        "retryable": retryable,
        "recovery_action": recovery_action,
    }
    if field_errors:
        envelope["field_errors"] = field_errors

    meta: dict[str, Any] = {"next_commands": next_commands}
    envelope["_meta"] = _augment_meta(
        meta, tool_name=tool_name, request_id=request_id, elapsed_ms=elapsed_ms
    )
    return envelope


def _classify(internal_code: str | None, status_code: int) -> tuple[ErrorCode, bool]:
    if internal_code and internal_code in _ERROR_CODE_MAP:
        return _ERROR_CODE_MAP[internal_code]
    if status_code in _STATUS_CODE_FALLBACK:
        return _STATUS_CODE_FALLBACK[status_code]
    if status_code >= 500:
        return "upstream_unavailable", True
    return "internal_error", False


# --- Declared untrusted_text object shape (v1.1) -----------------------------
# Inlined at every fenced position of every tool's outputSchema so the
# `kind: const "untrusted_text"` literal is REACHABLE in the declared schema
# (including inside list `items`), not only present at runtime. Inlined (no
# `$defs`) so FastMCP's compress_schema(prune_defs=True) cannot prune it.


def _ut() -> dict[str, Any]:
    """A fresh inline ``untrusted_text`` object schema (kind const literal)."""
    return {
        "type": "object",
        "properties": {
            "kind": {"const": "untrusted_text"},
            "text": {"type": "string"},
            "provenance": {
                "type": "object",
                "properties": {
                    "source": {"type": "string"},
                    "record_id": {"type": "string"},
                    "retrieved_at": {"type": "string"},
                },
                "required": ["source", "record_id", "retrieved_at"],
                "additionalProperties": True,
            },
            "raw_sha256": {"type": "string"},
        },
        "required": ["kind", "text", "provenance", "raw_sha256"],
        "additionalProperties": True,
    }


def _nullable(schema: dict[str, Any]) -> dict[str, Any]:
    return {"anyOf": [schema, {"type": "null"}]}


def _obj(props: dict[str, Any]) -> dict[str, Any]:
    return {"type": "object", "properties": props, "additionalProperties": True}


def _arr(items: dict[str, Any]) -> dict[str, Any]:
    return {"type": "array", "items": items}


def _passage_item() -> dict[str, Any]:
    # RankedPassage / PassageDetail fenced fields (text/snippet + table cells).
    return _obj(
        {
            "text": _nullable(_ut()),
            "snippet": _nullable(_ut()),
            "header": _nullable(_arr(_ut())),
            "rows": _nullable(_arr(_arr(_ut()))),
        }
    )


def _section() -> dict[str, Any]:
    # FencedGeneReviewSection: content fenced; subsections declared permissively
    # (the scraper bounds subsection depth; deeper content stays runtime-fenced).
    return _obj({"content": _ut(), "subsections": {"type": "object"}})


def _fulltext_metadata() -> dict[str, Any]:
    return _obj(
        {
            "authors": _nullable(_ut()),
            "update_info": _nullable(_ut()),
            "publication_info": _nullable(_ut()),
            "references": _arr(_ut()),
        }
    )


def _fenced_positions(tool_name: str) -> dict[str, Any]:
    """Declared fenced sub-schema(s) merged into a tool's envelope properties.

    Returns ``{"result": <schema>}`` for single-item tools or
    ``{"results": <schema>}`` for collection tools; ``{}`` for tools with no
    upstream free-text surface (get_links, get_license, search_genereviews).
    """
    if tool_name == "search_passages":
        return {"results": _arr(_passage_item())}
    if tool_name == "search_passages_batch":
        return {"results": _arr(_obj({"hits": _arr(_passage_item())}))}
    if tool_name == "get_passages_batch":
        return {"results": _arr(_passage_item())}
    if tool_name == "get_passage":
        return {
            "result": _obj(
                {
                    "passage": _passage_item(),
                    "neighbors_before": _arr(_passage_item()),
                    "neighbors_after": _arr(_passage_item()),
                }
            )
        }
    if tool_name == "get_chapter_section":
        return {"result": _obj({"content": _ut()})}
    if tool_name == "get_chapter_metadata":
        return {"result": _obj({"tables": _arr(_obj({"caption": _ut()}))})}
    if tool_name == "get_abstract":
        return {"result": _obj({"abstract": _ut()})}
    if tool_name == "get_fulltext":
        return {
            "result": _obj(
                {
                    "sections": {"type": "object", "additionalProperties": _section()},
                    "metadata": _fulltext_metadata(),
                }
            )
        }
    if tool_name == "get_genereview_summary":
        return {
            "result": _obj(
                {
                    "summary": _nullable(_section()),
                    "diagnosis": _nullable(_section()),
                    "management": _nullable(_section()),
                    "other_sections": {"type": "object", "additionalProperties": _section()},
                    "abstract_data": _nullable(_obj({"abstract": _ut()})),
                    "full_text_data": _nullable(_obj({"metadata": _fulltext_metadata()})),
                }
            )
        }
    if tool_name == "get_table":
        return {
            "result": _obj({"caption": _ut(), "header": _arr(_ut()), "rows": _arr(_arr(_ut()))})
        }
    return {}


def reshape_output_schema(
    schema: dict[str, Any] | None, tool_name: str | None = None
) -> dict[str, Any]:
    """Return the envelope-shaped ``outputSchema`` for one tool.

    Two jobs:

    1. Strip the FastMCP OpenAPI-provider ``x-fastmcp-wrap-result`` flag by
       replacing the schema entirely. That flag makes ``OpenAPITool.run()``
       wrap a non-"type: object" JSON body under ``{"result": ...}`` at the
       wire level — the root cause of genereviews' historical
       ``{"result": {"results": [...]}}`` double-wrap on ``search_passages``
       (a ``PassageSearchResponse | IdsOnlySearchResponse`` union). Overwriting
       the registered component's ``output_schema`` neutralizes it.
    2. Declare a schema that matches what we emit AND makes the fenced
       ``untrusted_text`` object (``kind`` const) REACHABLE at every fenced
       position — including inside list ``items`` (``results[*].text``,
       ``header[*]``, ``rows[*][*]``) — so the literal is present in the
       served ``outputSchema``, not only in runtime data. The object stays
       permissive (``additionalProperties: true``, only ``success``/``_meta``
       required) so it validates BOTH the success frame and the flat error
       frame that share one ``outputSchema`` slot; the low-level MCP SDK
       validates ``structuredContent`` against it on every call.

    The ``untrusted_text`` sub-schema is inlined (no ``$defs``) so FastMCP's
    downstream ``compress_schema(prune_defs=True)`` cannot prune it.
    """
    envelope_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "success": {"type": "boolean"},
            "_meta": {"type": "object"},
        },
        "required": ["success", "_meta"],
        "additionalProperties": True,
    }
    if tool_name is not None:
        envelope_schema["properties"].update(_fenced_positions(tool_name))
    return envelope_schema
