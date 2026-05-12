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


def test_rerank_populates_dense_rank_and_rrf_score() -> None:
    """After RRF reranking, evidence rows carry non-None dense_rank and rrf_score."""
    rows = [_row("p1", "summary", 1.0), _row("p2", "summary", 0.5)]
    dense = {"p1": 0.3, "p2": 0.9}  # dense flips order: p2 ranks higher densely
    out, diag = rerank_with_embeddings(rows, dense_scores=dense, rrf_k=60)
    assert diag.active is True
    # Top result must have both fields populated
    assert out[0].rrf_score is not None
    assert out[0].dense_rank is not None
    # All non-guarded (evidence) rows should have fields set
    for row in out:
        assert row.rrf_score is not None
        assert row.dense_rank is not None


def test_no_dense_scores_fallback_populates_lexical_rank_positions() -> None:
    rows = [
        _row("third", "summary", 0.1),
        _row("first", "summary", 0.9),
        _row("second", "summary", 0.5),
    ]

    out, diag = rerank_with_embeddings(rows, dense_scores={}, rrf_k=60)

    assert diag.fallback_reason == "no_dense_scores"
    assert [(r.passage.passage_id, r.lexical_rank_position) for r in out] == [
        ("first", 1),
        ("second", 2),
        ("third", 3),
    ]


def test_rrf_path_populates_lexical_rank_positions_from_lexical_sort() -> None:
    rows = [
        _row("lexical_first", "summary", 1.0),
        _row("lexical_second", "summary", 0.8),
        _row("lexical_third", "summary", 0.6),
    ]
    dense = {"lexical_first": 0.1, "lexical_second": 0.3, "lexical_third": 0.9}

    out, diag = rerank_with_embeddings(rows, dense_scores=dense, rrf_k=60)

    assert diag.active is True
    positions = {r.passage.passage_id: r.lexical_rank_position for r in out}
    assert positions == {
        "lexical_first": 1,
        "lexical_second": 2,
        "lexical_third": 3,
    }


def test_rerank_populates_lexical_rank_positions_on_guarded_rows() -> None:
    rows = [
        _row("evidence", "summary", 0.8),
        _row("guarded", "references", 1.0),
    ]
    dense = {"evidence": 0.5}

    out, diag = rerank_with_embeddings(rows, dense_scores=dense, rrf_k=60)

    assert diag.active is True
    assert [(r.passage.passage_id, r.lexical_rank_position) for r in out] == [
        ("evidence", 2),
        ("guarded", 1),
    ]


def test_no_evidence_fallback_populates_lexical_rank_positions_on_guarded_rows() -> None:
    rows = [
        _row("guarded_second", "references", 0.5),
        _row("guarded_first", "references", 0.9),
    ]

    out, diag = rerank_with_embeddings(
        rows,
        dense_scores={"guarded_first": 0.9, "guarded_second": 0.1},
        rrf_k=60,
    )

    assert diag.fallback_reason == "no_evidence_candidates"
    assert [(r.passage.passage_id, r.lexical_rank_position) for r in out] == [
        ("guarded_first", 1),
        ("guarded_second", 2),
    ]
