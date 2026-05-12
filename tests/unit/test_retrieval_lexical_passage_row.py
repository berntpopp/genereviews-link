"""Unit tests for LexicalPassageRow dataclass fields."""

from genereview_link.retrieval.repository import LexicalPassageRow, PassageRow


def _make_passage() -> PassageRow:
    return PassageRow(
        nbk_id="NBK1",
        passage_id="NBK1:0001",
        chapter_section="summary",
        heading_path=None,
        section_level=0,
        chunk_index=1,
        text="t",
    )


def test_lexical_passage_row_carries_rrf_fields() -> None:
    row = LexicalPassageRow(
        passage=_make_passage(),
        phrase_rank=0.0,
        strict_rank=0.0,
        recall_rank=0.0,
        recall_overlap_count=0,
        lexical_rank=0.5,
        dense_rank=3,
        rrf_score=0.024,
    )
    assert row.dense_rank == 3
    assert row.rrf_score == 0.024


def test_lexical_passage_row_rrf_fields_default_to_none() -> None:
    """Existing call sites that omit dense_rank/rrf_score must still work."""
    row = LexicalPassageRow(
        passage=_make_passage(),
        phrase_rank=1.0,
        strict_rank=1.0,
        recall_rank=1.0,
        recall_overlap_count=2,
        lexical_rank=0.8,
    )
    assert row.dense_rank is None
    assert row.rrf_score is None
