import logging
from fastapi import APIRouter, Depends, HTTPException, Query

from genereview_link.models.genereview_models import GeneReview
from genereview_link.services.genereview_service import GeneReviewService, DataNotFoundError

router = APIRouter(prefix="/genereview", tags=["GeneReviews"])

async def get_service() -> GeneReviewService:
    """Dependency to get GeneReviewService instance."""
    service = GeneReviewService()
    try:
        yield service
    finally:
        await service.close()

@router.get(
    "/{gene_symbol}",
    response_model=GeneReview,
    summary="Get comprehensive GeneReview data",
    operation_id="get_genereview_summary"
)
async def get_genereview(
    gene_symbol: str,
    include_abstract: bool = Query(True, description="Include PubMed abstract and metadata"),
    include_links: bool = Query(True, description="Include all available links"),
    include_fulltext: bool = Query(True, description="Include comprehensive scraped content"),
    service: GeneReviewService = Depends(get_service),
) -> GeneReview:
    """
    Complete workflow: Searches for a GeneReview by gene symbol, fetches abstract,
    gets all links, scrapes full text, and returns comprehensive structured data.
    
    This endpoint combines all the individual endpoints into a single comprehensive result.
    You can control which additional data to include using the query parameters.
    """
    try:
        return await service.get_genereview_comprehensive(
            gene_symbol,
            include_abstract=include_abstract,
            include_links=include_links,
            include_fulltext=include_fulltext
        )
    except DataNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logging.error(f"Error fetching GeneReview for {gene_symbol}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="An internal server error occurred.")