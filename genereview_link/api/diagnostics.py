"""Empty-result diagnostic suggestions for search_passages.

Rule-based, not LLM-generated. Triggered when len(results) == 0.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SearchDiagnostics:
    lexical_hits: int
    lexical_hits_after_filters: int
    applied_filters: list[str]
    suggestions: list[str]


def build_search_diagnostics(
    *,
    query: str,
    applied_filters: list[str],
    lexical_hits: int,
    lexical_hits_after_filters: int,
) -> SearchDiagnostics:
    suggestions: list[str] = []

    # Rule 1: gene filter killed >90% of hits (gene assumed valid post-T4.6)
    gene_filter = next((f for f in applied_filters if f.startswith("gene=")), None)
    if (
        gene_filter
        and lexical_hits > 0
        and lexical_hits_after_filters < lexical_hits / 10
    ):
        symbol = gene_filter.split("=", 1)[1]
        suggestions.append(
            f"the gene {symbol!r} is indexed but no passages match within the current filters; "
            "try removing the sections filter or broadening q"
        )

    # Rule 2: query is very long or very specific
    if len(query) > 80 or len(query.split()) > 8:
        suggestions.append("broaden q (current query is very specific)")

    # Rule 3: sections filter drops everything
    section_filter = next((f for f in applied_filters if f.startswith("sections=")), None)
    if (
        section_filter
        and lexical_hits > 0
        and lexical_hits_after_filters == 0
    ):
        suggestions.append("try other sections — current sections filter excludes all hits")

    return SearchDiagnostics(
        lexical_hits=lexical_hits,
        lexical_hits_after_filters=lexical_hits_after_filters,
        applied_filters=applied_filters,
        suggestions=suggestions,
    )
