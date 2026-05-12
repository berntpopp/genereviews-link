"""Empty-result diagnostic suggestions for search_passages.

Rule-based, not LLM-generated. Triggered when len(results) == 0.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SearchDiagnostics:
    lexical_candidate_count: int
    unfiltered_lexical_count: int | None
    applied_filters: list[str]
    suggestions: list[str]


def build_search_diagnostics(
    *,
    query: str,
    applied_filters: list[str],
    lexical_candidate_count: int,
    unfiltered_lexical_count: int | None,
) -> SearchDiagnostics:
    suggestions: list[str] = []
    unfiltered_count = unfiltered_lexical_count or 0

    # Rule 1: gene filter killed >90% of hits (gene assumed valid post-T4.6)
    gene_filter = next((f for f in applied_filters if f.startswith("gene=")), None)
    if gene_filter and unfiltered_count > 0 and lexical_candidate_count < unfiltered_count / 10:
        suggestions.append("gene-filter-drops-all")

    # Rule 2: query is very long or very specific
    if len(query) > 80 or len(query.split()) > 8:
        suggestions.append("broaden-query")

    # Rule 3: sections filter drops everything
    section_filter = next((f for f in applied_filters if f.startswith("sections=")), None)
    if section_filter and unfiltered_count > 0 and lexical_candidate_count == 0:
        suggestions.append("section-filter-drops-all")

    # Rule 4: nbk_id filter drops everything
    nbk_filter = next((f for f in applied_filters if f.startswith("nbk_id=")), None)
    if nbk_filter and unfiltered_count > 0 and lexical_candidate_count == 0:
        suggestions.append("nbk-id-filter-drops-all")

    return SearchDiagnostics(
        lexical_candidate_count=lexical_candidate_count,
        unfiltered_lexical_count=unfiltered_lexical_count,
        applied_filters=applied_filters,
        suggestions=suggestions,
    )
