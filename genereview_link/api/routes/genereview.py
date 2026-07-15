"""Comprehensive GeneReview endpoint.

Provides REST API endpoint for complete GeneReview workflow from gene symbol
to full data.
"""

import re
from typing import Annotated, Protocol

from fastapi import APIRouter, Depends, Path, Query, Request

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
from genereview_link.api.untrusted_limits import collect_untrusted, guard_untrusted_limits
from genereview_link.mcp.untrusted_content import fence_untrusted_text
from genereview_link.models.genereview_models import FencedGeneReviewSection, GeneReview
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
        # Corpus chapters routinely have no pubmed_id (issue #106 D1); emit an
        # empty string rather than the literal "None".
        pubmed_id=chapter.pubmed_id or "",
        book_url=f"https://www.ncbi.nlm.nih.gov/books/{nbk_id}/",
        title=fence_untrusted_text(
            chapter.title, source="genereviews", record_id=f"{nbk_id}#title"
        ),
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

    Subsection content is not counted; only top-level section content
    contributes to the budget.

    Truncation slices the ORIGINAL RAW upstream text (``section._raw_content``,
    stashed by ``fence_section_prose``) and re-fences THAT slice, so
    ``raw_sha256`` hashes the true pre-normalization bytes of the emitted
    (truncated) text — never the already-normalized ``.content.text``.

    Mutates ``result`` in place. Callers MUST pass a fresh instance (e.g. via
    ``model_copy(deep=True)``) when the upstream source caches the object —
    see ``get_genereview`` for the cache-isolation pattern.
    """
    ordered_sections: list[tuple[str, FencedGeneReviewSection | None]] = [
        ("summary", result.summary),
        ("diagnosis", result.diagnosis),
        ("management", result.management),
    ]
    for key in sorted(result.other_sections.keys()):
        ordered_sections.append((key, result.other_sections[key]))

    def _retruncate(section: FencedGeneReviewSection, raw_slice: str) -> None:
        # Fence the RAW upstream slice (record_id preserved) so raw_sha256 is
        # over the raw pre-normalization bytes of the emitted text.
        section.content = fence_untrusted_text(
            raw_slice,
            source=section.content.provenance.source,
            record_id=section.content.provenance.record_id,
        )
        section._raw_content = raw_slice

    total = 0
    truncated = False
    for _key, section in ordered_sections:
        if section is None:
            continue
        raw = section._raw_content
        if total >= max_chars:
            if raw:
                _retruncate(section, "")
                truncated = True
            continue
        remaining = max_chars - total
        if len(raw) > remaining:
            _retruncate(section, raw[:remaining])
            total += remaining
            truncated = True
        else:
            total += len(raw)
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
        "get_chapter_section. Resolves the gene to its DEFINING corpus chapter (the "
        "chapter that gene's GeneReview is about); a gene only mentioned in a "
        "multi-gene chapter, or absent from the corpus, returns not_found (use "
        "search_passages(gene=<symbol>) for mention-level evidence). Pass fresh=true "
        "to re-fetch the resolved chapter's content live from NCBI (resolution stays "
        "corpus-authoritative). Corpus-backed responses carry _meta.corpus_version; "
        "fresh responses stamp live provenance."
    ),
    operation_id="get_genereview_summary",
)
async def get_genereview(
    request: Request,
    gene_symbol: Annotated[
        str,
        Path(
            pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$",
            description="HGNC gene symbol to resolve, e.g. 'CFTR'.",
            examples=["CFTR"],
        ),
    ],
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
        # Resolution is ALWAYS corpus-authoritative — even with fresh=true. The
        # corpus is the source of truth for gene -> chapter; the blind, unranked
        # live E-utils list is not (issue #106 D1). fresh controls only whether the
        # resolved chapter's CONTENT is re-fetched live vs served from the warm
        # cache. A gene that resolves to no DEFINING chapter (mentioned only in a
        # multi-gene chapter, or absent) is not_found — never a guessed chapter.
        repository = get_optional_repository(request)
        chapter = None
        if repository is not None:
            chapter = await repository.get_defining_chapter_by_gene(gene_symbol.upper())
        if chapter is None:
            raise gene_not_found_error(gene_symbol)

        if fresh:
            result = await service.get_genereview_comprehensive_uncached(
                gene_symbol,
                include_abstract=include_abstract,
                include_links=include_links,
                include_fulltext=include_fulltext,
                chapter=chapter,
            )
        else:
            try:
                cached_result = await service.get_genereview_comprehensive_indexed(
                    gene_symbol,
                    include_abstract=include_abstract,
                    include_links=include_links,
                    include_fulltext=include_fulltext,
                    chapter=chapter,
                )
                # get_genereview_comprehensive_indexed is alru_cache-backed and
                # returns the same instance on cache hits; copy before the route
                # mutates result.meta (stamp_response_version) and section bodies
                # (_truncate_genereview_fulltext) to avoid cross-request leakage.
                result = cached_result.model_copy(deep=True)
            except DataNotFoundError:
                result = _minimal_gene_review_from_indexed_chapter(gene_symbol, chapter)
        stamp_response_version(
            result,
            corpus_version=(live_corpus_version() if fresh else active_corpus_version(request)),
        )
        if include_fulltext and max_chars > 0:
            _truncate_genereview_fulltext(result, max_chars)
        # Aggregate every fenced object (sections + abstract + fulltext metadata)
        # into one response-wide limit check after truncation.
        guard_untrusted_limits(collect_untrusted(result))
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
