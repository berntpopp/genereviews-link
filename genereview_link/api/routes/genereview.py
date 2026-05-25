"""Comprehensive GeneReview endpoint.

Provides REST API endpoint for complete GeneReview workflow from gene symbol
to full data.
"""

import re
from typing import Annotated, Protocol

from fastapi import APIRouter, Depends, Query, Request

from genereview_link.api.errors import StructuredHTTPException
from genereview_link.api.orchestration import (
    active_corpus_version,
    get_optional_repository,
    live_corpus_version,
    stamp_response_version,
)
from genereview_link.api.orchestration_errors import (
    gene_not_found_error,
    internal_orchestration_error,
)
from genereview_link.models.genereview_models import GeneReview, GeneReviewSection
from genereview_link.services.genereview_service import (
    DataNotFoundError,
    GeneReviewService,
)
from genereview_link.services.service_manager import get_managed_service

router = APIRouter(prefix="/genereview", tags=["GeneReviews"])

_NBK_ID_PATTERN = re.compile(r"/books/(NBK\d+)")


class _IndexedChapter(Protocol):
    @property
    def nbk_id(self) -> str: ...

    @property
    def pubmed_id(self) -> str | None: ...

    @property
    def title(self) -> str: ...


def _minimal_gene_review_from_indexed_chapter(
    gene_symbol: str, chapter: _IndexedChapter
) -> GeneReview:
    nbk_id = chapter.nbk_id
    return GeneReview(
        gene_symbol=gene_symbol.upper(),
        pubmed_id=str(chapter.pubmed_id),
        book_url=f"https://www.ncbi.nlm.nih.gov/books/{nbk_id}/",
        title=chapter.title,
    )


def _truncate_genereview_fulltext(result: GeneReview, max_chars: int) -> None:
    """Cap fulltext payload size to ``max_chars`` characters.

    Walks the top-level GeneReview sections in deterministic order
    (summary, diagnosis, management, then ``other_sections`` alphabetically),
    keeps content until the budget is exhausted, and clears the remainder.
    Stamps ``result.meta.truncated`` and ``result.meta.next_commands`` with a
    ``get_chapter_section`` hint when truncation fires. The hint carries the
    NBK ID extracted from the ``/books/<NBK...>`` path segment of
    ``result.book_url`` only — no hardcoded section, per the Risk Notes in
    the Group B spec. If the URL does not match the canonical Bookshelf path
    the next_commands hint is omitted (truncated is still set) rather than
    emitting a get_chapter_section call without ``nbk_id``.

    Subsection content is not counted; only top-level ``section.content``
    strings contribute to the budget.

    Mutates ``result`` in place. Callers MUST pass a fresh instance (e.g. via
    ``model_copy(deep=True)``) when the upstream source caches the object —
    see ``get_genereview`` for the cache-isolation pattern.
    """
    ordered_sections: list[tuple[str, GeneReviewSection | None]] = [
        ("summary", result.summary),
        ("diagnosis", result.diagnosis),
        ("management", result.management),
    ]
    for key in sorted(result.other_sections.keys()):
        ordered_sections.append((key, result.other_sections[key]))
    total = 0
    truncated = False
    for _key, section in ordered_sections:
        if section is None:
            continue
        content = section.content or ""
        if total >= max_chars:
            if content:
                section.content = ""
                truncated = True
            continue
        remaining = max_chars - total
        if len(content) > remaining:
            section.content = content[:remaining]
            total += remaining
            truncated = True
        else:
            total += len(content)
    if not truncated:
        return
    result.meta.truncated = True
    match = _NBK_ID_PATTERN.search(result.book_url)
    if not match:
        return
    result.meta.next_commands = [
        {"tool": "get_chapter_section", "arguments": {"nbk_id": match.group(1)}}
    ]


