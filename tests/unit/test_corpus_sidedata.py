"""Tests for side-data parsing."""

from __future__ import annotations

from pathlib import Path

from genereview_link.corpus.sidedata import SideData, load_sidedata

FIXTURES = Path(__file__).parent.parent / "fixtures" / "sidedata"


def test_gene_symbols_aggregate_per_nbk() -> None:
    sd = load_sidedata(FIXTURES)
    assert sd.gene_symbols["NBK1247"] == ("BRCA1", "BRCA2")
    assert sd.gene_symbols["NBK1311"] == ("HTT",)


def test_omim_ids_aggregate_per_nbk() -> None:
    sd = load_sidedata(FIXTURES)
    assert sd.omim_ids["NBK1247"] == ("113705", "600185")


def test_short_name_lookup() -> None:
    sd = load_sidedata(FIXTURES)
    assert sd.short_name_by_nbk["NBK1247"] == "brca1"


def test_missing_nbk_returns_empty_tuple() -> None:
    sd = load_sidedata(FIXTURES)
    assert sd.gene_symbols.get("NBKMISSING", ()) == ()
