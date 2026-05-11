"""Links endpoint for fetching related URLs.

Provides REST API endpoint for retrieving all available links for PubMed articles.
"""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from genereview_link.api.client_manager import get_managed_client
from genereview_link.api.eutils_client import EutilsClient
from genereview_link.models.genereview_models import LinkData

router = APIRouter(prefix="/links", tags=["Links"])


@router.get(
    "/{pubmed_id}",
    response_model=LinkData,
    summary="Get all available links for a PubMed ID",
    operation_id="get_links",
)
async def get_links(
    pubmed_id: str,
    client: Annotated[EutilsClient, Depends(get_managed_client)],
) -> LinkData:
    """
    Get all available links from a PubMed ID using NCBI E-utils elink.

    Returns categorized links including NCBI Bookshelf, PMC full text,
    and external links.
    """
    try:
        result = await client.get_all_links(pubmed_id)
        return LinkData(**result)
    except Exception as e:
        logging.error(f"Error fetching links for PMID {pubmed_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=500, detail="An error occurred while fetching links."
        ) from e
