"""Shared primary-gene-aware lexical ranking used by API and corpus evaluation."""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

from genereview_link.retrieval.query_gene import (
    apply_query_gene_context,
    is_defining_gene_for_chapter,
)
from genereview_link.retrieval.repository import LexicalPassageRow
from genereview_link.retrieval.rerank import rerank_with_embeddings

if TYPE_CHECKING:
    from genereview_link.retrieval.repository import GeneReviewRepository


async def augment_primary_gene_candidates(
    repository: GeneReviewRepository,
    rows: Sequence[LexicalPassageRow],
    query: str,
    *,
    explicit_gene: str | None = None,
    nbk_id: str | None = None,
    sections: list[str] | None = None,
    heading_path_contains: str | None = None,
    brief: bool = False,
    snippet_max_fragments: int = 2,
    snippet_max_words: int = 30,
    limit: int = 200,
) -> list[LexicalPassageRow]:
    """Add same-query candidates from a single exact primary-gene chapter search.

    This is candidate recall, not a result filter: if an exact uppercase query token is
    also present in corpus primary-gene metadata, matching passages join the normal
    lexical/dense candidate set and compete under the regular ranking model. Explicit
    gene filters already constrain the base retrieval; multi-gene and ambiguous-token
    queries are left alone.
    """
    from genereview_link.retrieval.query_gene import candidate_query_gene_symbol

    if explicit_gene is not None:
        return list(rows)
    symbol = candidate_query_gene_symbol(query)
    if symbol is None:
        return list(rows)
    primary_rows = await repository.search_passages(
        query,
        gene_symbol=symbol,
        gene_role="any",
        nbk_id=nbk_id,
        sections=sections,
        heading_path_contains=heading_path_contains,
        limit=limit,
        brief=brief,
        snippet_max_fragments=snippet_max_fragments,
        snippet_max_words=snippet_max_words,
    )
    defining_rows = [row for row in primary_rows if is_defining_gene_for_chapter(symbol, row)]
    if not defining_rows:
        return list(rows)
    combined = {row.passage.passage_id: row for row in rows}
    for row in defining_rows:
        combined.setdefault(row.passage.passage_id, row)
    return list(combined.values())


def rank_lexical_candidates(
    rows: Sequence[LexicalPassageRow],
    query: str,
    *,
    explicit_gene: str | None = None,
) -> list[LexicalPassageRow]:
    """Apply conservative gene context and the API's lexical ranking behavior."""
    contextual = apply_query_gene_context(rows, query, explicit_gene=explicit_gene)
    ranked, _diagnostics = rerank_with_embeddings(contextual, dense_scores={})
    return ranked


__all__ = ["augment_primary_gene_candidates", "rank_lexical_candidates"]
