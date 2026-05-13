"""Links endpoint for fetching related URLs.

Provides REST API endpoint for retrieving all available links for PubMed articles.
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
from genereview_link.api.orchestration_errors import upstream_ncbi_unavailable_error
from genereview_link.models.genereview_models import LinkData

router = APIRouter(prefix="/links", tags=["Links"])


@router.get(
    "/{pubmed_id}",
    response_model=LinkData,
    summary="Get normalized categorized links for a PubMed ID",
    description=(
        "Value-add wrapper over raw NCBI E-utils link retrieval. Returns "
        "categorized/normalized links with structured errors and corpus-version "
        "stamping via _meta.corpus_version when corpus context is active. Pass "
        "fresh=true to retrieve live NCBI links."
    ),
    operation_id="get_links",
)
async def get_links(
    request: Request,
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
        payload = await client.get_all_links(pubmed_id)
        out = LinkData(**payload)
        stamp_response_version(
            out,
            corpus_version=live_corpus_version() if fresh else active_corpus_version(request),
        )
        return out
    except StructuredHTTPException:
        raise
    except Exception as e:
        logging.error(f"Error fetching links for PMID {pubmed_id}: {e}", exc_info=True)
        raise upstream_ncbi_unavailable_error("fetch links") from e
