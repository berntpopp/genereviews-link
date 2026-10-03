"""Conservative recognition for canonical gene symbols written in a query."""

from __future__ import annotations

import re
from collections.abc import Collection, Sequence
from dataclasses import replace

from genereview_link.retrieval.repository import LexicalPassageRow

_CANONICAL_TOKEN = re.compile(r"(?<![A-Za-z0-9])[A-Z][A-Z0-9-]*(?![A-Za-z0-9])")
_GENETIC_CONTEXT = re.compile(
    r"\b(?:gene|genes|genetic|genetics|germline|mutation|mutations|"
    r"variant|variants|pathogenic|allele|alleles|molecular)\b",
    re.IGNORECASE,
)
_AMBIGUOUS_ENGLISH_SYMBOLS = frozenset({"CAT", "KIT", "MET", "SET"})
_QUERY_ACRONYMS = frozenset({"CT", "DNA", "MRI", "RNA", "US"})
_VARIANT_LIKE_TOKEN = re.compile(r"^[A-Z]\d{2,}[A-Z]$")


def is_defining_gene_for_chapter(gene_symbol: str, row: LexicalPassageRow) -> bool:
    """Match the repository's existing defining-chapter contract.

    A gene is defining when it is curated as primary, is the chapter's sole gene, or
    appears as a whole word in the chapter title. Mention-only body/cross-reference genes
    do not qualify.
    """
    passage = row.passage
    if gene_symbol in passage.primary_gene_symbols:
        return True
    if len(passage.gene_symbols) == 1 and passage.gene_symbols[0] == gene_symbol:
        return True
    title = passage.chapter_title or ""
    return re.search(r"\b" + re.escape(gene_symbol) + r"\b", title, re.IGNORECASE) is not None


def candidate_query_gene_symbol(query: str) -> str | None:
    """Return one syntactic symbol candidate; callers validate it against metadata."""
    tokens = {
        token
        for token in _CANONICAL_TOKEN.findall(query)
        if token not in _QUERY_ACRONYMS and not _VARIANT_LIKE_TOKEN.fullmatch(token)
        if token not in _AMBIGUOUS_ENGLISH_SYMBOLS or _GENETIC_CONTEXT.search(query)
    }
    return next(iter(tokens)) if len(tokens) == 1 else None


def infer_query_gene_symbol(query: str, chapter_symbols: Collection[str]) -> str | None:
    """Return one exact chapter gene symbol explicitly typed in uppercase.

    This is ranking context only. Callers must not use the result as a filter. A query
    naming multiple primary symbols has no single gene context, and a few English words
    that are also symbols require genetic context before they can be inferred.
    """
    candidate = candidate_query_gene_symbol(query)
    if candidate not in chapter_symbols:
        return None
    return candidate


def apply_query_gene_context(
    rows: Sequence[LexicalPassageRow],
    query: str,
    *,
    explicit_gene: str | None = None,
) -> list[LexicalPassageRow]:
    """Annotate primary-gene matches without filtering any retrieval candidates.

    An explicit filter takes precedence over free-text inference. Otherwise, a symbol is
    inferred only when one uppercase symbol-like token matches chapter gene metadata among
    the retrieved chapters. Common query acronyms and HGVS-style variant tokens are ignored.
    The candidate set is expected to include both lexical and dense results before this
    function is called.
    """
    primary_symbols = {symbol for row in rows for symbol in row.passage.gene_symbols}
    query_symbol = explicit_gene or infer_query_gene_symbol(query, primary_symbols)
    if query_symbol is None:
        return list(rows)
    return [
        replace(
            row,
            primary_gene_match=is_defining_gene_for_chapter(query_symbol, row),
        )
        for row in rows
    ]


__all__ = [
    "apply_query_gene_context",
    "candidate_query_gene_symbol",
    "infer_query_gene_symbol",
    "is_defining_gene_for_chapter",
]
