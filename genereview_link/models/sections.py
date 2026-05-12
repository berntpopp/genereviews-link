"""Canonical section names for GeneReviews passages.

The enum is exposed via Pydantic `Literal` so it appears as a
JSONSchema `enum` in the OpenAPI doc and in every MCP tool description.
This is the single source of truth for valid section values across the
API surface and the rerank module.
"""

from __future__ import annotations

from typing import Literal, get_args

SectionName = Literal[
    "summary",
    "diagnosis",
    "clinical_features",
    "management",
    "genetic_counseling",
    "molecular_genetics",
    "resources",
    "other",
    "references",
]

SECTION_NAMES: tuple[str, ...] = get_args(SectionName)

SYSTEMATICALLY_UNSCRAPED_SECTIONS: frozenset[str] = frozenset({"summary"})
"""Canonical section names that the current NXML scraper deliberately does NOT extract.

When get_chapter_metadata sees `passage_count == 0` for one of these, it emits a
SectionSummary.note explaining the absence. Keep this set small and explicit—if it
grows past ~3 entries, reconsider whether the scraper itself should change instead.
"""
