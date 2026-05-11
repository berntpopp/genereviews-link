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
