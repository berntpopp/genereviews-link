"""Abstract endpoint for fetching PubMed article abstracts.

Provides REST API endpoint for retrieving abstract and metadata
for PubMed articles by ID.
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
from genereview_link.api.orchestration_errors import (
    abstract_not_found_error,
    invalid_pubmed_id_error,
    upstream_ncbi_unavailable_error,
)
from genereview_link.api.untrusted_limits import collect_untrusted, guard_untrusted_limits
from genereview_link.mcp.untrusted_content import UntrustedText, fence_untrusted_text
from genereview_link.models.genereview_models import AbstractData

router = APIRouter(prefix="/abstract", tags=["Abstract"])


@router.get(
    "/{pmid}",
    response_model=AbstractData,
    summary="Get normalized abstract and metadata for a PubMed ID",
    description=(
        "Live NCBI E-utils abstract wrapper that always calls live NCBI. Adds "
        "normalized response shape, structured error envelopes, and "
        "corpus-version stamping over a raw efetch call (structured errors and "
        "version metadata are part of the value-add). Default responses may "
        "carry active _meta.corpus_version context; fresh=true labels the "
        "response version as live:<timestamp>."
    ),
    operation_id="get_abstract",
)
async def get_abstract(
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
            "Retrieval is ALWAYS a live PubMed E-utils fetch, so the response "
            "version is always live:<timestamp>."
        ),
    ),
) -> AbstractData:
    """
    Fetch abstract and metadata from PubMed using NCBI E-utils efetch.

    Returns detailed information including title, abstract, authors, journal,
    and publication date.

    Pass ``?fresh=true`` to label the response version as live.
    """
    try:
        if not pmid.isdigit():
            raise invalid_pubmed_id_error(pmid)

        result = await client.fetch_abstract(pmid)
        if not result:
            raise abstract_not_found_error(pmid)

        # v1.1: fence the upstream PubMed prose (abstract + title + journal + authors).
        rid = result.get("pmid", pmid)

        def _f(value: object, field: str) -> UntrustedText:
            return fence_untrusted_text(
                str(value or ""), source="genereviews", record_id=f"{rid}#{field}"
            )

        out = AbstractData(
            pmid=rid,
            title=_f(result.get("title", ""), "title"),
            abstract=_f(result.get("abstract", ""), "doc"),
            authors=[
                _f(author, f"author:{i}")
                for i, author in enumerate(result.get("authors", []) or [])
            ],
            journal=_f(result.get("journal", ""), "journal"),
            publication_date=result.get("publication_date", ""),
        )
        guard_untrusted_limits(collect_untrusted(out))
        stamp_response_version(
            out,
            # get_abstract ALWAYS fetches live from PubMed E-utils, so the version reflects
            # live provenance -- not the local corpus version, and never null.
            corpus_version=live_corpus_version(),
        )
        return out
    except StructuredHTTPException:
        raise
    except Exception as e:
        logging.error("Error fetching abstract for PMID %s (%s)", pmid, type(e).__name__)
        raise upstream_ncbi_unavailable_error("fetch abstract") from e
