"""GET /passages/search — RAG-shaped retrieval from Postgres corpus.

The embedder is FakeEmbeddingProvider by default (no 130MB BGE model loaded at
boot). Set GENEREVIEW_EAGER_LOAD_BGE=true to use the real SentenceTransformer.
"""

from __future__ import annotations

from typing import Annotated, Literal, cast

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from fastapi.responses import JSONResponse

from genereview_link.api.diagnostics import build_search_diagnostics
from genereview_link.api.errors import StructuredHTTPException
from genereview_link.models.genereview_models import (
    PassageDetail,
    PassageSearchResponse,
    PassageWindowResponse,
    RankedPassage,
    ResponseMeta,
    ScoreBreakdown,
    SearchDiagnosticsModel,
)
from genereview_link.models.sections import SectionName
from genereview_link.retrieval.embeddings import EmbeddingProvider
from genereview_link.retrieval.repository import GeneReviewRepository, PassageRow
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


def _get_corpus_version(request: Request) -> str | None:
    """Read the active corpus version cached on app.state during lifespan."""
    return getattr(request.app.state, "corpus_version", None)


@router.get(
    "/passages/search",
    response_model=PassageSearchResponse,
    response_model_by_alias=True,
    operation_id="search_passages",
    summary="Hybrid lexical + dense RAG search across GeneReviews passages.",
    description=(
        "Returns ranked passages from the active GeneReviews corpus.\n\n"
        "**Rerank modes:**\n"
        "- `rrf` (default): RRF over three-tsquery lexical + BGE-small "
        "dense cosine. Balanced quality. Use this for general questions.\n"
        "- `lexical`: skip the dense pass; lexical scoring only. "
        "Faster - saves the embed + HNSW probe round-trip. Use for "
        "latency-critical exact-term lookups.\n"
        "- `off`: lexical-only, NO section_priority tiebreaker. Returns "
        "rows in the repository's own lexical_rank order. Debugging / "
        "diagnostic use only.\n\n"
        "Use `mode='brief'` (default) for triage - returns "
        "~300-500-char `ts_headline` snippets with **bold** highlights "
        "around query terms. Switch to `mode='full'` once you've "
        "picked the row(s) you want to read.\n\n"
        "Filter with `gene` (HGNC symbol), `nbk_id` (single chapter), "
        "or `sections` (list; valid values in the sections JSONSchema "
        "enum). Use `exclude=score_breakdown` or `exclude=heading_path` "
        "to trim response payload further."
    ),
)
async def search_passages(
    q: Annotated[
        str,
        Query(
            min_length=1,
            max_length=500,
            description="Free-text query. Phrases, gene symbols, and clinical terms all work.",
        ),
    ],
    gene: Annotated[
        str | None,
        Query(
            description=(
                "Filter to a single HGNC gene symbol (e.g. 'BRCA1'). "
                "Matches any chapter whose gene_symbols array contains this value."
            ),
        ),
    ] = None,
    nbk_id: Annotated[
        str | None,
        Query(
            description="Restrict results to one chapter, e.g. 'NBK1247'.",
        ),
    ] = None,
    sections: Annotated[
        list[SectionName] | None,
        Query(
            description=(
                "Restrict to one or more canonical sections. Valid values "
                "are listed in this parameter's JSONSchema enum."
            ),
        ),
    ] = None,
    mode: Annotated[
        Literal["brief", "full"],
        Query(
            description=(
                "brief (default): each row carries a ts_headline snippet "
                "(2 fragments, ~30-60 words total, **bold** highlights around "
                "query terms - roughly 300-500 chars per row, so <= ~3 KB "
                "total at limit=5). full: each row carries the entire "
                "passage text - pick this only when you have already chosen "
                "the row(s) you want to read."
            ),
        ),
    ] = "brief",
    limit: Annotated[
        int,
        Query(
            ge=1,
            le=100,
            description="Number of rows to return. Default 5 keeps the brief-mode payload <= ~3 KB.",
        ),
    ] = 5,
    exclude: Annotated[
        list[Literal["score_breakdown", "heading_path"]] | None,
        Query(
            description=(
                "Optional field projection. Each listed value is dropped "
                "from every row. Use when you only need text + passage_id."
            )
        ),
    ] = None,
    include: Annotated[
        list[Literal["score_breakdown"]] | None,
        Query(
            description=(
                "Opt into default-off response fields. Currently supports "
                "'score_breakdown' (raw lexical/dense ranks). Use for ranker "
                "debugging."
            )
        ),
    ] = None,
    rerank: Annotated[
        Literal["rrf", "lexical", "off"],
        Query(
            description="See route description for operational guidance.",
        ),
    ] = "rrf",
    repo: Annotated[GeneReviewRepository, Depends(get_repository)] = ...,  # type: ignore[assignment]
    embedder: Annotated[EmbeddingProvider, Depends(get_embedding_provider)] = ...,  # type: ignore[assignment]
    request: Request = ...,  # type: ignore[assignment]
) -> PassageSearchResponse | JSONResponse:
    lex = await repo.search_passages(
        q,
        gene_symbol=gene,
        nbk_id=nbk_id,
        sections=list(sections) if sections else None,
        limit=max(limit * 3, 50),
        brief=(mode == "brief"),
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

    if rerank == "off":
        # Truly raw lexical order from the repo (no section_priority tiebreak).
        ranked = list(lex)
    else:
        ranked, _diag = rerank_with_embeddings(lex, dense_scores)
    ranked = ranked[:limit]

    include_set = set(include or [])
    include_score_breakdown = "score_breakdown" in include_set

    out: list[RankedPassage] = []
    for pos, r in enumerate(ranked, start=1):
        score_breakdown = (
            ScoreBreakdown(
                lexical_rank=r.lexical_rank,
                phrase_rank=r.phrase_rank,
                strict_rank=r.strict_rank,
                recall_rank=r.recall_rank,
                dense_score=dense_scores.get(r.passage.passage_id),
                dense_rank=r.dense_rank,
                rrf_score=r.rrf_score,
                section_priority=SECTION_PRIORITY.get(r.passage.chapter_section, 100),
                final_position=pos,
            )
            if include_score_breakdown
            else None
        )
        out.append(
            RankedPassage(
                passage_id=r.passage.passage_id,
                nbk_id=r.passage.nbk_id,
                gene_symbols=list(r.passage.gene_symbols),
                chapter_title=r.passage.chapter_title or "",
                chapter_last_updated=r.passage.chapter_last_updated,
                chapter_section=cast(SectionName, r.passage.chapter_section),
                heading_path=r.passage.heading_path,
                text=r.passage.text if mode == "full" else None,
                snippet=r.snippet if mode == "brief" else None,
                char_count=len(r.passage.text),
                score_breakdown=score_breakdown,
            )
        )

    corpus = _get_corpus_version(request)

    diagnostics_model: SearchDiagnosticsModel | None = None
    if not out:
        applied: list[str] = []
        if gene:
            applied.append(f"gene={gene}")
        if sections:
            applied.append(f"sections={','.join(sections)}")
        if nbk_id:
            applied.append(f"nbk_id={nbk_id}")
        diag = build_search_diagnostics(
            query=q,
            applied_filters=applied,
            lexical_hits=len(lex),
            lexical_hits_after_filters=len(out),
        )
        diagnostics_model = SearchDiagnosticsModel(
            lexical_hits=diag.lexical_hits,
            lexical_hits_after_filters=diag.lexical_hits_after_filters,
            applied_filters=diag.applied_filters,
            suggestions=diag.suggestions,
        )

    meta = ResponseMeta(corpus_version=corpus, diagnostics=diagnostics_model)

    # score_breakdown is opt-in (absent by default). Always exclude it from
    # model_dump, then re-inject only when the caller requested it.
    excluded: set[str] = {str(field) for field in (exclude or [])}
    if not include_score_breakdown:
        excluded.add("score_breakdown")

    if excluded:
        return JSONResponse(
            {
                "results": [row.model_dump(exclude=excluded, mode="json") for row in out],
                "_meta": meta.model_dump(),
            }
        )
    return PassageSearchResponse(results=out, meta=meta)  # type: ignore[call-arg]


@router.get(
    "/passages/{passage_id}",
    response_model=PassageWindowResponse,
    response_model_by_alias=True,
    operation_id="get_passage",
    summary="Fetch a GeneReviews passage by its passage_id, with optional context window.",
    description=(
        "Returns the focal passage wrapped in a ``PassageWindowResponse`` envelope. "
        "Use ``neighbors`` (0-5) to fetch adjacent chunks before and after the focal "
        "passage within the same section. Set ``cross_sections=true`` to allow neighbors "
        "to span section boundaries within the same chapter.\n\n"
        "The ``_meta`` field carries attribution and the active corpus version."
    ),
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
    neighbors: Annotated[
        int,
        Query(
            ge=0,
            le=5,
            description=(
                "Fetch this many adjacent chunks before and after the focal passage. "
                "Default 0 returns only the focal passage with empty neighbor lists."
            ),
        ),
    ] = 0,
    cross_sections: Annotated[
        bool,
        Query(
            description=(
                "If true, neighbors may span across section boundaries within the same "
                "chapter. Default false keeps neighbors within the same section."
            ),
        ),
    ] = False,
    repo: Annotated[GeneReviewRepository, Depends(get_repository)] = ...,  # type: ignore[assignment]
    request: Request = ...,  # type: ignore[assignment]
) -> PassageWindowResponse:
    focal, before, after, has_more_before, has_more_after = await repo.get_passage_window(
        passage_id, before=neighbors, after=neighbors, cross_sections=cross_sections
    )
    if focal is None:
        raise StructuredHTTPException(
            status_code=404,
            code="passage_not_found",
            message=f"passage {passage_id!r} not found",
            recovery_hint=(
                "passage_id has the form NBKxxxx:NNNN. Use search_passages "
                "to discover valid passage_ids, or get_chapter_section to "
                "list all passages in a section."
            ),
            next_commands=[
                {"tool": "search_passages", "arguments": {"q": "<your query>"}},
            ],
        )

    def _to_detail(row: PassageRow) -> PassageDetail:
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

    return PassageWindowResponse(  # type: ignore[call-arg]
        passage=_to_detail(focal),
        neighbors_before=[_to_detail(r) for r in before],
        neighbors_after=[_to_detail(r) for r in after],
        has_more_before=has_more_before,
        has_more_after=has_more_after,
        meta=ResponseMeta(corpus_version=_get_corpus_version(request)),
    )
