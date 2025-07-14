import logging
from fastapi import APIRouter, Depends, HTTPException

from genereview_link.models.genereview_models import AbstractData
from genereview_link.api.eutils_client import EutilsClient
from genereview_link.api.client_manager import get_managed_client

router = APIRouter(prefix="/abstract", tags=["Abstract"])

@router.get(
    "/{pubmed_id}",
    response_model=AbstractData,
    summary="Get abstract and metadata for a PubMed ID",
    operation_id="get_abstract"
)
async def get_abstract(
    pubmed_id: str,
    client: EutilsClient = Depends(get_managed_client),
) -> AbstractData:
    """
    Fetch abstract and metadata from PubMed using NCBI E-utils efetch.
    
    Returns detailed information including title, abstract, authors, journal, and publication date.
    """
    try:
        result = await client.fetch_abstract(pubmed_id)
        if not result:
            raise HTTPException(status_code=404, detail=f"Abstract not found for PubMed ID: {pubmed_id}")
        
        # Ensure all required fields have default values
        return AbstractData(
            pmid=result.get("pmid", pubmed_id),
            title=result.get("title", ""),
            abstract=result.get("abstract", ""),
            authors=result.get("authors", []),
            journal=result.get("journal", ""),
            publication_date=result.get("publication_date", "")
        )
    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Error fetching abstract for PMID {pubmed_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="An error occurred while fetching the abstract.")