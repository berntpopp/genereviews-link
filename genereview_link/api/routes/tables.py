"""GET /chapters/{nbk_id}/tables/{table_id} -- fetch a single table as structured rows."""

from __future__ import annotations

from typing import Annotated, cast

from fastapi import APIRouter, Depends, Path, Request

from genereview_link.api.errors import FieldError, StructuredHTTPException
from genereview_link.api.routes.passages import _get_corpus_version, get_repository
from genereview_link.api.routes.table_enrichment import fence_table_cells
from genereview_link.api.untrusted_limits import collect_untrusted, guard_untrusted_limits
from genereview_link.mcp.untrusted_content import fence_untrusted_text
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
        "guessing numeric table labels. caption and every header/row cell are "
        "upstream table prose, emitted as v1.1 untrusted_text objects."
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
    repo: Annotated[GeneReviewRepository, Depends(get_repository)] = ...,  # type: ignore[assignment]
    request: Request = ...,  # type: ignore[assignment]
) -> TableResponse:
    """Fetch a single chapter table as structured rows.

    Use after search_passages or get_chapter_metadata to retrieve a
    specific table's data when you need row-level access (the table is
    also retrievable as a passage_type='table' passage via search_passages).

    caption + every header/row cell is upstream table prose, so each is
    emitted as a v1.1 untrusted_text object. The former ``markdown_table``
    field and its ``format`` query parameter were dropped (they duplicated the
    now-fenced cells); callers render markdown from the structured cells.

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

    base = f"{table.nbk_id}#table:{table.table_id}"
    fenced_caption = fence_untrusted_text(table.caption, source="genereviews", record_id=base)
    fenced_heading = (
        fence_untrusted_text(
            table.heading_path, source="genereviews", record_id=f"{base}#heading_path"
        )
        if table.heading_path is not None
        else None
    )
    fenced_header, fenced_rows = fence_table_cells(
        table.header, table.rows, nbk_id=table.nbk_id, table_id=table.table_id
    )

    response = TableResponse(
        nbk_id=table.nbk_id,
        table_id=table.table_id,
        caption=fenced_caption,
        heading_path=fenced_heading,
        section=cast(SectionName, table.section),
        header=fenced_header,
        rows=fenced_rows,
        passage_id=table.passage_id,
        **{"_meta": ResponseMeta(corpus_version=_get_corpus_version(request))},
    )
    guard_untrusted_limits(collect_untrusted(response))
    return response
