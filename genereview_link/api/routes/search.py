"""Search endpoint for finding GeneReviews by gene symbol.

Provides REST API endpoint for searching NCBI database.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request

from genereview_link.api.client_manager import get_managed_client
from genereview_link.api.errors import StructuredHTTPException
from genereview_link.api.eutils_client import EutilsClient
from genereview_link.api.orchestration import (
    active_corpus_version,
    get_optional_repository,
    live_corpus_version,
    stamp_response_version,
)
from genereview_link.api.orchestration_errors import internal_orchestration_error
from genereview_link.logging_config import PerformanceLogger, get_logger
from genereview_link.models.genereview_models import SearchResult

router = APIRouter(prefix="/search", tags=["Search"])
logger = get_logger(__name__)


@router.get(
    "/{gene_symbol}",
    response_model=SearchResult,
    summary="Search GeneReviews by gene symbol with corpus-first fallback behavior",
    description=(
        "Search GeneReviews by gene symbol using the indexed corpus first when "
        "available, then fallback to live NCBI E-utils. If resolver links are "
        "unavailable or no PubMed ID is found, use search_passages(gene=<symbol>) "
        "for indexed chapter evidence. Pass fresh=true to bypass the corpus and "
        "query live NCBI."
    ),
    operation_id="search_genereviews",
)
async def search_genereviews(
    request: Request,
    gene_symbol: str,
    client: Annotated[EutilsClient, Depends(get_managed_client)],
    retmax: int = Query(20, description="Maximum number of results to return", ge=1, le=100),
    fresh: bool = Query(False, description="Bypass index; fetch live from NCBI"),
) -> SearchResult:
    """Search for GeneReviews associated with the given gene symbol.

    Uses NCBI E-utils esearch to find relevant GeneReviews.
    Returns a list of PubMed IDs along with search metadata.

    Pass ``?fresh=true`` to bypass the index and fetch live from NCBI.
    """
    # Create request-scoped logger. correlation_id is injected automatically by
    # the structlog processor wired in logging_config.py (Task B3).
    request_logger = logger.bind(
        gene_symbol=gene_symbol,
        retmax=retmax,
    )

    request_logger.info("Starting GeneReview search")

    with PerformanceLogger(request_logger, "genereview_search") as perf:
        try:
            if not fresh:
                repo = get_optional_repository(request)
                if repo is not None:
                    chapter = await repo.get_chapter_by_gene(gene_symbol.upper())
                    if chapter is not None and chapter.pubmed_id:
                        out = SearchResult(
                            count=1,
                            retmax=retmax,
                            retstart=0,
                            ids=[chapter.pubmed_id],
                            webenv="",
                            querykey="",
                        )
                        stamp_response_version(
                            out,
                            corpus_version=active_corpus_version(request),
                        )
                        perf.add_context(result_count=1, ids_found=1)
                        request_logger.info(
                            "Search completed from repository",
                            result_count=1,
                            ids_found=1,
                        )
                        return out

            result = await client.search_genereviews(gene_symbol, retmax=retmax)

            # Log search results
            result_count = result.get("count", 0)
            ids_found = len(result.get("ids", []))

            perf.add_context(result_count=result_count, ids_found=ids_found)
            request_logger.info(
                "Search completed successfully",
                result_count=result_count,
                ids_found=ids_found,
            )

            out = SearchResult(**result)
            stamp_response_version(
                out,
                corpus_version=live_corpus_version() if fresh else active_corpus_version(request),
            )
            return out

        except StructuredHTTPException:
            raise
        except Exception as e:
            request_logger.error(
                "Search failed",
                error_type=type(e).__name__,
                error_message=str(e),
                exc_info=True,
            )
            raise internal_orchestration_error(
                "search GeneReviews",
                gene_symbol=gene_symbol,
            ) from e
