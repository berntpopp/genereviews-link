"""Chapter-level routes: /chapters/{nbk_id}/sections/{section}."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path

from genereview_link.api.errors import FieldError, StructuredHTTPException
from genereview_link.api.routes.passages import get_repository
from genereview_link.models.genereview_models import (
    ChapterSectionResponse,
    PassageInSection,
)
from genereview_link.models.sections import SECTION_NAMES, SectionName
from genereview_link.retrieval.repository import GeneReviewRepository

router = APIRouter(tags=["Chapters"])


@router.get(
    "/chapters/{nbk_id}/sections/{section}",
    response_model=ChapterSectionResponse,
    response_model_by_alias=True,
    operation_id="get_chapter_section",
    summary="Fetch all passages for a section of a GeneReview chapter",
)
async def get_chapter_section(
    nbk_id: Annotated[
        str,
        Path(
            description="Bare NCBI Bookshelf ID, e.g. 'NBK1247'.",
        ),
    ],
    section: Annotated[
        SectionName,
        Path(
            description=(
                "Canonical section name; valid values listed in this parameter's JSONSchema enum."
            ),
        ),
    ],
    repo: Annotated[GeneReviewRepository, Depends(get_repository)] = ...,  # type: ignore[assignment]
) -> ChapterSectionResponse:
    """Return all passages for a specific section of a GeneReview chapter.

    Concatenates all passage texts in chunk order and returns both the
    individual passages and the combined text.
    """
    passages = await repo.get_section(nbk_id, section)
    if not passages:
        raise StructuredHTTPException(
            status_code=404,
            code="section_not_found",
            message=f"section {section!r} not found for chapter {nbk_id}",
            recovery_hint=(
                "valid section names are listed in the section parameter's "
                "JSONSchema enum; use search_passages without a section "
                "filter to discover which sections exist for this chapter."
            ),
            field_errors=[
                FieldError(
                    field="section",
                    reason="unknown_value",
                    valid_values=list(SECTION_NAMES),
                )
            ],
            next_commands=[
                {
                    "tool": "search_passages",
                    "arguments": {"q": "<your query>", "nbk_id": nbk_id},
                }
            ],
        )
    head = passages[0]
    return ChapterSectionResponse(
        nbk_id=nbk_id,
        chapter_title=head.chapter_title or "",
        chapter_section=section,
        chapter_last_updated=head.chapter_last_updated,
        passages=[
            PassageInSection(
                passage_id=p.passage_id,
                heading_path=p.heading_path,
                section_level=p.section_level,
                chunk_index=p.chunk_index,
                text=p.text,
            )
            for p in passages
        ],
        concatenated_text="\n\n".join(p.text for p in passages),
    )
