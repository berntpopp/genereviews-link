import logging
from fastapi import APIRouter, Depends, HTTPException, Query

from genereview_link.models.genereview_models import SearchResult
from genereview_link.api.eutils_client import EutilsClient

router = APIRouter(prefix="/search", tags=["Search"])

async def get_client() -> EutilsClient:
    """Dependency to get EutilsClient instance."""
    client = EutilsClient()
    try:
        yield client
    finally:
        await client.close()

@router.get(
    "/{gene_symbol}",
    response_model=SearchResult,
    summary="Search for GeneReviews by gene symbol",
    operation_id="search_genereviews"
)
async def search_genereviews(
    gene_symbol: str,
    retmax: int = Query(20, description="Maximum number of results to return", ge=1, le=100),
    client: EutilsClient = Depends(get_client),
) -> SearchResult:
    """
    Search for GeneReviews associated with the given gene symbol using NCBI E-utils esearch.
    
    Returns a list of PubMed IDs along with search metadata.
    """
    try:
        result = await client.search_genereviews(gene_symbol, retmax=retmax)
        return SearchResult(**result)
    except Exception as e:
        logging.error(f"Error searching for gene {gene_symbol}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="An error occurred while searching for GeneReviews.")