@router.get(
    "/{gene_symbol}",
    response_model=GeneReview,
    summary="Resolve a gene into a convenience GeneReview summary",
    description=(
        "Convenience orchestration tool. Default response is lean: include_fulltext "
        "defaults to False; opt in for full chapter prose. max_chars (default 16000) "
        "truncates fulltext to keep responses context-budget friendly; truncated "
        "responses set _meta.truncated=true and surface next_commands -> "
        "get_chapter_section. Resolves gene -> PubMed -> NBK using the local corpus "
        "when available, and falls back through live NCBI services. If resolution "
        "fails, use search_passages(gene=<symbol>) to retrieve indexed chapter "
        "evidence directly. Pass fresh=true to bypass indexed context and fetch live "
        "data. Corpus-backed responses carry _meta.corpus_version; live or "
        "unresolved responses may use live version stamping or omit corpus_version "
        "when no corpus chapter resolved."
    ),
    operation_id="get_genereview_summary",
)
async def get_genereview(
    request: Request,
    gene_symbol: str,
    service: Annotated[GeneReviewService, Depends(get_managed_service)],
    include_abstract: bool = Query(True, description="Include PubMed abstract and metadata"),
    include_links: bool = Query(True, description="Include all available links"),
    include_fulltext: bool = Query(
        False,
        description=(
            "Default False: response is lean. Opt in for chapter prose. "
            "Truncation is governed by max_chars."
        ),
    ),
    fresh: bool = Query(False, description="Bypass index; fetch live from NCBI"),
    max_chars: int = Query(
        16000,
        ge=0,
        le=200000,
        description=(
            "Cap fulltext payload size in characters when include_fulltext=true. "
            "Pass 0 to disable the cap. Truncated responses set _meta.truncated=true "
            "and surface next_commands -> get_chapter_section."
        ),
    ),
) -> GeneReview:
    """Get complete workflow for GeneReview by gene symbol.

    Searches for a GeneReview by gene symbol, fetches abstract,
    gets all links, scrapes full text, and returns comprehensive structured data.

    This endpoint combines all the individual endpoints into a single
    comprehensive result.
    You can control which additional data to include using the query parameters.

    Pass ``?fresh=true`` to bypass the index and fetch live from NCBI.
    """
    try:
        indexed_chapter = None
        if not fresh:
            repository = get_optional_repository(request)
            if repository is not None:
                chapter = await repository.get_chapter_by_gene(gene_symbol.upper())
                if chapter is not None and chapter.pubmed_id:
                    indexed_chapter = chapter

        if indexed_chapter is not None:
            try:
                cached_result = await service.get_genereview_comprehensive_indexed(
                    gene_symbol,
                    include_abstract=include_abstract,
                    include_links=include_links,
                    include_fulltext=include_fulltext,
                    chapter=indexed_chapter,
                )
                # get_genereview_comprehensive_indexed is alru_cache-backed and
                # returns the same instance on cache hits; copy before the route
                # mutates result.meta (stamp_response_version) and section bodies
                # (_truncate_genereview_fulltext) to avoid cross-request leakage.
                result = cached_result.model_copy(deep=True)
            except DataNotFoundError:
                result = _minimal_gene_review_from_indexed_chapter(gene_symbol, indexed_chapter)
        else:
            result = await service.get_genereview_comprehensive_uncached(
                gene_symbol,
                include_abstract=include_abstract,
                include_links=include_links,
                include_fulltext=include_fulltext,
            )
        stamp_response_version(
            result,
            corpus_version=(
                live_corpus_version()
                if fresh
                else active_corpus_version(request)
                if indexed_chapter is not None
                else None
            ),
        )
        if include_fulltext and max_chars > 0:
            _truncate_genereview_fulltext(result, max_chars)
        return result
    except StructuredHTTPException:
        raise
    except DataNotFoundError as e:
        raise gene_not_found_error(gene_symbol) from e
    except Exception as e:
        raise internal_orchestration_error(
            "fetch GeneReview summary",
            gene_symbol=gene_symbol,
        ) from e
