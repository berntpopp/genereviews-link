"""Abstract endpoint for fetching PubMed article abstracts.

Provides REST API endpoint for retrieving abstract and metadata
for PubMed articles by ID.
"""

import logging
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from genereview_link.api.client_manager import get_managed_client
from genereview_link.api.eutils_client import EutilsClient
from genereview_link.models.genereview_models import AbstractData

router = APIRouter(prefix="/abstract", tags=["Abstract"])


@router.get(
    "/{pubmed_id}",
    response_model=AbstractData,
    summary="Get abstract and metadata for a PubMed ID",
    operation_id="get_abstract",
)
async def get_abstract(
    pubmed_id: str,
    client: Annotated[EutilsClient, Depends(get_managed_client)],
    fresh: bool = Query(False, description="Bypass index; fetch live from NCBI"),
) -> AbstractData:
    """
    Fetch abstract and metadata from PubMed using NCBI E-utils efetch.

    Returns detailed information including title, abstract, authors, journal,
    and publication date.

    Pass ``?fresh=true`` to bypass the index and fetch live from NCBI.
    """
    # TODO: repository-first path (Phase 5.3+); for now passes through to EutilsClient
    # until repository is populated.
    try:
        result = await client.fetch_abstract(pubmed_id)
        if not result:
            raise HTTPException(
                status_code=404,
                detail=f"Abstract not found for PubMed ID: {pubmed_id}",
            )

        # Ensure all required fields have default values
        out = AbstractData(
            pmid=result.get("pmid", pubmed_id),
            title=result.get("title", ""),
            abstract=result.get("abstract", ""),
            authors=result.get("authors", []),
            journal=result.get("journal", ""),
            publication_date=result.get("publication_date", ""),
        )
        if fresh:
            out.corpus_version = f"live:{datetime.now(UTC).isoformat()}"
        return out
    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Error fetching abstract for PMID {pubmed_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="An error occurred while fetching the abstract.",
        ) from e
