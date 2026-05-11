"""GET /passages/search — RAG-shaped retrieval from Postgres corpus.

The embedder is FakeEmbeddingProvider by default (no 130MB BGE model loaded at
boot). Set GENEREVIEW_EAGER_LOAD_BGE=true to use the real SentenceTransformer.
"""

from __future__ import annotations

from typing import Annotated, Literal, cast

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request

from genereview_link.models.genereview_models import (
    PassageDetail,
    RankedPassage,
    ScoreBreakdown,
)
from genereview_link.models.sections import SectionName
from genereview_link.retrieval.embeddings import EmbeddingProvider
from genereview_link.retrieval.repository import GeneReviewRepository
from genereview_link.retrieval.rerank import (
    SECTION_PRIORITY,
    rerank_with_embeddings,
)

router = APIRouter(tags=["Passages"])


async def get_repository(request: Request) -> GeneReviewRepository:
    """Resolve GeneReviewRepository from app state; 503 if not configured."""
    repo: GeneReviewRepository | None = getattr(request.app.state, "repository", None)
    if repo is None:
        raise HTTPException(
            status_code=503,
            detail="DATABASE_URL not configured — Postgres repository unavailable",
        )
    return repo


async def get_embedding_provider(request: Request) -> EmbeddingProvider:
    """Resolve EmbeddingProvider from app state."""
    embedder: EmbeddingProvider | None = getattr(request.app.state, "embedder", None)
    if embedder is None:
        raise HTTPException(status_code=503, detail="Embedding provider not initialised")
    return embedder


@router.get(
    "/passages/search",
    response_model=list[RankedPassage],
    operation_id="search_passages",
    summary="Hybrid lexical + dense RAG search across GeneReviews passages",
)
async def search_passages(
    q: Annotated[str, Query(min_length=1, max_length=500)],
    gene: Annotated[str | None, Query()] = None,
    nbk: Annotated[str | None, Query()] = None,
    sections: Annotated[list[str] | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    rerank: Annotated[Literal["rrf", "lexical", "off"], Query()] = "rrf",
    repo: Annotated[GeneReviewRepository, Depends(get_repository)] = ...,  # type: ignore[assignment]
    embedder: Annotated[EmbeddingProvider, Depends(get_embedding_provider)] = ...,  # type: ignore[assignment]
) -> list[RankedPassage]:
    """Search GeneReview passages using hybrid lexical + dense retrieval.

    Returns ranked passages from the active corpus. Use ``?rerank=lexical``
    to skip dense embedding (faster but lower quality). Use ``?rerank=off``
    to return raw BM25-style results.
    """
    lex = await repo.search_passages(
        q,
        gene_symbol=gene,
        nbk_id=nbk,
        sections=sections,
        limit=max(limit * 3, 50),
    )
    dense_scores: dict[str, float] = {}
    if rerank == "rrf":
        qv = await embedder.embed_query(q)
        active_table = await repo.active_embedding_table()
        dense_scores = await repo.dense_scores_for_passages(
            qv,
            [(r.passage.nbk_id, r.passage.passage_id) for r in lex],
            model_table=active_table,
        )
    ranked, _diag = rerank_with_embeddings(lex, dense_scores)
    ranked = ranked[:limit]

    out: list[RankedPassage] = []
    for pos, r in enumerate(ranked, start=1):
        out.append(
            RankedPassage(
                passage_id=r.passage.passage_id,
                nbk_id=r.passage.nbk_id,
                gene_symbols=list(r.gene_symbols),
                chapter_title=r.passage.chapter_title or "",
                chapter_last_updated=r.passage.chapter_last_updated,
                chapter_section=cast(SectionName, r.passage.chapter_section),
                heading_path=r.passage.heading_path,
                text=r.passage.text,
                char_count=len(r.passage.text),
                score_breakdown=ScoreBreakdown(
                    lexical_rank=r.lexical_rank,
                    phrase_rank=r.phrase_rank,
                    strict_rank=r.strict_rank,
                    recall_rank=r.recall_rank,
                    dense_score=dense_scores.get(r.passage.passage_id),
                    dense_rank=None,
                    rrf_score=None,
                    section_priority=SECTION_PRIORITY.get(r.passage.chapter_section, 100),
                    final_position=pos,
                ),
            )
        )
    return out


@router.get(
    "/passages/{passage_id}",
    response_model=PassageDetail,
    operation_id="get_passage",
    summary="Fetch a single GeneReviews passage by its passage_id.",
)
async def get_passage(
    passage_id: Annotated[
        str,
        Path(
            description=(
                "Globally unique passage identifier of the form "
                "'NBKxxxx:NNNN' (e.g. 'NBK1247:0022'). NBKxxxx is the "
                "chapter; NNNN is the 4-digit chunk index within the chapter."
            ),
            pattern=r"^NBK\d+:\d{4}$",
        ),
    ],
    repo: Annotated[GeneReviewRepository, Depends(get_repository)] = ...,  # type: ignore[assignment]
) -> PassageDetail:
    row = await repo.get_passage(passage_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"passage {passage_id!r} not found")
    return PassageDetail(
        passage_id=row.passage_id,
        nbk_id=row.nbk_id,
        chapter_title=row.chapter_title or "",
        chapter_last_updated=row.chapter_last_updated,
        chapter_section=cast(SectionName, row.chapter_section),
        heading_path=row.heading_path,
        section_level=row.section_level,
        chunk_index=row.chunk_index,
        text=row.text,
        char_count=len(row.text),
        gene_symbols=list(row.gene_symbols),
    )
