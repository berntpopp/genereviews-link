"""GET /chapters/{nbk_id}/tables/{table_id} -- fetch a single table as structured rows."""

from __future__ import annotations

from typing import Annotated, Literal, cast

from fastapi import APIRouter, Depends, Path, Query, Request

from genereview_link.api.errors import FieldError, StructuredHTTPException
from genereview_link.api.routes.passages import _get_corpus_version, get_repository
from genereview_link.corpus.tables import render_table_markdown
from genereview_link.models.genereview_models import ResponseMeta, TableResponse
from genereview_link.models.sections import SectionName, canonicalize_nbk_id
from genereview_link.retrieval.repository import GeneReviewRepository

router = APIRouter(tags=["Chapters"])


@router.get(
    "/chapters/{nbk_id}/tables/{table_id}",
    response_model=TableResponse,
    response_model_by_alias=True,
    operation_id="get_table",
    summary="Fetch a known GeneReviews table_id as structured rows",
    description=(
        "Fetch a known GeneReviews table_id as structured rows. Call "
        "get_chapter_metadata first to discover tables[] entries and avoid "
        "guessing numeric table labels."
    ),
)
async def get_table(
    nbk_id: Annotated[
        str,
        Path(
            pattern=r"^NBK\d+$",
            description="Bare NCBI Bookshelf ID, e.g. 'NBK1247'.",
        ),
    ],
    table_id: Annotated[
        str,
        Path(
            pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$",
            description="Table identifier, e.g. 't5'. Discoverable via get_chapter_metadata.",
        ),
    ],
    format: Annotated[
        Literal["structured", "markdown_table"],
        Query(
            description=(
                "Output format. 'structured' (default) returns header/rows only. "
                "'markdown_table' additionally populates markdown_table with a "
                "GitHub-flavored-markdown rendering of the table. "
                "header and rows are always present in both modes."
            ),
        ),
    ] = "structured",
    repo: Annotated[GeneReviewRepository, Depends(get_repository)] = ...,  # type: ignore[assignment]
    request: Request = ...,  # type: ignore[assignment]
) -> TableResponse:
    """Fetch a single chapter table as structured rows.

    Use after search_passages or get_chapter_metadata to retrieve a
    specific table's data when you need row-level access (the table is
    also retrievable as a passage_type='table' passage via search_passages).

    format=markdown_table adds a GitHub-flavored-markdown rendering in the
    markdown_table field. header and rows are still returned in that mode so
    the response remains self-describing and callers can switch formats without
    losing structured access.

    Latency: ~1ms p50.
    """
    nbk_id = canonicalize_nbk_id(nbk_id)
    table = await repo.get_table(nbk_id, table_id)
    if table is None:
        # Discover valid table IDs for this chapter to help the caller self-correct.
        meta = await repo.get_chapter_metadata(nbk_id)
        field_errors: list[FieldError] | None = None
        if meta is not None:
            valid = await repo.list_table_ids(nbk_id)
            if valid:
                field_errors = [
                    FieldError(field="table_id", valid_values=valid, reason="unknown table_id")
                ]
        raise StructuredHTTPException(
            status_code=404,
            code="table_not_found",
            message=f"table {table_id!r} not in chapter {nbk_id!r}",
            recovery_hint=(
                "check available tables via get_chapter_metadata or inspect "
                "field_errors.valid_values for the list of known table IDs"
            ),
            field_errors=field_errors,
            next_commands=[{"tool": "get_chapter_metadata", "arguments": {"nbk_id": nbk_id}}],
        )

    markdown_table: str | None = None
    if format == "markdown_table":
        markdown_table = render_table_markdown(
            caption=table.caption,
            header=table.header,
            rows=table.rows,
        )

    return TableResponse(
        nbk_id=table.nbk_id,
        table_id=table.table_id,
        caption=table.caption,
        heading_path=table.heading_path,
        section=cast(SectionName, table.section),
        header=table.header,
        rows=table.rows,
        passage_id=table.passage_id,
        markdown_table=markdown_table,
        **{"_meta": ResponseMeta(corpus_version=_get_corpus_version(request))},
    )
