"""Links endpoint for fetching related URLs.

Provides REST API endpoint for retrieving all available links for PubMed articles.
"""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Request

from genereview_link.api.client_manager import get_managed_client
from genereview_link.api.errors import StructuredHTTPException
from genereview_link.api.eutils_client import EutilsClient
from genereview_link.api.orchestration import (
    live_corpus_version,
    stamp_response_version,
)
from genereview_link.api.orchestration_errors import upstream_ncbi_unavailable_error
from genereview_link.api.untrusted_limits import collect_untrusted, guard_untrusted_limits
from genereview_link.mcp.untrusted_content import fence_untrusted_text
from genereview_link.models.genereview_models import LinkData, LinkEntry

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
    pmid: Annotated[
        str,
        Path(
            description="PubMed ID (numeric), e.g. '20301425'.",
            examples=["20301425"],
        ),
    ],
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
        payload = dict(await client.get_all_links(pmid))
        # v1.1: link_entries[*].provider is upstream NCBI Provider/Name (or
        # Category) prose — fence it before it leaves the MCP boundary.
        raw_entries = payload.pop("link_entries", None)
        fenced_entries: list[LinkEntry] | None = None
        if raw_entries:
            fenced_entries = [
                LinkEntry(
                    url=entry["url"],
                    link_type=entry["link_type"],
                    provider=(
                        fence_untrusted_text(
                            entry["provider"],
                            source="genereviews",
                            record_id=f"{pmid}#link:{i}",
                        )
                        if entry.get("provider")
                        else None
                    ),
                )
                for i, entry in enumerate(raw_entries)
            ]
        out = LinkData(link_entries=fenced_entries, **payload)
        guard_untrusted_limits(collect_untrusted(out))
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
        logging.error("Error fetching links for PMID %s (%s)", pmid, type(e).__name__)
        raise upstream_ncbi_unavailable_error("fetch links") from e
