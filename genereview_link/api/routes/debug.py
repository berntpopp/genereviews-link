"""Debug routes — gated behind DEBUG_RANKING_ENABLED setting.

These routes expose internal ranking diagnostics and are excluded from MCP.
They should never be enabled in production without additional auth.
"""

from __future__ import annotations

from typing import Annotated, cast

from fastapi import APIRouter, Depends, HTTPException, Query

from genereview_link.api.routes.passages import (
    _fence_heading_path,
    _format_recommended_citation,
    _format_source_url,
    get_embedding_provider,
    get_repository,
)
from genereview_link.config import settings
from genereview_link.mcp.untrusted_content import fence_untrusted_text
from genereview_link.models.genereview_models import RankedPassage, ScoreBreakdown
from genereview_link.models.sections import SectionName
from genereview_link.retrieval.embeddings import EmbeddingProvider
from genereview_link.retrieval.repository import GeneReviewRepository
from genereview_link.retrieval.rerank import SECTION_PRIORITY, rerank_with_embeddings

router = APIRouter(prefix="/debug", tags=["Debug"], include_in_schema=False)


def _require_debug_enabled() -> None:
    if not settings.DEBUG_RANKING_ENABLED:
        raise HTTPException(status_code=404, detail="Not found")


@router.get("/ranking", dependencies=[Depends(_require_debug_enabled)])
async def debug_ranking(
    q: Annotated[str, Query(min_length=1, max_length=500)],
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
    repo: Annotated[GeneReviewRepository, Depends(get_repository)] = ...,  # type: ignore[assignment]
    embedder: Annotated[EmbeddingProvider, Depends(get_embedding_provider)] = ...,  # type: ignore[assignment]
) -> dict[str, object]:
    """Return full ScoreBreakdown for top-N lexical candidates (debug only).

    Gated behind DEBUG_RANKING_ENABLED=true. Returns 404 when disabled.
    Excluded from MCP tool exposure.
    """
    lex = await repo.search_passages(q, limit=max(limit * 3, 50))
    qv = await embedder.embed_query(q)
    active_table = await repo.active_embedding_table()
    dense_scores = await repo.dense_scores_for_passages(
        qv,
        [(r.passage.nbk_id, r.passage.passage_id) for r in lex],
        model_table=active_table,
    )
    ranked, diag = rerank_with_embeddings(lex, dense_scores)
    ranked = ranked[:limit]

    passages: list[RankedPassage] = []
    for pos, r in enumerate(ranked, start=1):
        passages.append(
            RankedPassage(
                passage_id=r.passage.passage_id,
                nbk_id=r.passage.nbk_id,
                gene_symbols=list(r.passage.gene_symbols),
                chapter_title=fence_untrusted_text(
                    r.passage.chapter_title or "",
                    source="genereviews",
                    record_id=f"{r.passage.passage_id}#chapter_title",
                ),
                chapter_last_updated=r.passage.chapter_last_updated,
                chapter_section=cast(SectionName, r.passage.chapter_section),
                heading_path=_fence_heading_path(r.passage.heading_path, r.passage.passage_id),
                passage_type=r.passage.passage_type,
                text=fence_untrusted_text(
                    r.passage.text, source="genereviews", record_id=r.passage.passage_id
                ),
                char_count=len(r.passage.text),
                score_breakdown=ScoreBreakdown(
                    lexical_rank=r.lexical_rank,
                    phrase_rank=r.phrase_rank,
                    strict_rank=r.strict_rank,
                    recall_rank=r.recall_rank,
                    dense_score=dense_scores.get(r.passage.passage_id),
                    dense_rank=r.dense_rank,
                    rrf_score=r.rrf_score,
                    section_priority=SECTION_PRIORITY.get(r.passage.chapter_section, 100),
                    final_position=pos,
                ),
                recommended_citation=_format_recommended_citation(
                    nbk_id=r.passage.nbk_id,
                    last_updated=r.passage.chapter_last_updated,
                    passage_id=r.passage.passage_id,
                ),
                table_id=r.passage.table_id if r.passage.passage_type == "table" else None,
                source_url=_format_source_url(r.passage.nbk_id),
            )
        )
    return {
        "query": q,
        "diagnostics": {
            "enabled": diag.enabled,
            "active": diag.active,
            "candidate_count": diag.candidate_count,
            "embedded_candidate_count": diag.embedded_candidate_count,
            "missing_embedding_count": diag.missing_embedding_count,
            "strategy": diag.strategy,
            "fallback_reason": diag.fallback_reason,
        },
        "passages": [p.model_dump() for p in passages],
    }
