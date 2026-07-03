"""Shared MCP tool annotations for genereview-link.

Mirrors the fleet's de-facto conformant exemplar (clingen-link's
``clingen_link/mcp/annotations.py``). Every genereview-link tool is a
read-only NCBI GeneReviews/Bookshelf lookup against an externally-evolving
corpus (open world) — none mutate state — so ``READ_ONLY_OPEN_WORLD`` applies
uniformly across the tool surface.
"""

from __future__ import annotations

from mcp.types import ToolAnnotations

READ_ONLY_OPEN_WORLD = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=True,
)
