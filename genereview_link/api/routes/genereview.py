"""Comprehensive GeneReview endpoint.

Provides REST API endpoint for complete GeneReview workflow from gene symbol
to full data.
"""

from typing import Annotated

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
from genereview_link.models.genereview_models import GeneReview
from genereview_link.services.genereview_service import (
    DataNotFoundError,
    GeneReviewService,
)
from genereview_link.services.service_manager import get_managed_service

router = APIRouter(prefix="/genereview", tags=["GeneReviews"])


@router.get(
    "/{gene_symbol}",
    response_model=GeneReview,
    summary="Resolve a gene into a convenience GeneReview summary",
    description=(
        "Convenience orchestration tool that resolves gene -> PubMed -> NBK, "
        "uses local corpus NBK resolution when available, and falls back through "
        "live NCBI services. If resolution fails, use search_passages(gene=<symbol>) "
        "to retrieve indexed chapter evidence directly. Pass fresh=true to bypass "
        "indexed context and fetch live data. Corpus-backed responses carry "
        "_meta.corpus_version; live or unresolved responses may use live version "
        "stamping or omit corpus_version when no corpus chapter resolved."
    ),
    operation_id="get_genereview_summary",
)
async def get_genereview(
    request: Request,
    gene_symbol: str,
    service: Annotated[GeneReviewService, Depends(get_managed_service)],
    include_abstract: bool = Query(True, description="Include PubMed abstract and metadata"),
    include_links: bool = Query(True, description="Include all available links"),
    include_fulltext: bool = Query(True, description="Include comprehensive scraped content"),
    fresh: bool = Query(False, description="Bypass index; fetch live from NCBI"),
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

        result = await service.get_genereview_comprehensive_uncached(
            gene_symbol,
            include_abstract=include_abstract,
            include_links=include_links,
            include_fulltext=include_fulltext,
            chapter=indexed_chapter,
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
