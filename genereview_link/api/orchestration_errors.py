"""Structured errors for orchestration entry points."""

from __future__ import annotations

from typing import Any

from genereview_link.api.errors import StructuredHTTPException


def _search_passages_command(gene_symbol: str) -> dict[str, Any]:
    gene = gene_symbol.upper()
    return {"tool": "search_passages", "arguments": {"gene": gene, "q": gene}}


def gene_not_found_error(gene_symbol: str) -> StructuredHTTPException:
    gene = gene_symbol.upper()
    return StructuredHTTPException(
        status_code=404,
        code="gene_not_found",
        message=f"No GeneReviews chapter was found for gene symbol {gene}.",
        recovery_hint=(
            "Try search_passages with the gene filter, or broaden the query if "
            "the gene may be mentioned in a multi-gene chapter."
        ),
        next_commands=[_search_passages_command(gene)],
    )


def pmid_resolver_failed_error(
    pubmed_id: str,
    *,
    gene_symbol: str | None = None,
) -> StructuredHTTPException:
    commands: list[dict[str, Any]] = []
    if gene_symbol:
        commands.append(_search_passages_command(gene_symbol))
    return StructuredHTTPException(
        status_code=502,
        code="pmid_resolver_failed",
        message=f"Could not resolve PubMed ID {pubmed_id} to an NCBI Bookshelf chapter.",
        recovery_hint=(
            "Use corpus-backed passage search when possible; live PubMed links "
            "can omit Bookshelf relationships even for indexed GeneReviews."
        ),
        next_commands=commands,
    )


def upstream_ncbi_unavailable_error(action: str) -> StructuredHTTPException:
    return StructuredHTTPException(
        status_code=502,
        code="upstream_ncbi_unavailable",
        message=f"NCBI was unavailable while attempting to {action}.",
        recovery_hint="Retry later or use indexed corpus tools such as search_passages.",
    )


def abstract_not_found_error(pubmed_id: str) -> StructuredHTTPException:
    return StructuredHTTPException(
        status_code=404,
        code="abstract_not_found",
        message=f"Abstract not found for PubMed ID {pubmed_id}.",
        recovery_hint=(
            "Verify the PubMed ID, or use search_passages for indexed GeneReviews "
            "content when a full abstract is not required."
        ),
    )


def invalid_nbk_id_error(nbk_id: str) -> StructuredHTTPException:
    return StructuredHTTPException(
        status_code=400,
        code="invalid_nbk_id",
        message=f"Invalid NBK ID format: {nbk_id}.",
        recovery_hint="Use an NCBI Bookshelf identifier such as NBK1247 or 1247.",
    )


def fulltext_scrape_failed_error(nbk_id: str, reason: str) -> StructuredHTTPException:
    return StructuredHTTPException(
        status_code=404,
        code="fulltext_scrape_failed",
        message=f"Could not scrape content for {nbk_id}: {reason}.",
        recovery_hint=(
            "Verify the NBK ID in NCBI Bookshelf, or use search_passages for indexed "
            "GeneReviews passage retrieval."
        ),
    )


def internal_orchestration_error(
    action: str,
    *,
    gene_symbol: str | None = None,
) -> StructuredHTTPException:
    commands: list[dict[str, Any]] = []
    if gene_symbol:
        commands.append(_search_passages_command(gene_symbol))
    return StructuredHTTPException(
        status_code=500,
        code="internal_error",
        message=f"An internal error occurred while attempting to {action}.",
        recovery_hint=(
            "Retry once. If the error persists, use search_passages or "
            "get_chapter_metadata for indexed corpus retrieval."
        ),
        next_commands=commands,
    )
