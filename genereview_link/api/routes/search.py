"""Search endpoint for finding GeneReviews by gene symbol.

Provides REST API endpoint for searching NCBI database.
"""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from genereview_link.api.client_manager import get_managed_client
from genereview_link.api.eutils_client import EutilsClient
from genereview_link.logging_config import PerformanceLogger, get_logger
from genereview_link.models.genereview_models import SearchResult

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
    client: Annotated[EutilsClient, Depends(get_managed_client)],
    retmax: int = Query(20, description="Maximum number of results to return", ge=1, le=100),
    fresh: bool = Query(False, description="Bypass index; fetch live from NCBI"),
) -> SearchResult:
    """Search for GeneReviews associated with the given gene symbol.

    Uses NCBI E-utils esearch to find relevant GeneReviews.
    Returns a list of PubMed IDs along with search metadata.

    Pass ``?fresh=true`` to bypass the index and fetch live from NCBI.
    """
    # TODO: repository-first path (Phase 5.3+); for now passes through to EutilsClient
    # until repository is populated.
    # Create request-scoped logger. correlation_id is injected automatically by
    # the structlog processor wired in logging_config.py (Task B3).
    request_logger = logger.bind(
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

            out = SearchResult(**result)
            if fresh:
                out.corpus_version = f"live:{datetime.now(UTC).isoformat()}"
            return out

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
            ) from e
