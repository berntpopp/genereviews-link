"""Chapter-level routes: /chapters/{nbk}/sections/{section}."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from genereview_link.api.routes.passages import get_repository
from genereview_link.retrieval.repository import GeneReviewRepository

router = APIRouter(tags=["Chapters"])


@router.get(
    "/chapters/{nbk}/sections/{section}",
    operation_id="get_chapter_section",
    summary="Fetch all passages for a section of a GeneReview chapter",
)
async def get_chapter_section(
    nbk: str,
    section: str,
    repo: Annotated[GeneReviewRepository, Depends(get_repository)] = ...,  # type: ignore[assignment]
) -> dict[str, object]:
    """Return all passages for a specific section of a GeneReview chapter.

    Concatenates all passage texts in chunk order and returns both the
    individual passages and the combined text.
    """
    passages = await repo.get_section(nbk, section)
    if not passages:
        raise HTTPException(status_code=404, detail="section not found")
    return {
        "nbk_id": nbk,
        "chapter_section": section,
        "passages": [
            {
                "passage_id": p.passage_id,
                "heading_path": p.heading_path,
                "section_level": p.section_level,
                "chunk_index": p.chunk_index,
                "text": p.text,
            }
            for p in passages
        ],
        "concatenated_text": "\n\n".join(p.text for p in passages),
    }
