"""Abstract endpoint for fetching PubMed article abstracts.

Provides REST API endpoint for retrieving abstract and metadata
for PubMed articles by ID.
"""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request

from genereview_link.api.client_manager import get_managed_client
from genereview_link.api.errors import StructuredHTTPException
from genereview_link.api.eutils_client import EutilsClient
from genereview_link.api.orchestration import (
    active_corpus_version,
    live_corpus_version,
    stamp_response_version,
)
from genereview_link.api.orchestration_errors import (
    abstract_not_found_error,
    upstream_ncbi_unavailable_error,
)
from genereview_link.models.genereview_models import AbstractData

router = APIRouter(prefix="/abstract", tags=["Abstract"])


@router.get(
    "/{pubmed_id}",
    response_model=AbstractData,
    summary="Get normalized abstract and metadata for a PubMed ID",
    description=(
        "Live NCBI E-utils abstract wrapper that always calls live NCBI and "
        "returns a normalized response with structured errors. Default responses "
        "may carry active _meta.corpus_version context; fresh=true labels the "
        "response version as live:<timestamp>."
    ),
    operation_id="get_abstract",
)
async def get_abstract(
    request: Request,
    pubmed_id: str,
    client: Annotated[EutilsClient, Depends(get_managed_client)],
    fresh: bool = Query(
        False,
        description="Label response version as live:<timestamp>; retrieval already uses live NCBI",
    ),
) -> AbstractData:
    """
    Fetch abstract and metadata from PubMed using NCBI E-utils efetch.

    Returns detailed information including title, abstract, authors, journal,
    and publication date.

    Pass ``?fresh=true`` to label the response version as live.
    """
    # TODO: repository-first path (Phase 5.3+); for now passes through to EutilsClient
    # until repository is populated.
    try:
        result = await client.fetch_abstract(pubmed_id)
        if not result:
            raise abstract_not_found_error(pubmed_id)

        # Ensure all required fields have default values
        out = AbstractData(
            pmid=result.get("pmid", pubmed_id),
            title=result.get("title", ""),
            abstract=result.get("abstract", ""),
            authors=result.get("authors", []),
            journal=result.get("journal", ""),
            publication_date=result.get("publication_date", ""),
        )
        stamp_response_version(
            out,
            corpus_version=live_corpus_version() if fresh else active_corpus_version(request),
        )
        return out
    except StructuredHTTPException:
        raise
    except Exception as e:
        logging.error(f"Error fetching abstract for PMID {pubmed_id}: {e}", exc_info=True)
        raise upstream_ncbi_unavailable_error("fetch abstract") from e
