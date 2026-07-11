"""Chapter-level routes: /chapters/{nbk_id}/sections/{section}."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, cast

from fastapi import APIRouter, Depends, Path, Query, Request

from genereview_link.api.errors import StructuredHTTPException
from genereview_link.api.routes.passages import _get_corpus_version, get_repository
from genereview_link.api.untrusted_limits import collect_untrusted, guard_untrusted_limits
from genereview_link.mcp.untrusted_content import fence_untrusted_text
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
from genereview_link.models.staleness import (
    likely_stale_for_therapeutics as _likely_stale,
)
from genereview_link.models.staleness import (
    staleness_band as _staleness_band,
)
from genereview_link.models.staleness import (
    years_since as _years_since,
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
    description=(
        "Fetch all passages for a section. For keyword search within this section, "
        "use search_passages(q, nbk_id=..., sections=[...]). ``content`` carries the "
        "full joined section text (v1.1 untrusted_text; overlap stripped by default). "
        "Pass dedupe=false only for literal chunk text."
    ),
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
    dedupe: Annotated[
        bool,
        Query(
            description=(
                "Strip overlapping text between adjacent chunks "
                "(longest-common-suffix/prefix heuristic). "
                "Default True for LLM-ready joined text in ``content``. Pass false "
                "only when you need the literal stored chunk concatenation."
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
) -> ChapterSectionResponse:
    """Return all passages for a specific section of a GeneReview chapter.

    ``passages[]`` carries structural identifiers only (passage_id,
    heading_path, section_level, chunk_index); the section's full text is
    emitted once, fenced, on ``content`` (v1.1 untrusted_text) to avoid
    duplicating upstream prose across sibling fields.

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
            empty_content = fence_untrusted_text(
                "", source="genereviews", record_id=f"{nbk_id}#{section}"
            )
            empty_title = fence_untrusted_text(
                chapter.title, source="genereviews", record_id=f"{nbk_id}#chapter_title"
            )
            guard_untrusted_limits([empty_content, empty_title])
            return ChapterSectionResponse(  # type: ignore[call-arg]
                nbk_id=nbk_id,
                chapter_title=empty_title,
                chapter_section=section,
                chapter_last_updated=chapter.last_updated_date,
                passages=[],
                passage_count=0,
                content=empty_content,
                content_char_count=0,
                note=_note_for_empty_section(section, nbk_id),
                meta=ResponseMeta(
                    corpus_version=_get_corpus_version(request),
                    next_commands=(
                        [{"tool": "get_abstract", "arguments": {"pmid": chapter.pubmed_id}}]
                        if chapter.pubmed_id
                        else None
                    ),
                ),
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
    parts = [p.text for p in passages]
    concatenated = _strip_overlap(parts) if dedupe else "\n\n".join(parts)
    fenced_content = fence_untrusted_text(
        concatenated, source="genereviews", record_id=f"{nbk_id}#{section}"
    )
    fenced_chapter_title = fence_untrusted_text(
        head.chapter_title or "", source="genereviews", record_id=f"{nbk_id}#chapter_title"
    )
    passages_response = [
        PassageInSection(
            passage_id=p.passage_id,
            heading_path=(
                fence_untrusted_text(
                    p.heading_path,
                    source="genereviews",
                    record_id=f"{p.passage_id}#heading_path",
                )
                if p.heading_path is not None
                else None
            ),
            section_level=p.section_level,
            chunk_index=p.chunk_index,
        )
        for p in passages
    ]
    response = ChapterSectionResponse(  # type: ignore[call-arg]
        nbk_id=nbk_id,
        chapter_title=fenced_chapter_title,
        chapter_section=section,
        chapter_last_updated=head.chapter_last_updated,
        passages=passages_response,
        passage_count=len(passages_response),
        content=fenced_content,
        content_char_count=len(fenced_content.text),
        meta=ResponseMeta(corpus_version=_get_corpus_version(request)),
    )
    guard_untrusted_limits(collect_untrusted(response))
    return response


@router.get(
    "/chapters/{nbk_id}/metadata",
    response_model=ChapterMetadataResponse,
    response_model_by_alias=True,
    operation_id="get_chapter_metadata",
    summary=(
        "The chapter outline tool: title, dates, gene symbols, section counts, "
        "and tables. Use search_passages(q, nbk_id=...) for keyword search "
        "within this chapter."
    ),
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
    """The chapter outline tool.

    Returns chapter title, dates, gene symbols, per-section passage_count, and
    the full tables[] list with table_id, caption, section, and heading_path.
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
    sections_built = [
        SectionSummary(
            section=cast(SectionName, s.section),
            passage_count=s.passage_count,
            total_char_count=s.total_char_count,
            note=s.note,
        )
        for s in meta.sections
    ]
    today = datetime.now(tz=UTC).date()
    ysu = _years_since(meta.chapter_last_updated, today)
    band = _staleness_band(ysu)
    total_chars = sum(s.total_char_count for s in sections_built)
    tables = [
        TableSummary(
            table_id=t.table_id,
            caption=fence_untrusted_text(
                t.caption,
                source="genereviews",
                record_id=f"{meta.nbk_id}#table:{t.table_id}",
            ),
            section=cast(SectionName, t.section),
            heading_path=fence_untrusted_text(
                t.heading_path,
                source="genereviews",
                record_id=f"{meta.nbk_id}#table:{t.table_id}#heading_path",
            ),
            passage_id=t.passage_id,
        )
        for t in meta.tables
    ]
    response = ChapterMetadataResponse(  # type: ignore[call-arg]
        nbk_id=meta.nbk_id,
        title=fence_untrusted_text(
            meta.title, source="genereviews", record_id=f"{meta.nbk_id}#title"
        ),
        chapter_last_updated=meta.chapter_last_updated,
        chapter_ingested_at=meta.chapter_ingested_at,
        gene_symbols=list(meta.gene_symbols),
        sections=sections_built,
        table_count=meta.table_count,
        tables=tables,
        years_since_update=ysu,
        staleness_band=band,
        likely_stale_for_therapeutics=_likely_stale(band, sections_built),
        total_char_count=total_chars,
        total_tokens_estimate=total_chars // 4,
        meta=ResponseMeta(corpus_version=_get_corpus_version(request)),
    )
    guard_untrusted_limits(collect_untrusted(response))
    return response
