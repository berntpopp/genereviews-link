"""GET /passages/search — RAG-shaped retrieval from Postgres corpus.

The embedder is FakeEmbeddingProvider by default (no 130MB BGE model loaded at
boot). Set GENEREVIEW_EAGER_LOAD_BGE=true to use the real SentenceTransformer.
"""

from __future__ import annotations

import asyncio
import dataclasses
from datetime import UTC, date, datetime, timedelta
from typing import Annotated, Literal, cast, get_args

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from fastapi.responses import JSONResponse

from genereview_link.api.diagnostics import build_search_diagnostics
from genereview_link.api.errors import FieldError, StructuredHTTPException
from genereview_link.api.routes.table_enrichment import table_fields
from genereview_link.api.untrusted_limits import collect_untrusted, guard_untrusted_limits
from genereview_link.mcp.untrusted_content import UntrustedText, fence_untrusted_text
from genereview_link.models.genereview_models import (
    IdsOnlyPassage,
    IdsOnlySearchResponse,
    PassageBatchRequest,
    PassageBatchResponse,
    PassageDetail,
    PassageRole,
    PassageSearchResponse,
    PassageWindowResponse,
    RankedPassage,
    ResponseMeta,
    ScoreBreakdown,
    SearchDiagnosticsModel,
)
from genereview_link.models.sections import SECTION_NAMES, SectionName, canonicalize_nbk_id
from genereview_link.retrieval.embeddings import EmbeddingProvider
from genereview_link.retrieval.repository import GeneReviewRepository, LexicalPassageRow, PassageRow
from genereview_link.retrieval.rerank import (
    SECTION_PRIORITY,
    detect_query_intents,
    rerank_with_embeddings,
)

router = APIRouter(tags=["Passages"])

BATCH_MAX_IDS = 20
SECTION_VALUES_DESCRIPTION = ", ".join(f'"{section}"' for section in SECTION_NAMES)
PASSAGE_ROLE_VALUES: frozenset[str] = frozenset(str(role) for role in get_args(PassageRole))


def _format_recommended_citation(
    *,
    nbk_id: str,
    last_updated: date | None,
    passage_id: str,
) -> str:
    """Return the recommended_citation string (identifiers/date only).

    The chapter title is NOT embedded here (v1.1 no-duplication): it is emitted
    once, fenced, on the ``chapter_title`` field. This keeps the citation a
    prose-free, pasteable anchor of stable identifiers + freshness date.
    """
    date_str = last_updated.isoformat() if isinstance(last_updated, date) else "date n/a"
    return f"{nbk_id}. Updated {date_str}. Passage {passage_id}."


def _fence_heading_path(heading_path: str | None, passage_id: str) -> UntrustedText | None:
    """Fence a passage's upstream heading path (v1.1); None stays None."""
    if heading_path is None:
        return None
    return fence_untrusted_text(
        heading_path, source="genereviews", record_id=f"{passage_id}#heading_path"
    )


# When include=table_data emits the structured header/rows cells, the passage
# body text must NOT also carry the rendered table markdown (the SAME cell prose)
# — v1.1 no-duplication. In that mode the structured cells are the single
# canonical carrier and the body text is this server-synthesized pointer note.
_TABLE_BODY_PLACEHOLDER = (
    "[Table content is emitted as structured cells in the header and rows fields.]"
)


def _table_body_placeholder(passage_id: str) -> UntrustedText:
    return fence_untrusted_text(
        _TABLE_BODY_PLACEHOLDER, source="genereviews", record_id=f"{passage_id}#table_cells_ref"
    )


def _format_source_url(nbk_id: str) -> str:
    """Chapter-level NCBI Bookshelf URL for a passage's containing chapter.

    Per-passage anchors (section-level deep links) require the chapter's NXML
    ``short_name``, which isn't currently projected onto PassageRow. Returning
    the chapter URL still resolves the "no URL anywhere on responses" gap.
    """
    return f"https://www.ncbi.nlm.nih.gov/books/{nbk_id}/"


