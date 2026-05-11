"""Dataclasses representing parsed corpus rows before DB insert."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import date
from functools import cached_property
from typing import Literal


@dataclass(frozen=True, slots=True)
class ChapterRecord:
    """One GeneReviews chapter, ready for genereview_chapters insertion."""

    nbk_id: str
    short_name: str
    title: str
    pubmed_id: str | None
    gene_symbols: tuple[str, ...]
    omim_ids: tuple[str, ...]
    authors: str | None
    initial_pub_date: date | None
    last_updated_date: date | None
    nxml_relpath: str
    raw_metadata: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class PassageRecord:
    """One chunked passage, ready for genereview_passages insertion."""

    nbk_id: str
    passage_id: str
    chapter_section: str
    heading_path: str | None
    section_level: int
    chunk_index: int
    text: str
    char_count: int
    token_estimate: int
    passage_type: Literal["narrative", "table"] = "narrative"
    table_id: str | None = None
    table_data: dict[str, object] | None = None  # {"caption": str, "header": list[str], "rows": list[list[str]]}

    @cached_property
    def text_hash(self) -> str:
        return hashlib.sha256(self.text.encode("utf-8")).hexdigest()
