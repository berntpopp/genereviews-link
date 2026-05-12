"""Tests for role- and intent-adjusted retrieval reranking."""

from __future__ import annotations

import pytest

from genereview_link.retrieval.repository import LexicalPassageRow, PassageRow
from genereview_link.retrieval.rerank import (
    ROLE_MULTIPLIER,
    adjusted_score_for,
    rerank_with_embeddings,
)


def _row(
    passage_id: str,
    section: str,
    *,
    lexical_rank: float,
    role: str = "evidence",
    nbk_id: str = "NBK1",
) -> LexicalPassageRow:
    return LexicalPassageRow(
        passage=PassageRow(
            nbk_id=nbk_id,
            passage_id=passage_id,
            chapter_section=section,
            heading_path=section.title(),
            section_level=1,
            chunk_index=0,
            text=f"text for {passage_id}",
            passage_role=role,
        ),
        phrase_rank=lexical_rank,
        strict_rank=0.0,
        recall_rank=0.0,
        recall_overlap_count=1,
        lexical_rank=lexical_rank,
    )


def test_identical_rrf_evidence_beats_cross_reference_in_same_section() -> None:
    rows = [
        _row("evidence", "management", lexical_rank=1.0, role="evidence"),
        _row("cross_ref", "management", lexical_rank=0.9, role="cross_reference"),
    ]
    dense_scores = {"cross_ref": 0.9, "evidence": 0.1}

    out, diag = rerank_with_embeddings(rows, dense_scores=dense_scores, rrf_k=60)

    assert diag.active is True
    scores = {r.passage.passage_id: r for r in out}
    assert scores["cross_ref"].rrf_score == pytest.approx(scores["evidence"].rrf_score)
    assert [r.passage.passage_id for r in out] == ["evidence", "cross_ref"]
    assert scores["cross_ref"].role_multiplier == ROLE_MULTIPLIER["cross_reference"]


def test_management_intent_boost_beats_dense_tiebreak_for_identical_rrf() -> None:
    rows = [
        _row("management", "management", lexical_rank=1.0),
        _row("genetic", "genetic_counseling", lexical_rank=0.9),
    ]
    dense_scores = {"genetic": 0.9, "management": 0.1}

    out, _diag = rerank_with_embeddings(
        rows,
        dense_scores=dense_scores,
        rrf_k=60,
        query_intents=["management"],
    )

    scores = {r.passage.passage_id: r for r in out}
    assert scores["management"].rrf_score == pytest.approx(scores["genetic"].rrf_score)
    assert scores["management"].intent_section_boost == pytest.approx(0.30)
    assert [r.passage.passage_id for r in out] == ["management", "genetic"]


def test_cross_reference_with_higher_raw_rrf_loses_after_multiplier() -> None:
    rows = [
        _row("cross_ref", "management", lexical_rank=1.0, role="cross_reference"),
        _row("evidence", "management", lexical_rank=0.9, role="evidence"),
    ]
    dense_scores = {"cross_ref": 0.9, "evidence": 0.1}

    out, _diag = rerank_with_embeddings(rows, dense_scores=dense_scores, rrf_k=79)

    scores = {r.passage.passage_id: r for r in out}
    assert scores["cross_ref"].rrf_score is not None
    assert scores["evidence"].rrf_score is not None
    assert scores["cross_ref"].rrf_score > scores["evidence"].rrf_score
    assert scores["cross_ref"].adjusted_score is not None
    assert scores["evidence"].adjusted_score is not None
    assert scores["cross_ref"].adjusted_score < scores["evidence"].adjusted_score
    assert [r.passage.passage_id for r in out] == ["evidence", "cross_ref"]


def test_empty_intents_all_evidence_preserves_prior_sort_order() -> None:
    rows = [
        _row("lexical_first", "management", lexical_rank=1.0, nbk_id="NBK2"),
        _row("dense_first", "genetic_counseling", lexical_rank=0.9, nbk_id="NBK1"),
        _row("lower_rrf", "summary", lexical_rank=0.8, nbk_id="NBK1"),
    ]
    dense_scores = {"dense_first": 0.9, "lexical_first": 0.1, "lower_rrf": 0.05}

    out, _diag = rerank_with_embeddings(
        rows,
        dense_scores=dense_scores,
        rrf_k=60,
        query_intents=[],
    )

    prior_sort_order = sorted(
        out,
        key=lambda r: (
            -(r.rrf_score or 0.0),
            -dense_scores.get(r.passage.passage_id, 0.0),
            {"summary": 0, "management": 1, "genetic_counseling": 2}.get(
                r.passage.chapter_section,
                100,
            ),
            r.passage.nbk_id,
            r.passage.passage_id,
        ),
    )
    assert [r.passage.passage_id for r in out] == [r.passage.passage_id for r in prior_sort_order]
    assert [r.adjusted_score for r in out] == [r.rrf_score for r in out]
    assert [r.role_multiplier for r in out] == [1.0, 1.0, 1.0]
    assert [r.intent_section_boost for r in out] == [0.0, 0.0, 0.0]


def test_adjusted_score_helper_defaults_unknown_role_to_evidence_multiplier() -> None:
    adjusted_score, role_multiplier, section_boost = adjusted_score_for(
        rrf_score=0.5,
        role="unknown",
        section="diagnosis",
        query_intents=["diagnosis"],
    )

    assert role_multiplier == ROLE_MULTIPLIER["evidence"]
    assert section_boost == pytest.approx(0.30)
    assert adjusted_score == pytest.approx(0.65)
