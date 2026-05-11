"""Chapter-level routes: /chapters/{nbk_id}/sections/{section}."""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Path, Query, Request
from fastapi.responses import JSONResponse

from genereview_link.api.errors import StructuredHTTPException
from genereview_link.api.routes.passages import _get_corpus_version, get_repository
from genereview_link.models.genereview_models import (
    ChapterMetadataResponse,
    ChapterSectionResponse,
    PassageInSection,
    ResponseMeta,
    SectionSummary,
)
from genereview_link.models.sections import SectionName
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
    include: Annotated[
        list[Literal["concatenated_text"]] | None,
        Query(
            description=(
                "Opt into default-off response fields. Pass include=concatenated_text "
                "to receive the joined passage text in addition to passages[]."
            ),
        ),
    ] = None,
    repo: Annotated[GeneReviewRepository, Depends(get_repository)] = ...,  # type: ignore[assignment]
    request: Request = ...,  # type: ignore[assignment]
) -> ChapterSectionResponse | JSONResponse:
    """Return all passages for a specific section of a GeneReview chapter.

    By default returns individual passages only. Pass include=concatenated_text
    to also receive the joined passage text.
    """
    passages = await repo.get_section(nbk_id, section)
    if not passages:
        raise StructuredHTTPException(
            status_code=404,
            code="section_empty_for_chapter",
            message=f"chapter {nbk_id!r} has no passages in section {section!r}",
            recovery_hint=(
                "the chapter exists but this section has no rows. Use "
                "search_passages with nbk_id=<chapter> to discover which "
                "sections this chapter actually populates, or try a different "
                "section."
            ),
            next_commands=[
                {
                    "tool": "search_passages",
                    "arguments": {"q": "<your query>", "nbk_id": nbk_id},
                }
            ],
        )
    head = passages[0]
    include_set = set(include or [])
    concatenated = (
        "\n\n".join(p.text for p in passages) if "concatenated_text" in include_set else None
    )
    response = ChapterSectionResponse(  # type: ignore[call-arg]
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
        concatenated_text=concatenated,
        meta=ResponseMeta(corpus_version=_get_corpus_version(request)),
    )
    if "concatenated_text" not in include_set:
        return JSONResponse(
            response.model_dump(exclude={"concatenated_text"}, mode="json", by_alias=True)
        )
    return response


@router.get(
    "/chapters/{nbk_id}/metadata",
    response_model=ChapterMetadataResponse,
    response_model_by_alias=True,
    operation_id="get_chapter_metadata",
    summary="Return chapter title, last-updated date, gene symbols, section counts, and table count",
)
async def get_chapter_metadata(
    nbk_id: Annotated[
        str,
        Path(
            pattern=r"^NBK\d+$",
            description="Bare NCBI Bookshelf ID, e.g. 'NBK1247'.",
        ),
    ],
    repo: Annotated[GeneReviewRepository, Depends(get_repository)] = ...,  # type: ignore[assignment]
    request: Request = ...,  # type: ignore[assignment]
) -> ChapterMetadataResponse:
    """Return chapter title, last-updated date, gene symbols, section counts, and table count.

    Use this before get_chapter_section to avoid blind calls on empty sections.
    """
    meta = await repo.get_chapter_metadata(nbk_id)
    if meta is None:
        raise StructuredHTTPException(
            status_code=404,
            code="chapter_not_found",
            message=f"chapter {nbk_id!r} not in corpus",
            recovery_hint=("check the NBK ID; use search_passages to discover indexed chapters"),
            next_commands=[
                {"tool": "search_passages", "arguments": {"q": "<gene symbol or term>"}}
            ],
        )
    return ChapterMetadataResponse(  # type: ignore[call-arg]
        nbk_id=meta.nbk_id,
        title=meta.title,
        chapter_last_updated=meta.chapter_last_updated,
        gene_symbols=list(meta.gene_symbols),
        sections=[
            SectionSummary(section=s.section, passage_count=s.passage_count)  # type: ignore[arg-type]
            for s in meta.sections
        ],
        table_count=meta.table_count,
        meta=ResponseMeta(corpus_version=_get_corpus_version(request)),
    )
