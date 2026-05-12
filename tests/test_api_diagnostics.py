"""Unit tests for the empty-result diagnostics module."""

from __future__ import annotations

from genereview_link.api.diagnostics import build_search_diagnostics


def test_diagnostics_suggests_dropping_gene_when_filter_kills_hits() -> None:
    diag = build_search_diagnostics(
        query="risk-reducing surgery",
        applied_filters=["gene=BRCA1", "sections=management"],
        lexical_candidate_count=2,
        unfiltered_lexical_count=120,
    )
    assert diag.lexical_candidate_count == 2
    assert diag.unfiltered_lexical_count == 120
    assert any("gene" in s.lower() for s in diag.suggestions)


def test_diagnostics_suggests_broadening_long_query() -> None:
    long_q = "x " * 50
    diag = build_search_diagnostics(
        query=long_q,
        applied_filters=[],
        lexical_candidate_count=0,
        unfiltered_lexical_count=None,
    )
    assert any("broaden" in s.lower() for s in diag.suggestions)


def test_diagnostics_suggests_other_sections_when_section_filter_drops_all() -> None:
    diag = build_search_diagnostics(
        query="foo",
        applied_filters=["sections=management"],
        lexical_candidate_count=0,
        unfiltered_lexical_count=10,
    )
    assert any("section" in s.lower() for s in diag.suggestions)


def test_diagnostics_suggests_other_chapters_when_nbk_filter_drops_all() -> None:
    diag = build_search_diagnostics(
        query="foo",
        applied_filters=["nbk_id=NBK1"],
        lexical_candidate_count=0,
        unfiltered_lexical_count=10,
    )
    assert "nbk-id-filter-drops-all" in diag.suggestions
