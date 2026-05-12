"""License/attribution endpoint.

Single source of truth for the GeneReviews data-source license. Returns the
same LicenseNotice payload that older API revisions inlined on every record
response — exposed once here to keep per-record responses lean.
"""

from __future__ import annotations

from fastapi import APIRouter

from genereview_link.models.genereview_models import LicenseNotice

router = APIRouter(tags=["License"])


@router.get(
    "/license",
    response_model=LicenseNotice,
    summary="Get the GeneReviews data-source license and attribution notice",
    operation_id="get_license",
)
async def get_license() -> LicenseNotice:
    """Get attribution and citation terms for the GeneReviews corpus.

    Use this tool when emitting a citation block, compiling a research-use
    disclosure, or verifying redistribution terms before exporting passages.
    Returns the same content as the genereview://license resource.
    """
    return LicenseNotice()
