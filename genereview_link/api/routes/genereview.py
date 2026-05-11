"""Comprehensive GeneReview endpoint.

Provides REST API endpoint for complete GeneReview workflow from gene symbol
to full data.
"""

import logging
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from genereview_link.models.genereview_models import GeneReview, LicenseNotice
from genereview_link.services.genereview_service import (
    DataNotFoundError,
    GeneReviewService,
)
from genereview_link.services.service_manager import get_managed_service

router = APIRouter(prefix="/genereview", tags=["GeneReviews"])


@router.get(
    "/{gene_symbol}",
    response_model=GeneReview,
    summary="Get comprehensive GeneReview data",
    operation_id="get_genereview_summary",
)
async def get_genereview(
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
    # TODO: repository-first path (Phase 5.3+); for now passes through to EutilsClient
    # until repository is populated.
    try:
        result = await service.get_genereview_comprehensive(
            gene_symbol,
            include_abstract=include_abstract,
            include_links=include_links,
            include_fulltext=include_fulltext,
        )
        result.license = LicenseNotice()
        if fresh:
            result.corpus_version = f"live:{datetime.now(UTC).isoformat()}"
        return result
    except DataNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except Exception as e:
        logging.error(f"Error fetching GeneReview for {gene_symbol}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="An internal server error occurred.") from e
