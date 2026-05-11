"""PassageDetail + extended RankedPassage Pydantic models."""

from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from genereview_link.models.genereview_models import (
    PassageDetail,
    RankedPassage,
    ScoreBreakdown,
)


def _score_breakdown() -> ScoreBreakdown:
    return ScoreBreakdown(
        lexical_rank=1.0,
        phrase_rank=0.5,
        strict_rank=0.4,
        recall_rank=0.3,
        section_priority=1,
        final_position=1,
    )


def test_passage_detail_minimal_fields():
    pd = PassageDetail(
        passage_id="NBK1:0001",
        nbk_id="NBK1",
        chapter_title="Test Chapter",
        chapter_last_updated=date(2025, 12, 1),
        chapter_section="management",
        heading_path="Management > X",
        section_level=2,
        chunk_index=1,
        text="hello world",
        char_count=11,
        gene_symbols=["TG"],
    )
    assert pd.passage_id == "NBK1:0001"
    assert pd.chapter_title == "Test Chapter"


def test_passage_detail_rejects_bad_chapter_section():
    with pytest.raises(ValidationError):
        PassageDetail(
            passage_id="NBK1:0001",
            nbk_id="NBK1",
            chapter_title="Test",
            chapter_last_updated=None,
            chapter_section="bogus",  # not in SectionName
            heading_path=None,
            section_level=1,
            chunk_index=0,
            text="",
            char_count=0,
            gene_symbols=[],
        )


def test_ranked_passage_allows_text_or_snippet():
    rp = RankedPassage(
        passage_id="NBK1:0001",
        nbk_id="NBK1",
        gene_symbols=["TG"],
        chapter_title="Test",
        chapter_last_updated=date(2025, 12, 1),
        chapter_section="management",
        heading_path="Management > X",
        text=None,
        snippet="**BRCA1**: example",
        char_count=20,
        score_breakdown=_score_breakdown(),
    )
    assert rp.snippet == "**BRCA1**: example"
    assert rp.text is None