def _passage_role(role: str | None) -> PassageRole | None:
    if role is None or role not in PASSAGE_ROLE_VALUES:
        return None
    return cast(PassageRole, role)


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
    response_model=PassageSearchResponse | IdsOnlySearchResponse,
    response_model_by_alias=True,
    operation_id="search_passages",
    summary="Hybrid lexical + dense RAG search across GeneReviews passages.",
    description=(
        "Returns ranked passages from the active GeneReviews corpus. "
        'For intervention/treatment queries, pass sections=["management"]; '
        'for diagnostic-criteria queries, pass sections=["diagnosis", "clinical_features"]. '
        "This is the biggest precision lever.\n\n"
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
        "to trim response payload further.\n\n"
        "Latency: ~27ms p50 (rerank=rrf), ~26ms p50 (rerank=lexical), "
        "~26ms p50 (rerank=off)."
    ),
)
async def search_passages(
    q: Annotated[
        str | None,
        Query(
            min_length=1,
            max_length=512,
            description=("Query string (canonical). Either q or query is required."),
        ),
    ] = None,
    query: Annotated[
        str | None,
        Query(
            min_length=1,
            max_length=512,
            description="Alias for q (cross-MCP convention).",
        ),
    ] = None,
    gene: Annotated[
        str | None,
        Query(
            description=(
                "Filter to a single HGNC gene symbol (e.g. 'BRCA1'). "
                "Matches any chapter whose gene_symbols array contains this value."
            ),
        ),
    ] = None,
    gene_role: Annotated[
        Literal["any", "primary", "mentioned"],
        Query(
            description=(
                "Filter by gene role in the chapter. 'any' (default): gene in "
                "gene_symbols (current behaviour). 'primary': gene in "
                "primary_gene_symbols (chapter-defining gene). 'mentioned': gene "
                "in gene_symbols but NOT in primary_gene_symbols. "
                "Requires gene to be set; ignored when gene is absent. "
                "Note: primary_gene_symbols is populated on re-ingest; existing "
                "installs default to '{}' until then."
            ),
        ),
    ] = "any",
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
                f"Restrict to one or more canonical sections. Values: {SECTION_VALUES_DESCRIPTION}."
            ),
        ),
    ] = None,
    heading_path_contains: Annotated[
        str | None,
        Query(
            min_length=1,
            max_length=200,
            description=("Case-insensitive substring filter on heading_path. Applied pre-rerank."),
        ),
    ] = None,
    mode: Annotated[
        Literal["brief", "full", "ids_only"],
        Query(
            description=(
                'Values: "brief" (default; snippet + IDs, ~3 KB), '
                '"full" (full text), "ids_only" (lean rows: `passage_id` + '
                "`rrf_score` + `lexical_rank_position` + `chapter_section`). "
                "Use ids_only for bulk-triage workflows; include/exclude flags "
                "and recommended_citation are not emitted in this mode."
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
                'Optional field projection. Values: "score_breakdown" '
                '(drops the opt-in score_breakdown field), "heading_path" '
                "(drops heading_path from every row). Use when you only need "
                "text + passage_id."
            )
        ),
    ] = None,
    include: Annotated[
        list[Literal["score_breakdown", "table_data"]] | None,
        Query(
            description=(
                'Opt into default-off response fields. Values: "score_breakdown" '
                "(returns raw lexical/dense ranks and populates "
                "_meta.dense_model_id + embedding_dim), "
                '"table_data" (for table passages: populates v1.1-fenced header '
                "+ rows cells; narrative passages remain unaffected)."
            )
        ),
    ] = None,
    snippet_chars: Annotated[
        int,
        Query(
            ge=80,
            le=800,
            description=(
                "Approximate snippet length in characters (brief mode only; ignored "
                "for full/ids_only). Default 400. Maps to ts_headline MaxFragments and MaxWords."
            ),
        ),
    ] = 400,
    rerank: Annotated[
        Literal["rrf", "lexical", "off"],
        Query(
            description=(
                'Values: "rrf" (default; reciprocal-rank fusion of weighted lexical '
                "+ dense embedding rank - best for clinical-concept queries), "
                '"lexical" (weighted lexical score with section-priority tiebreaker - '
                "best for exact gene-symbol or variant strings; for multi-token "
                'clinical concept queries, use "rrf"), "off" (raw repository '
                "order - debugging only; do not rely on ordering)."
            ),
        ),
    ] = "rrf",
    repo: Annotated[GeneReviewRepository, Depends(get_repository)] = ...,  # type: ignore[assignment]
    embedder: Annotated[EmbeddingProvider, Depends(get_embedding_provider)] = ...,  # type: ignore[assignment]
    request: Request = ...,  # type: ignore[assignment]
) -> PassageSearchResponse | IdsOnlySearchResponse | JSONResponse:
    if q is not None and query is not None and q != query:
        raise StructuredHTTPException(
            status_code=422,
            code="conflicting_query_param",
            message="both q and query supplied with different values",
            recovery_hint="pass only one of q or query, or pass the same string in both",
        )
    if not q and not query:
        raise StructuredHTTPException(
            status_code=422,
            code="missing_query",
            message="one of q or query is required",
            recovery_hint="pass q='your query string'",
        )
    q = q or query
    assert q is not None
    if nbk_id is not None:
        nbk_id = canonicalize_nbk_id(nbk_id)
    query_intents = detect_query_intents(q)

    if gene:
        idx = getattr(request.app.state, "gene_index", None)
        if idx is not None and not idx.is_indexed(gene):
            suggestions = idx.close_matches(gene, limit=3)
            raise StructuredHTTPException(
                status_code=400,
                code="gene_not_indexed",
                message=f"gene symbol {gene!r} is not indexed in the corpus",
                recovery_hint=(
                    "use the canonical HGNC symbol; aliases (e.g., 'hMLH1' for 'MLH1')"
                    " are not supported"
                ),
                field_errors=(
                    [
                        FieldError(
                            field="gene",
                            reason="symbol not found in the indexed corpus",
                            valid_values=suggestions,
                        )
                    ]
                    if suggestions
                    else None
                ),
                next_commands=(
                    [
                        {"tool": "search_passages", "arguments": {"q": "<query>", "gene": s}}
                        for s in suggestions
                    ]
                    or None
                ),
            )

    # Convert snippet_chars to ts_headline tuning parameters.
    # snippet_max_fragments and snippet_max_words are integer-bounded by FastAPI's
    # ge/le validators (snippet_chars in [80, 800]), so it is safe to use them
    # in an f-string for the ts_headline options string — no raw user input reaches SQL.
    snippet_max_fragments = max(1, snippet_chars // 200)
    snippet_max_words = max(15, min(60, snippet_chars // 7))

    k_parallel = 200  # widened from 50 to fix the recall ceiling
    sections_tuple = tuple(sections) if sections else None

    dense_scores: dict[str, float] = {}
    lex: list[LexicalPassageRow]

    if rerank == "rrf":
        qv = await embedder.embed_query(q)

        # Parallel retrieval: lexical-top-200 and dense-top-200, both filter-aware.
        lexical_task = asyncio.create_task(
            repo.search_passages(
                q,
                gene_symbol=gene,
                nbk_id=nbk_id,
                sections=list(sections_tuple) if sections_tuple else None,
                heading_path_contains=heading_path_contains,
                limit=k_parallel,
                brief=(mode == "brief"),
                snippet_max_fragments=snippet_max_fragments,
                snippet_max_words=snippet_max_words,
                gene_role=gene_role,
            )
        )
        dense_task = asyncio.create_task(
            repo._dense_candidates_filtered(
                query_vector=qv,
                gene=gene,
                nbk_id=nbk_id,
                sections=sections_tuple,
                heading_path_contains=heading_path_contains,
                top_k=k_parallel,
                gene_role=gene_role,
            )
        )
        lex_rows, dense_rows = await asyncio.gather(lexical_task, dense_task)

        # Build dense_scores dict from dense candidates.
        dense_scores = {
            str(r["passage_id"]): float(r["dense_score"])  # type: ignore[arg-type]
            for r in dense_rows
        }

        # Find passage_ids that are in dense results but not in lexical results.
        lex_ids = {r.passage.passage_id for r in lex_rows}
        dense_only_ids = [
            str(r["passage_id"]) for r in dense_rows if str(r["passage_id"]) not in lex_ids
        ]

        # Hydrate dense-only passage_ids with full passage data (option a).
        dense_only_passages: dict[str, PassageRow] = {}
        if dense_only_ids:
            dense_only_passages = await repo.fetch_passages_by_ids(dense_only_ids)

        # Build LexicalPassageRow objects for dense-only candidates.
        # These have zero lexical scores so they compete only via dense rank in RRF.
        # Set primary_gene_match=True when the queried gene is in primary_gene_symbols.
        dense_only_rows: list[LexicalPassageRow] = [
            LexicalPassageRow(
                passage=p,
                phrase_rank=0.0,
                strict_rank=0.0,
                recall_rank=0.0,
                recall_overlap_count=0,
                lexical_rank=0.0,
                snippet=None,
                primary_gene_match=bool(gene and gene in p.primary_gene_symbols),
            )
            for pid in dense_only_ids
            if (p := dense_only_passages.get(pid)) is not None
        ]

        # Annotate lexical rows with primary_gene_match for ranker boost.
        lex_rows = [
            dataclasses.replace(
                r,
                primary_gene_match=bool(gene and gene in r.passage.primary_gene_symbols),
            )
            for r in lex_rows
        ]

        lex = list(lex_rows) + dense_only_rows

    else:
        raw_lex = await repo.search_passages(
            q,
            gene_symbol=gene,
            nbk_id=nbk_id,
            sections=list(sections_tuple) if sections_tuple else None,
            heading_path_contains=heading_path_contains,
            limit=max(limit * 3, 50),
            brief=(mode == "brief"),
            snippet_max_fragments=snippet_max_fragments,
            snippet_max_words=snippet_max_words,
            gene_role=gene_role,
        )
        lex = [
            dataclasses.replace(
                r,
                primary_gene_match=bool(gene and gene in r.passage.primary_gene_symbols),
            )
            for r in raw_lex
        ]

    if rerank == "off":
        # Truly raw lexical order from the repo (no section_priority tiebreak).
        ranked = list(lex)
    else:
        ranked, _diag = rerank_with_embeddings(lex, dense_scores, query_intents=query_intents)
    ranked = ranked[:limit]

    corpus = _get_corpus_version(request)

    applied_filters: list[str] = []
    if gene:
        applied_filters.append(f"gene={gene}")
        if gene_role != "any":
            applied_filters.append(f"gene_role={gene_role}")
    if nbk_id:
        applied_filters.append(f"nbk_id={nbk_id}")
    if sections:
        applied_filters.append(f"sections={','.join(sections)}")
    if heading_path_contains:
        applied_filters.append(f"heading_path_contains={heading_path_contains}")

    diagnostics_model = SearchDiagnosticsModel(
        rerank_used=rerank,
        lexical_candidate_count=len(lex),
        dense_candidate_count=len(dense_scores) if rerank == "rrf" else None,
        applied_filters=applied_filters,
        section_filters=list(sections) if sections else [],
        suggestions=[],
        query_intents=query_intents,
    )
    if not ranked:
        unfiltered_lexical_count: int | None = None
        if applied_filters:
            unfiltered_lex = await repo.search_passages(
                q,
                gene_symbol=None,
                nbk_id=None,
                sections=None,
                heading_path_contains=None,
                limit=max(limit * 3, 50),
                brief=False,
                snippet_max_fragments=snippet_max_fragments,
                snippet_max_words=snippet_max_words,
            )
            unfiltered_lexical_count = len(unfiltered_lex)
            diagnostics_model.unfiltered_lexical_count = unfiltered_lexical_count
        diag = build_search_diagnostics(
            query=q,
            applied_filters=applied_filters,
            lexical_candidate_count=len(lex),
            unfiltered_lexical_count=unfiltered_lexical_count,
        )
        diagnostics_model.suggestions = diag.suggestions
    ingest_dates = [
        dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)
        for dt in (r.passage.chapter_ingested_at for r in ranked[:3])
        if dt is not None
    ]
    if (
        ingest_dates
        and datetime.now(UTC) - min(ingest_dates) > timedelta(days=180)
        and "corpus-may-be-stale" not in diagnostics_model.suggestions
    ):
        diagnostics_model.suggestions.append("corpus-may-be-stale")

    if mode == "ids_only":
        meta = ResponseMeta(corpus_version=corpus, diagnostics=diagnostics_model)
        return IdsOnlySearchResponse(  # type: ignore[call-arg]
            results=[
                IdsOnlyPassage(
                    passage_id=r.passage.passage_id,
                    nbk_id=r.passage.nbk_id,
                    chapter_section=cast(SectionName, r.passage.chapter_section),
                    rrf_score=r.rrf_score,
                    lexical_rank_position=r.lexical_rank_position,
                )
                for r in ranked
            ],
            meta=meta,
        )

    include_set = set(include or [])
    include_score_breakdown = "score_breakdown" in include_set
    include_table_data = "table_data" in include_set

    out: list[RankedPassage] = []
    for pos, r in enumerate(ranked, start=1):
        score_breakdown = (
            ScoreBreakdown(
                lexical_rank=r.lexical_rank,
                phrase_rank=r.phrase_rank,
                strict_rank=r.strict_rank,
                recall_rank=r.recall_rank,
                adjusted_score=r.adjusted_score,
                role_multiplier=r.role_multiplier,
                intent_section_boost=r.intent_section_boost,
                passage_role=_passage_role(r.passage.passage_role),
                dense_score=dense_scores.get(r.passage.passage_id),
                dense_rank=r.dense_rank,
                rrf_score=r.rrf_score,
                section_priority=SECTION_PRIORITY.get(r.passage.chapter_section, 100),
                final_position=pos,
            )
            if include_score_breakdown
            else None
        )
        tdata = table_fields(r.passage, want=include_table_data)
        # v1.1: RankedPassage populates EITHER text OR snippet, never both.
        # Fence whichever is populated at this MCP serialization boundary. When
        # structured table cells are emitted, the body is a pointer note instead
        # of the rendered table markdown (no-duplication).
        table_cells_emitted = tdata.get("header") is not None
        pid = r.passage.passage_id
        fenced_text: UntrustedText | None = None
        fenced_snippet: UntrustedText | None = None
        if mode == "full":
            fenced_text = (
                _table_body_placeholder(pid)
                if table_cells_emitted
                else fence_untrusted_text(r.passage.text, source="genereviews", record_id=pid)
            )
        elif mode == "brief" and r.snippet is not None:
            fenced_snippet = (
                _table_body_placeholder(pid)
                if table_cells_emitted
                else fence_untrusted_text(r.snippet, source="genereviews", record_id=pid)
            )
        out.append(
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
                chapter_ingested_at=r.passage.chapter_ingested_at,
                chapter_section=cast(SectionName, r.passage.chapter_section),
                heading_path=_fence_heading_path(r.passage.heading_path, r.passage.passage_id),
                passage_type=r.passage.passage_type,
                passage_role=_passage_role(r.passage.passage_role),
                text=fenced_text,
                snippet=fenced_snippet,
                char_count=len(r.passage.text),
                rrf_score=r.rrf_score,
                lexical_score=r.lexical_rank,
                lexical_rank_position=r.lexical_rank_position,
                dense_rank_position=r.dense_rank,
                score_breakdown=score_breakdown,
                recommended_citation=_format_recommended_citation(
                    nbk_id=r.passage.nbk_id,
                    last_updated=r.passage.chapter_last_updated,
                    passage_id=r.passage.passage_id,
                ),
                table_id=r.passage.table_id if r.passage.passage_type == "table" else None,
                source_url=_format_source_url(r.passage.nbk_id),
                **tdata,
            )
        )

    # Aggregate EVERY fenced object this response emits (text/snippet + fenced
    # table-data cells) into one limit check. search limit maxes at 100, but a
    # wide table-data passage can add many cells, so use the generous ceiling.
    guard_untrusted_limits(collect_untrusted(out))

    if include_score_breakdown:
        meta = ResponseMeta(
            corpus_version=corpus,
            diagnostics=diagnostics_model,
            dense_model_id=getattr(request.app.state, "dense_model_id", None),
            embedding_dim=getattr(request.app.state, "embedding_dim", None),
        )
    else:
        meta = ResponseMeta(corpus_version=corpus, diagnostics=diagnostics_model)

    # score_breakdown and table_data fields are opt-in.
    # Always exclude them from model_dump, then re-inject only when requested.
    excluded: set[str] = {str(field) for field in (exclude or [])}
    if not include_score_breakdown:
        excluded.add("score_breakdown")
    if not include_table_data:
        excluded.update({"header", "rows"})

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
        "The ``_meta`` field carries attribution and the active corpus version.\n\n"
        "Latency: ~1ms p50 (neighbors=0), ~1ms p50 (neighbors=3)."
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
    include: Annotated[
        list[Literal["table_data"]] | None,
        Query(
            description=(
                "Opt into table_data (v1.1-fenced header + rows cells for table passages)."
            ),
        ),
    ] = None,
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

    include_set = set(include or [])
    include_table_data = "table_data" in include_set

    focal_detail = _passage_row_to_detail(focal, include_table_data=include_table_data)
    before_details = [
        _passage_row_to_detail(r, include_table_data=include_table_data) for r in before
    ]
    after_details = [
        _passage_row_to_detail(r, include_table_data=include_table_data) for r in after
    ]
    response = PassageWindowResponse(  # type: ignore[call-arg]
        passage=focal_detail,
        neighbors_before=before_details,
        neighbors_after=after_details,
        has_more_before=has_more_before,
        has_more_after=has_more_after,
        meta=ResponseMeta(corpus_version=_get_corpus_version(request)),
    )
    guard_untrusted_limits(collect_untrusted(response))
    return response


def _passage_row_to_detail(
    row: PassageRow,
    *,
    include_table_data: bool = False,
) -> PassageDetail:
    """Convert a PassageRow to a PassageDetail response model (v1.1-fenced)."""
    tdata = table_fields(row, want=include_table_data)
    # When structured table cells are emitted, the body text is a pointer note
    # (not the rendered table markdown) so the cell prose lives once (no-dup).
    if tdata.get("header") is not None:
        fenced_text = _table_body_placeholder(row.passage_id)
    else:
        fenced_text = fence_untrusted_text(row.text, source="genereviews", record_id=row.passage_id)
    return PassageDetail(
        nbk_id=row.nbk_id,
        passage_id=row.passage_id,
        chapter_title=fence_untrusted_text(
            row.chapter_title or "",
            source="genereviews",
            record_id=f"{row.passage_id}#chapter_title",
        ),
        chapter_last_updated=row.chapter_last_updated,
        chapter_section=cast(SectionName, row.chapter_section),
        heading_path=_fence_heading_path(row.heading_path, row.passage_id),
        section_level=row.section_level,
        chunk_index=row.chunk_index,
        text=fenced_text,
        char_count=len(row.text),
        gene_symbols=list(row.gene_symbols),
        passage_type=row.passage_type,
        passage_role=_passage_role(row.passage_role),
        recommended_citation=_format_recommended_citation(
            nbk_id=row.nbk_id,
            last_updated=row.chapter_last_updated,
            passage_id=row.passage_id,
        ),
        source_url=_format_source_url(row.nbk_id),
        **tdata,
    )


@router.post(
    "/passages/batch",
    response_model=PassageBatchResponse,
    response_model_by_alias=True,
    operation_id="get_passages_batch",
    summary="Fetch up to 20 passages by id in a single request.",
    description=(
        "Returns the requested passages in the same order as the input ``ids`` list.\n\n"
        "Returns 200 even with partial misses; ``missing_ids`` lists unresolved ids.\n\n"
        "Returns 422 on empty list or per-id regex failure (FastAPI/Pydantic validation).\n\n"
        "Returns 413 with ``code='batch_size_exceeded'`` when the list has more than 20 ids."
    ),
)
async def get_passages_batch(
    body: PassageBatchRequest,
    request: Request,
    repo: Annotated[GeneReviewRepository, Depends(get_repository)],
) -> PassageBatchResponse:
    """Fetch up to 20 passages by id in a single request.

    Returns 200 even with partial misses; missing_ids lists unresolved ids.
    Returns 422 on empty list or per-id regex failure (FastAPI/Pydantic validation).
    Returns 413 with code='batch_size_exceeded' when the list has more than 20 ids.
    """
    if len(body.ids) > BATCH_MAX_IDS:
        raise StructuredHTTPException(
            status_code=413,
            code="batch_size_exceeded",
            message=f"batch size {len(body.ids)} exceeds limit {BATCH_MAX_IDS}",
            recovery_hint=f"split the request into chunks of {BATCH_MAX_IDS} ids each",
            next_commands=[
                {
                    "tool": "get_passages_batch",
                    "arguments": {"ids": body.ids[:BATCH_MAX_IDS]},
                },
            ],
        )

    include_set = set(body.include or [])
    include_table_data = "table_data" in include_set

    found: list[PassageDetail] = []
    missing: list[str] = []

    async with repo._acquire() as conn:
        await conn.execute("set search_path to genereview, public")
        for pid in body.ids:
            row = await repo._fetch_passage_row(conn, pid)
            if row is None:
                missing.append(pid)
                continue
            found.append(_passage_row_to_detail(row, include_table_data=include_table_data))

    response = PassageBatchResponse(  # type: ignore[call-arg]
        passages=found,
        missing_ids=missing,
        meta=ResponseMeta(corpus_version=_get_corpus_version(request)),
    )
    guard_untrusted_limits(collect_untrusted(response))
    return response
