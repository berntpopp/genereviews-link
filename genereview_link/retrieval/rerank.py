"""RRF + adjusted_score reranker.

Ported from pubtator-link/services/review_context/{ranking,embedding_rerank}.py
with the simplification that there's only one source (FTP archive), so
source_priority is gone.

Sort key (tuple, descending adjusted score and RRF, then ascending priorities):
    (-adjusted_score, -rrf_score, -dense_score, SECTION_PRIORITY[section], nbk_id, passage_id)
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TypedDict

from genereview_link.retrieval.repository import LexicalPassageRow

SECTION_PRIORITY: Mapping[str, int] = {
    "summary": 0,
    "diagnosis": 0,
    "clinical_features": 1,
    "management": 1,
    "genetic_counseling": 2,
    "molecular_genetics": 2,
    "resources": 5,
    "other": 7,
    "references": 50,
}

GUARDED_SECTIONS = frozenset({"references"})
RRF_STRATEGY = "lexical_top_k_dense_rrf"
PRIMARY_GENE_BOOST = 1.25  # chapters where queried gene is primary rank higher

ROLE_MULTIPLIER: Mapping[str, float] = {
    "cross_reference": 0.4,
    "evidence": 1.0,
    "definition": 0.95,
    "table_caption": 0.85,
    "table_body": 1.0,
}


class QueryIntentBoost(TypedDict):
    patterns: tuple[str, ...]
    section_boost: Mapping[str, float]


QUERY_INTENT_BOOSTS: Mapping[str, QueryIntentBoost] = {
    "management": {
        "patterns": (
            "treatment",
            "management",
            "therapy",
            "surgery",
            "prophylactic",
            "risk-reducing",
            "screening",
            "surveillance",
            "intervention",
            "prevent",
            "prevention",
            "managing",
        ),
        "section_boost": {"management": 0.30},
    },
    "diagnosis": {
        "patterns": (
            "diagnosis",
            "diagnostic criteria",
            "establishing",
            "confirming",
            "differential",
            "differential diagnosis",
        ),
        "section_boost": {"diagnosis": 0.30, "clinical_features": 0.10},
    },
    "genetics": {
        "patterns": (
            "inheritance",
            "penetrance",
            "autosomal",
            "x-linked",
            "variant spectrum",
            "molecular genetics",
        ),
        "section_boost": {"molecular_genetics": 0.20, "genetic_counseling": 0.05},
    },
}


@dataclass(slots=True)
class RerankDiagnostics:
    enabled: bool = True
    active: bool = False
    candidate_count: int = 0
    embedded_candidate_count: int = 0
    missing_embedding_count: int = 0
    strategy: str | None = None
    fallback_reason: str | None = None


def detect_query_intents(query: str) -> list[str]:
    normalized_query = query.casefold()
    return sorted(
        intent
        for intent, boost in QUERY_INTENT_BOOSTS.items()
        if any(pattern in normalized_query for pattern in boost["patterns"])
    )


def _section_boost(section: str, query_intents: Sequence[str]) -> float:
    section_boost = 0.0
    for intent in query_intents:
        boost = QUERY_INTENT_BOOSTS.get(intent)
        if boost is not None:
            section_boost += boost["section_boost"].get(section, 0.0)
    return section_boost


def adjusted_score_for(
    *,
    rrf_score: float,
    role: str,
    section: str,
    query_intents: Sequence[str],
    primary_gene_match: bool = False,
) -> tuple[float, float, float]:
    role_multiplier = ROLE_MULTIPLIER.get(role, ROLE_MULTIPLIER["evidence"])
    section_boost = _section_boost(section, query_intents)
    adjusted = rrf_score * role_multiplier * (1.0 + section_boost)
    if primary_gene_match:
        adjusted = adjusted * PRIMARY_GENE_BOOST
    return (
        adjusted,
        role_multiplier,
        section_boost,
    )


def _section_key(row: LexicalPassageRow) -> int:
    return SECTION_PRIORITY.get(row.passage.chapter_section, 100)


def _is_guarded(row: LexicalPassageRow) -> bool:
    return row.passage.chapter_section in GUARDED_SECTIONS


def _rerank_key(row: LexicalPassageRow) -> tuple[float, int, str, str]:
    return (-row.lexical_rank, _section_key(row), row.passage.nbk_id, row.passage.passage_id)


def rerank_with_embeddings(
    rows: Sequence[LexicalPassageRow],
    dense_scores: Mapping[str, float],
    *,
    rrf_k: int = 60,
    query_intents: Sequence[str] = (),
) -> tuple[list[LexicalPassageRow], RerankDiagnostics]:
    """Rank lexical candidates with RRF plus role and intent score adjustments."""
    diag = RerankDiagnostics(
        candidate_count=len(rows),
        embedded_candidate_count=sum(1 for r in rows if r.passage.passage_id in dense_scores),
    )
    diag.missing_embedding_count = diag.candidate_count - diag.embedded_candidate_count

    if not rows:
        diag.fallback_reason = "no_candidates"
        return [], diag

    lex_sorted = sorted(rows, key=_rerank_key)
    lex_rank = {r.passage.passage_id: i + 1 for i, r in enumerate(lex_sorted)}
    lex_positioned = [
        dataclasses.replace(
            r,
            lexical_rank_position=lex_rank[r.passage.passage_id],
        )
        for r in lex_sorted
    ]

    if not dense_scores:
        diag.fallback_reason = "no_dense_scores"
        return lex_positioned, diag

    evidence = [r for r in lex_positioned if not _is_guarded(r)]
    guarded = [r for r in lex_positioned if _is_guarded(r)]
    if not evidence:
        diag.fallback_reason = "no_evidence_candidates"
        return guarded, diag

    diag.active = True
    diag.strategy = RRF_STRATEGY

    dense_sorted = sorted(
        (r for r in evidence if r.passage.passage_id in dense_scores),
        key=lambda r: (-dense_scores[r.passage.passage_id], _rerank_key(r)),
    )
    dense_rank = {r.passage.passage_id: i + 1 for i, r in enumerate(dense_sorted)}

    def rrf(r: LexicalPassageRow) -> float:
        score = 1.0 / (rrf_k + lex_rank[r.passage.passage_id])
        if r.passage.passage_id in dense_rank:
            score += 1.0 / (rrf_k + dense_rank[r.passage.passage_id])
        return score

    scored_evidence = []
    for r in evidence:
        rrf_score = rrf(r)
        adjusted_score, role_multiplier, section_boost = adjusted_score_for(
            rrf_score=rrf_score,
            role=r.passage.passage_role or "evidence",
            section=r.passage.chapter_section,
            query_intents=query_intents,
            primary_gene_match=r.primary_gene_match,
        )
        scored_evidence.append(
            dataclasses.replace(
                r,
                lexical_rank_position=lex_rank[r.passage.passage_id],
                dense_rank=dense_rank.get(r.passage.passage_id),
                rrf_score=rrf_score,
                adjusted_score=adjusted_score,
                role_multiplier=role_multiplier,
                intent_section_boost=section_boost,
            )
        )
    final_evidence = sorted(
        scored_evidence,
        key=lambda r: (
            -(r.adjusted_score if r.adjusted_score is not None else (r.rrf_score or 0.0)),
            -(r.rrf_score or 0.0),
            -dense_scores.get(r.passage.passage_id, 0.0),
            _section_key(r),
            r.passage.nbk_id,
            r.passage.passage_id,
        ),
    )
    return final_evidence + guarded, diag
