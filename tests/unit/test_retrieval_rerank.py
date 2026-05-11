"""Tests for RRF + section_priority rerank."""

from __future__ import annotations

from genereview_link.retrieval.repository import LexicalPassageRow, PassageRow
from genereview_link.retrieval.rerank import (
    SECTION_PRIORITY,
    rerank_with_embeddings,
)


def _row(passage_id: str, section: str, lexical_rank: float = 1.0) -> LexicalPassageRow:
    return LexicalPassageRow(
        passage=PassageRow(
            nbk_id="NBK1",
            passage_id=passage_id,
            chapter_section=section,
            heading_path=section.title(),
            section_level=1,
            chunk_index=0,
            text=f"text for {passage_id}",
        ),
        phrase_rank=lexical_rank,
        strict_rank=0.0,
        recall_rank=0.0,
        recall_overlap_count=1,
        lexical_rank=lexical_rank,
    )


def test_section_priority_orders_ties() -> None:
    rows = [_row("a", "references", 1.0), _row("b", "summary", 1.0)]
    out, _diag = rerank_with_embeddings(rows, dense_scores={}, rrf_k=60)
    # references is guarded — appended last
    assert out[0].passage.passage_id == "b"
    assert out[-1].passage.passage_id == "a"


def test_rrf_combines_lexical_and_dense() -> None:
    rows = [_row("a", "summary", 1.0), _row("b", "summary", 0.5)]
    # dense flips the order
    dense = {"a": 0.1, "b": 0.9}
    out, diag = rerank_with_embeddings(rows, dense_scores=dense, rrf_k=60)
    assert out[0].passage.passage_id == "b"
    assert diag.strategy == "lexical_top_k_dense_rrf"


def test_section_priority_constants() -> None:
    assert SECTION_PRIORITY["summary"] == 0
    assert SECTION_PRIORITY["references"] == 50
