"""Chapter-level routes: /chapters/{nbk_id}/sections/{section}."""

from __future__ import annotations

from typing import Annotated, Literal, cast

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
    TableSummary,
)
from genereview_link.models.sections import (
    SYSTEMATICALLY_UNSCRAPED_SECTIONS,
    SectionName,
    canonicalize_nbk_id,
)
from genereview_link.retrieval.repository import GeneReviewRepository, _note_for_empty_section

router = APIRouter(tags=["Chapters"])


def _strip_overlap(parts: list[str], min_overlap: int = 30) -> str:
    """Join text chunks while removing the longest common suffix/prefix overlap.

    When the chunker produces overlapping windows, adjacent chunks share a
    common tail/head region.  This helper finds the longest suffix of each
    previous chunk that matches a prefix of the next chunk (at least
    ``min_overlap`` characters) and removes the duplicate before joining.

    Returns the deduplicated concatenation (no separator inserted — the
    overlap region is the natural boundary).
    """
    if not parts:
        return ""
    out = [parts[0]]
    for nxt in parts[1:]:
        prev = out[-1]
        max_match = min(len(prev), len(nxt))
        overlap_len = 0
        for k in range(max_match, min_overlap - 1, -1):
            if prev[-k:] == nxt[:k]:
                overlap_len = k
                break
        out.append(nxt[overlap_len:])
    return "".join(out)


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
            pattern=r"^NBK\d+$",
            description="Bare NCBI Bookshelf ID, e.g. 'NBK1247'.",
        ),
    ],
    section: Annotated[
        SectionName,
        Path(
            description=(
                'Canonical section name. Values: "summary", "diagnosis", '
                '"clinical_features", "management", "genetic_counseling", '
                '"molecular_genetics", "resources", "other", "references".'
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
    dedupe: Annotated[
        bool,
        Query(
            description=(
                "Strip overlapping text between adjacent chunks "
                "(longest-common-suffix/prefix heuristic). "
                "Default True for LLM-ready joined text. Pass false only when "
                "you need literal stored chunk text."
            ),
        ),
    ] = True,
    heading_path_contains: Annotated[
        str | None,
        Query(
            max_length=200,
            description=(
                "Optional substring match on passage heading_path (case-insensitive). "
                "Use to narrow a section to a specific subsection. Example: "
                "heading_path_contains='Risk-Reducing Surgery' on section=management "
                "returns only the surgery subsection's passages instead of all 10."
            ),
        ),
    ] = None,
    repo: Annotated[GeneReviewRepository, Depends(get_repository)] = ...,  # type: ignore[assignment]
    request: Request = ...,  # type: ignore[assignment]
) -> ChapterSectionResponse | JSONResponse:
    """Return all passages for a specific section of a GeneReview chapter.

    By default returns individual passages only. Pass include=concatenated_text
    to also receive joined passage text with chunk overlap stripped by default.

    Latency: ~1ms p50.
    """
    nbk_id = canonicalize_nbk_id(nbk_id)
    passages = await repo.get_section(nbk_id, section, heading_path_contains=heading_path_contains)
    if not passages:
        chapter = await repo.get_chapter_by_nbk(nbk_id)
        if chapter is None:
            raise StructuredHTTPException(
                status_code=404,
                code="chapter_not_found",
                message=f"chapter {nbk_id!r} not in corpus",
                recovery_hint="check the NBK ID; use search_passages to discover indexed chapters",
                next_commands=[
                    {"tool": "search_passages", "arguments": {"q": "<gene symbol or term>"}}
                ],
            )
        if section in SYSTEMATICALLY_UNSCRAPED_SECTIONS:
            return ChapterSectionResponse(  # type: ignore[call-arg]
                nbk_id=nbk_id,
                chapter_title=chapter.title,
                chapter_section=section,
                chapter_last_updated=chapter.last_updated_date,
                passages=[],
                passage_count=0,
                note=_note_for_empty_section(section, nbk_id),
                meta=ResponseMeta(corpus_version=_get_corpus_version(request)),
            )
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
    if "concatenated_text" in include_set:
        parts = [p.text for p in passages]
        concatenated: str | None = _strip_overlap(parts) if dedupe else "\n\n".join(parts)
    else:
        concatenated = None
    passages_response = [
        PassageInSection(
            passage_id=p.passage_id,
            heading_path=p.heading_path,
            section_level=p.section_level,
            chunk_index=p.chunk_index,
            text=p.text,
        )
        for p in passages
    ]
    response = ChapterSectionResponse(  # type: ignore[call-arg]
        nbk_id=nbk_id,
        chapter_title=head.chapter_title or "",
        chapter_section=section,
        chapter_last_updated=head.chapter_last_updated,
        passages=passages_response,
        passage_count=len(passages_response),
        concatenated_text=concatenated,
        concatenated_char_count=(len(concatenated) if concatenated is not None else None),
        meta=ResponseMeta(corpus_version=_get_corpus_version(request)),
    )
    if "concatenated_text" not in include_set:
        return JSONResponse(
            response.model_dump(
                exclude={"concatenated_text", "concatenated_char_count"},
                mode="json",
                by_alias=True,
            )
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

    Latency: ~1ms p50.
    """
    nbk_id = canonicalize_nbk_id(nbk_id)
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
            SectionSummary(
                section=cast(SectionName, s.section),
                passage_count=s.passage_count,
                total_char_count=s.total_char_count,
                note=s.note,
            )
            for s in meta.sections
        ],
        table_count=meta.table_count,
        tables=[
            TableSummary(
                table_id=t.table_id,
                caption=t.caption,
                section=cast(SectionName, t.section),
                heading_path=t.heading_path,
                passage_id=t.passage_id,
            )
            for t in meta.tables
        ],
        meta=ResponseMeta(corpus_version=_get_corpus_version(request)),
    )
