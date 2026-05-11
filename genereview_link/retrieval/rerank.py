"""RRF + section_priority reranker.

Ported from pubtator-link/services/review_context/{ranking,embedding_rerank}.py
with the simplification that there's only one source (FTP archive), so
source_priority is gone.

Sort key (tuple, descending RRF, then ascending priorities):
    (-rrf_score, SECTION_PRIORITY[section], nbk_id, passage_id)
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

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


@dataclass(slots=True)
class RerankDiagnostics:
    enabled: bool = True
    active: bool = False
    candidate_count: int = 0
    embedded_candidate_count: int = 0
    missing_embedding_count: int = 0
    strategy: str | None = None
    fallback_reason: str | None = None


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
) -> tuple[list[LexicalPassageRow], RerankDiagnostics]:
    """Rank lexical candidates with RRF; section_priority is a tiebreaker."""
    diag = RerankDiagnostics(
        candidate_count=len(rows),
        embedded_candidate_count=sum(1 for r in rows if r.passage.passage_id in dense_scores),
    )
    diag.missing_embedding_count = diag.candidate_count - diag.embedded_candidate_count

    if not rows:
        diag.fallback_reason = "no_candidates"
        return [], diag

    lex_sorted = sorted(rows, key=_rerank_key)

    if not dense_scores:
        diag.fallback_reason = "no_dense_scores"
        return lex_sorted, diag

    evidence = [r for r in lex_sorted if not _is_guarded(r)]
    guarded = [r for r in lex_sorted if _is_guarded(r)]
    if not evidence:
        diag.fallback_reason = "no_evidence_candidates"
        return guarded, diag

    diag.active = True
    diag.strategy = RRF_STRATEGY

    lex_rank = {r.passage.passage_id: i + 1 for i, r in enumerate(lex_sorted)}
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

    final_evidence = sorted(
        evidence,
        key=lambda r: (
            -rrf(r),
            -dense_scores.get(r.passage.passage_id, 0.0),
            _section_key(r),
            r.passage.nbk_id,
            r.passage.passage_id,
        ),
    )
    return final_evidence + guarded, diag
