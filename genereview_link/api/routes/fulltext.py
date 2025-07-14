import logging
import re
from fastapi import APIRouter, Depends, HTTPException

from genereview_link.models.genereview_models import (
    FullTextData,
    GeneReviewSection,
    FullTextMetadata,
)
from genereview_link.api.eutils_client import EutilsClient
from genereview_link.api.client_manager import get_managed_client

router = APIRouter(prefix="/fulltext", tags=["Full Text"])


@router.get(
    "/{nbk_id}",
    response_model=FullTextData,
    summary="Get comprehensive scraped content from NCBI Bookshelf",
    operation_id="get_fulltext",
)
async def get_fulltext(
    nbk_id: str,
    client: EutilsClient = Depends(get_managed_client),
) -> FullTextData:
    """
    Scrape comprehensive content from an NCBI Bookshelf page.

    The NBK ID can be provided with or without the 'NBK' prefix.
    Returns structured sections, metadata, and the complete document content.
    """
    try:
        # Clean up NBK ID - remove NBK prefix if present and ensure it's valid
        clean_id = re.sub(r"^NBK", "", nbk_id)
        if not clean_id.isdigit():
            raise HTTPException(
                status_code=400, detail=f"Invalid NBK ID format: {nbk_id}"
            )

        # Construct the URL
        book_url = f"https://www.ncbi.nlm.nih.gov/books/NBK{clean_id}/"

        result = await client.scrape_genereview_comprehensive(book_url)

        if result.get("error"):
            raise HTTPException(
                status_code=404, detail=f"Could not scrape content: {result['error']}"
            )

        # Convert sections to GeneReviewSection objects
        sections = {}
        for key, section_data in result.get("sections", {}).items():
            sections[key] = GeneReviewSection(
                title=section_data["title"], content=section_data["content"]
            )

        # Convert metadata
        metadata_dict = result.get("metadata", {})
        metadata = FullTextMetadata(
            authors=metadata_dict.get("authors"),
            update_info=metadata_dict.get("update_info"),
            publication_info=metadata_dict.get("publication_info"),
            last_updated=metadata_dict.get("last_updated"),
            references=metadata_dict.get("references", []),
        )

        return FullTextData(
            nbk_id=result.get("nbk_id", clean_id),
            url=result.get("url", book_url),
            title=result.get("title", ""),
            sections=sections,
            metadata=metadata,
        )
    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Error scraping NBK{nbk_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=500, detail="An error occurred while scraping the full text."
        )
