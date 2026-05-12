"""Fulltext endpoint for scraping complete GeneReview documents.

Provides REST API endpoint for retrieving comprehensive content from NCBI Bookshelf.
"""

import logging
import re
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query

from genereview_link.api.client_manager import get_managed_client
from genereview_link.api.eutils_client import EutilsClient
from genereview_link.models.genereview_models import (
    FullTextData,
    FullTextMetadata,
    GeneReviewSection,
)
from genereview_link.models.sections import canonicalize_nbk_id

router = APIRouter(prefix="/fulltext", tags=["Full Text"])


def _build_section(section_data: dict[str, Any]) -> GeneReviewSection:
    """Recursively convert a raw section dict from the scraper into a GeneReviewSection.

    The scraper produces sections with optional ``level`` and ``subsections`` keys.
    Subsections are themselves raw dicts and must be converted recursively. Depth is
    bounded by the scraper (level 2 sections contain level 3 subsections whose
    ``subsections`` field is always an empty dict), so recursion terminates quickly.
    """
    raw_subsections = section_data.get("subsections") or {}
    subsections: dict[str, GeneReviewSection] = {
        key: _build_section(value) for key, value in raw_subsections.items()
    }
    return GeneReviewSection(
        title=section_data["title"],
        content=section_data["content"],
        level=section_data.get("level", 1),
        subsections=subsections,
    )


def _filter_sections(
    sections: dict[str, GeneReviewSection], requested: str | None
) -> dict[str, GeneReviewSection]:
    """Filter ``sections`` by a comma-separated ``requested`` query string.

    Matching is fuzzy: a section is kept when its key (lowercased) equals any
    requested token OR contains a requested token as a substring. When
    ``requested`` is ``None`` or empty, all sections are returned unchanged.
    """
    if not requested:
        return sections
    tokens = [tok.strip().lower() for tok in requested.split(",") if tok.strip()]
    if not tokens:
        return sections
    return {
        key: section
        for key, section in sections.items()
        if key.lower() in tokens or any(tok in key.lower() for tok in tokens)
    }


@router.get(
    "/{nbk_id}",
    response_model=FullTextData,
    summary="Get comprehensive scraped content from NCBI Bookshelf",
    operation_id="get_fulltext",
)
async def get_fulltext(
    nbk_id: str,
    client: Annotated[EutilsClient, Depends(get_managed_client)],
    sections: Annotated[
        str | None,
        Query(
            description=(
                "Optional comma-separated list of section keys to return "
                "(e.g. 'summary,diagnosis,management'). Matching is fuzzy: "
                "tokens match exact keys or any key containing the token as "
                "a substring. When omitted, all sections are returned."
            ),
            examples=["summary,diagnosis,management"],
        ),
    ] = None,
    fresh: bool = Query(False, description="Bypass index; fetch live from NCBI"),
) -> FullTextData:
    """Scrape comprehensive content from an NCBI Bookshelf page.

    The NBK ID can be provided with or without the 'NBK' prefix.
    Returns structured sections, metadata, and the complete document content.
    When ``sections`` is supplied, only matching sections are included in the
    response.

    Pass ``?fresh=true`` to bypass the index and fetch live from NCBI.
    """
    # TODO: repository-first path (Phase 5.3+); for now passes through to EutilsClient
    # until repository is populated.
    try:
        nbk_id = canonicalize_nbk_id(nbk_id)
        # Clean up NBK ID - remove NBK prefix if present and ensure it's valid
        clean_id = re.sub(r"^NBK", "", nbk_id)
        if not clean_id.isdigit():
            raise HTTPException(status_code=400, detail=f"Invalid NBK ID format: {nbk_id}")

        # Construct the URL
        book_url = f"https://www.ncbi.nlm.nih.gov/books/NBK{clean_id}/"

        result = await client.scrape_genereview_comprehensive(book_url)

        if result.get("error"):
            raise HTTPException(
                status_code=404,
                detail=f"Could not scrape content: {result['error']}",
            )

        # Convert sections to GeneReviewSection objects (propagating level/subsections)
        all_sections: dict[str, GeneReviewSection] = {
            key: _build_section(section_data)
            for key, section_data in result.get("sections", {}).items()
        }

        # Filter sections when the caller requested a specific subset
        filtered_sections = _filter_sections(all_sections, sections)

        # Convert metadata
        metadata_dict = result.get("metadata", {})
        metadata = FullTextMetadata(
            authors=metadata_dict.get("authors"),
            update_info=metadata_dict.get("update_info"),
            publication_info=metadata_dict.get("publication_info"),
            last_updated=metadata_dict.get("last_updated"),
            references=metadata_dict.get("references", []),
        )

        out = FullTextData(
            nbk_id=result.get("nbk_id", clean_id),
            url=result.get("url", book_url),
            title=result.get("title", ""),
            sections=filtered_sections,
            metadata=metadata,
        )
        if fresh:
            out.corpus_version = f"live:{datetime.now(UTC).isoformat()}"
        return out
    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Error scraping NBK{nbk_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="An error occurred while scraping the full text.",
        ) from e
