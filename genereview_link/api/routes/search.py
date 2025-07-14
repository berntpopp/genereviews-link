"""
Search endpoint for finding GeneReviews by gene symbol.
Provides REST API endpoint for searching NCBI database.
"""

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from genereview_link.models.genereview_models import SearchResult
from genereview_link.api.eutils_client import EutilsClient
from genereview_link.api.client_manager import get_managed_client
from genereview_link.logging_config import get_logger, PerformanceLogger

router = APIRouter(prefix="/search", tags=["Search"])
logger = get_logger(__name__)


@router.get(
    "/{gene_symbol}",
    response_model=SearchResult,
    summary="Search for GeneReviews by gene symbol",
    operation_id="search_genereviews",
)
async def search_genereviews(
    request: Request,
    gene_symbol: str,
    retmax: int = Query(
        20, description="Maximum number of results to return", ge=1, le=100
    ),
    client: EutilsClient = Depends(get_managed_client),
) -> SearchResult:
    """
    Search for GeneReviews associated with the given gene symbol using
    NCBI E-utils esearch.

    Returns a list of PubMed IDs along with search metadata.
    """
    # Create request-scoped logger
    request_logger = logger.bind(
        correlation_id=getattr(request.state, "correlation_id", "unknown"),
        gene_symbol=gene_symbol,
        retmax=retmax,
    )

    request_logger.info("Starting GeneReview search")

    with PerformanceLogger(request_logger, "genereview_search") as perf:
        try:
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

            return SearchResult(**result)

        except Exception as e:
            request_logger.error(
                "Search failed",
                error_type=type(e).__name__,
                error_message=str(e),
                exc_info=True,
            )
            raise HTTPException(
                status_code=500,
                detail="An error occurred while searching for GeneReviews.",
            )
