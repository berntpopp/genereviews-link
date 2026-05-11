"""Links endpoint for fetching related URLs.

Provides REST API endpoint for retrieving all available links for PubMed articles.
"""

import logging
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from genereview_link.api.client_manager import get_managed_client
from genereview_link.api.eutils_client import EutilsClient
from genereview_link.models.genereview_models import LicenseNotice, LinkData

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
    fresh: bool = Query(False, description="Bypass index; fetch live from NCBI"),
) -> LinkData:
    """
    Get all available links from a PubMed ID using NCBI E-utils elink.

    Returns categorized links including NCBI Bookshelf, PMC full text,
    and external links.

    Pass ``?fresh=true`` to bypass the index and fetch live from NCBI.
    """
    # TODO: repository-first path (Phase 5.3+); for now passes through to EutilsClient
    # until repository is populated.
    try:
        result = await client.get_all_links(pubmed_id)
        out = LinkData(**result)
        out.license = LicenseNotice()
        if fresh:
            out.corpus_version = f"live:{datetime.now(UTC).isoformat()}"
        return out
    except Exception as e:
        logging.error(f"Error fetching links for PMID {pubmed_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=500, detail="An error occurred while fetching links."
        ) from e
