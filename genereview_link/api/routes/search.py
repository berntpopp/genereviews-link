"""Search endpoint for finding GeneReviews by gene symbol.

Provides REST API endpoint for searching NCBI database.
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request

from genereview_link.api.client_manager import get_managed_client
from genereview_link.api.errors import StructuredHTTPException
from genereview_link.api.eutils_client import EutilsClient
from genereview_link.api.orchestration import (
    active_corpus_version,
    get_optional_repository,
    live_corpus_version,
    stamp_response_version,
)
from genereview_link.api.orchestration_errors import internal_orchestration_error
from genereview_link.logging_config import PerformanceLogger, get_logger
from genereview_link.models.genereview_models import SearchResult

router = APIRouter(prefix="/search", tags=["Search"])
logger = get_logger(__name__)


def _empty_search_recovery(gene_symbol: str) -> tuple[str, list[dict[str, Any]]]:
    gene = gene_symbol.upper()
    return (
        "No PubMed resolver hit was found. Use corpus-backed passage search for "
        "indexed GeneReviews evidence, or broaden the gene/query if this may be a "
        "multi-gene chapter.",
        [{"tool": "search_passages", "arguments": {"gene": gene, "q": gene}}],
    )


@router.get(
    "/{gene_symbol}",
    response_model=SearchResult,
    summary="Search GeneReviews by gene symbol with corpus-first fallback behavior",
    description=(
        "Search GeneReviews by gene symbol using the indexed corpus first when "
        "available, then fallback to live NCBI E-utils. If resolver links are "
        "unavailable or no PubMed ID is found, use search_passages(gene=<symbol>) "
        "for indexed chapter evidence. Pass fresh=true to bypass the corpus and "
        "query live NCBI."
    ),
    operation_id="search_genereviews",
)
async def search_genereviews(
    request: Request,
    gene_symbol: str,
    client: Annotated[EutilsClient, Depends(get_managed_client)],
    retmax: int = Query(20, description="Maximum number of results to return", ge=1, le=100),
    fresh: bool = Query(False, description="Bypass index; fetch live from NCBI"),
) -> SearchResult:
    """Search for GeneReviews associated with the given gene symbol.

    Uses NCBI E-utils esearch to find relevant GeneReviews.
    Returns a list of PubMed IDs along with search metadata.

    Pass ``?fresh=true`` to bypass the index and fetch live from NCBI.
    """
    # Create request-scoped logger. correlation_id is injected automatically by
    # the structlog processor wired in logging_config.py (Task B3).
    request_logger = logger.bind(
        gene_symbol=gene_symbol,
        retmax=retmax,
    )

    request_logger.info("Starting GeneReview search")

    with PerformanceLogger(request_logger, "genereview_search") as perf:
        try:
            if not fresh:
                repo = get_optional_repository(request)
                if repo is not None:
                    chapters = await repo.get_chapters_by_gene(gene_symbol.upper())
                    ids = [chapter.pubmed_id for chapter in chapters if chapter.pubmed_id]
                    if ids:
                        out = SearchResult(
                            count=len(ids),
                            retmax=retmax,
                            retstart=0,
                            ids=ids[:retmax],
                            webenv="",
                            querykey="",
                        )
                        stamp_response_version(
                            out,
                            corpus_version=active_corpus_version(request),
                        )
                        perf.add_context(result_count=len(ids), ids_found=len(out.ids))
                        request_logger.info(
                            "Search completed from repository",
                            result_count=len(ids),
                            ids_found=len(out.ids),
                        )
                        return out

            result = await client.search_genereviews(gene_symbol, retmax=retmax)

            # Log search results
            result_count = result.get("count", 0)
            ids_found = len(result.get("ids", []))

            perf.add_context(result_count=result_count, ids_found=ids_found)
            request_logger.info(
                "Search completed successfully",
                result_count=result_count,
                ids_found=ids_found,
            )

            out = SearchResult(**result)
            if out.count == 0 and not out.ids:
                recovery_hint, next_commands = _empty_search_recovery(gene_symbol)
                out.recovery_hint = recovery_hint
                out.meta.next_commands = next_commands
            stamp_response_version(
                out,
                # This path is reached only via the live NCBI search (a repository hit
                # returns early above), so the version always reflects live provenance
                # -- never null. (A corpus hit keeps the active corpus_version, above.)
                corpus_version=live_corpus_version(),
            )
            return out

        except StructuredHTTPException:
            raise
        except Exception as e:
            request_logger.error(
                "Search failed",
                error_type=type(e).__name__,
                error_message=str(e),
                exc_info=True,
            )
            raise internal_orchestration_error(
                "search GeneReviews",
                gene_symbol=gene_symbol,
            ) from e
