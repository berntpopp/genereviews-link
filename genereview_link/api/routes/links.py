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
    live_corpus_version,
    stamp_response_version,
)
from genereview_link.api.orchestration_errors import upstream_ncbi_unavailable_error
from genereview_link.models.genereview_models import LinkData

router = APIRouter(prefix="/links", tags=["Links"])


@router.get(
    "/{pmid}",
    response_model=LinkData,
    summary="Get normalized categorized links for a PubMed ID",
    description=(
        "Live NCBI E-utils link wrapper that always calls live NCBI and returns "
        "categorized/normalized links. Adds structured error envelopes and "
        "corpus-version stamping over a raw elink call. Default responses may "
        "carry active _meta.corpus_version context; fresh=true labels the "
        "response version as live:<timestamp>."
    ),
    operation_id="get_links",
)
async def get_links(
    request: Request,
    pmid: str,
    client: Annotated[EutilsClient, Depends(get_managed_client)],
    fresh: bool = Query(
        False,
        description=(
            "Retained for backward compatibility; no longer affects versioning. "
            "Retrieval is ALWAYS a live NCBI E-utils call, so the response version "
            "is always live:<timestamp>."
        ),
    ),
) -> LinkData:
    """
    Get all available links from a PubMed ID using NCBI E-utils elink.

    Returns categorized links including NCBI Bookshelf, PMC full text,
    and external links.

    Pass ``?fresh=true`` to label the response version as live.
    """
    try:
        payload = await client.get_all_links(pmid)
        out = LinkData(**payload)
        stamp_response_version(
            out,
            # get_links ALWAYS calls live NCBI E-utils, so the version reflects live
            # provenance -- not the local corpus version, and never null.
            corpus_version=live_corpus_version(),
        )
        return out
    except StructuredHTTPException:
        raise
    except Exception as e:
        logging.error(f"Error fetching links for PMID {pmid}: {e}", exc_info=True)
        raise upstream_ncbi_unavailable_error("fetch links") from e
