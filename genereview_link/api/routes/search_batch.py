"""POST /passages/search/batch — run several independent passage searches in one call.

This module deliberately imports and calls the existing ``search_passages``
handler from ``passages.py`` directly (as a plain async function, bypassing
FastAPI dependency injection) so that all retrieval and rerank logic lives in
exactly one place.  No search or rerank logic is duplicated here.

Architecture constraint: ``passages.py`` is at its 741-LOC hard ceiling and
must not grow.  All batch-specific code lives here.
"""

from __future__ import annotations

import asyncio
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from genereview_link.api.routes.passages import (
    _get_corpus_version,
    get_embedding_provider,
    get_repository,
    search_passages,
)
from genereview_link.models.genereview_models import (
    IdsOnlySearchResponse,
    PassageSearchResponse,
    ResponseMeta,
    SearchBatchRequest,
    SearchBatchResponse,
    SearchBatchResultItem,
)
from genereview_link.retrieval.embeddings import EmbeddingProvider
from genereview_link.retrieval.repository import GeneReviewRepository

router = APIRouter(tags=["Passages"])

SEARCH_BATCH_MAX = 5


def _extract_hits(
    result: PassageSearchResponse | IdsOnlySearchResponse | JSONResponse,
) -> list[Any]:
    """Return the raw hits list from a search_passages return value.

    ``search_passages`` may return one of three types:
    - ``PassageSearchResponse`` — Pydantic model, access ``.results``
    - ``IdsOnlySearchResponse`` — Pydantic model, access ``.results``
    - ``JSONResponse`` — when exclude/include flags cause a JSONResponse;
      body is already serialised, so decode it here.
    """
    if isinstance(result, JSONResponse):
        import json

        # JSONResponse.body may be bytes/bytearray/memoryview; bytes() normalises all.
        raw_body: bytes = bytes(result.body)
        body: dict[str, Any] = json.loads(raw_body)
        return list(body.get("results", []))
    # Both PassageSearchResponse and IdsOnlySearchResponse expose .results
    raw = result.results
    return [r.model_dump(mode="json") for r in raw]


def _annotate_cross_query_hits(
    results_hits: list[list[dict[str, Any]]],
) -> list[list[dict[str, Any]]]:
    """Annotate hits that appear in multiple query results.

    For each passage_id that appears in more than one result:
    - The canonical occurrence (lowest query_index) is left unchanged.
    - Every other occurrence gets ``also_matched_query_indices`` set to the
      list of other indices (excluding its own) where the passage also appeared.

    Hits are never removed — the annotation keeps the payload tight by signalling
    redundancy without discarding evidence.
    """
    # Build a map: passage_id -> sorted list of query indices where it appears
    pid_to_indices: dict[str, list[int]] = {}
    for qi, hits in enumerate(results_hits):
        for hit in hits:
            pid = hit.get("passage_id")
            if pid is None:
                continue
            pid_to_indices.setdefault(pid, []).append(qi)

    # Annotate duplicates: only non-canonical occurrences get annotated.
    # The canonical occurrence is the one with the lowest query_index.
    annotated: list[list[dict[str, Any]]] = []
    for qi, hits in enumerate(results_hits):
        annotated_hits: list[dict[str, Any]] = []
        for hit in hits:
            pid = hit.get("passage_id")
            indices = pid_to_indices.get(pid) if pid is not None else None
            if indices is not None and len(indices) > 1 and qi != indices[0]:
                # Non-canonical: annotate with other indices (excluding self)
                other_indices = [i for i in indices if i != qi]
                hit = {**hit, "also_matched_query_indices": other_indices}
            annotated_hits.append(hit)
        annotated.append(annotated_hits)
    return annotated


@router.post(
    "/passages/search/batch",
    response_model=SearchBatchResponse,
    response_model_by_alias=True,
    operation_id="search_passages_batch",
    summary="Run up to 5 independent passage searches in a single batched call.",
    description=(
        "Accepts 1-5 search specs, each with its own ``q``, ``sections``, "
        "``nbk_id``, ``gene``, ``mode``, ``rerank``, etc. (mirrors the "
        "query parameters of ``GET /passages/search``). Executes all specs "
        "concurrently via ``asyncio.gather`` and returns a flat envelope.\n\n"
        "**When to use:** when a clinical-report workflow issues several "
        "related but independent queries (e.g. one scoped to ``management``, "
        "one to ``genetic_counseling``, one open-section exploratory) — "
        "batching cuts N round-trip latencies to ~1x the slowest query.\n\n"
        "**Response shape:** ``results[i].query_index`` matches the "
        "zero-based position of the spec in the request. "
        "``results[i].hits`` mirrors the shape of ``GET /passages/search`` "
        "results for that spec's ``mode``.\n\n"
        "**Deduplication:** when the same ``passage_id`` appears in hits "
        "for multiple specs, every occurrence except the one from the "
        "lowest ``query_index`` carries ``also_matched_query_indices`` "
        "(a list of the other indices). Hits are never removed — the "
        "annotation signals redundancy without discarding evidence.\n\n"
        "**Cap:** requests with more than 5 specs are rejected with 422.\n\n"
        "**MCP:** registered as the ``search_passages_batch`` MCP tool "
        "via the FastMCP OpenAPI proxy.\n\n"
        "Latency: p95 <= 1.5x the slowest single-query latency for a "
        "3-spec batch (concurrent execution)."
    ),
)
async def search_passages_batch(
    body: SearchBatchRequest,
    request: Request,
    repo: Annotated[GeneReviewRepository, Depends(get_repository)],
    embedder: Annotated[EmbeddingProvider, Depends(get_embedding_provider)],
) -> SearchBatchResponse:
    """Run up to 5 independent passage searches concurrently.

    Calls the existing ``search_passages`` handler directly (bypassing
    FastAPI dependency injection) for each spec, then assembles the results
    into a flat envelope.  Deduplication is applied as described in the
    endpoint docstring above.
    """
    corpus = _get_corpus_version(request)

    async def _run_one(
        spec_index: int,
    ) -> tuple[int, PassageSearchResponse | IdsOnlySearchResponse | JSONResponse]:
        spec = body.specs[spec_index]
        result = await search_passages(
            q=spec.q,
            query=None,
            gene=spec.gene,
            nbk_id=spec.nbk_id,
            sections=spec.sections,
            heading_path_contains=spec.heading_path_contains,
            mode=spec.mode,
            limit=spec.limit,
            exclude=None,
            include=None,
            snippet_chars=spec.snippet_chars,
            rerank=spec.rerank,
            repo=repo,
            embedder=embedder,
            request=request,
        )
        return spec_index, result

    raw_results: list[tuple[int, Any]] = await asyncio.gather(
        *(_run_one(i) for i in range(len(body.specs)))
    )

    # raw_results comes back in completion order — sort by spec index
    raw_results.sort(key=lambda t: t[0])

    # Extract plain-dict hit lists per spec
    hits_per_spec: list[list[dict[str, Any]]] = [_extract_hits(result) for _, result in raw_results]

    # Annotate cross-query duplicates
    annotated_hits = _annotate_cross_query_hits(hits_per_spec)

    results: list[SearchBatchResultItem] = [
        SearchBatchResultItem(
            query_index=i,
            q=body.specs[i].q,
            sections=body.specs[i].sections,
            hits=annotated_hits[i],
        )
        for i in range(len(body.specs))
    ]

    return SearchBatchResponse(  # type: ignore[call-arg]
        results=results,
        meta=ResponseMeta(corpus_version=corpus),
    )
