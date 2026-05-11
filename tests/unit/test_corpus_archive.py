"""Tests for archive metadata parsing (offline)."""

from __future__ import annotations

from genereview_link.corpus.archive import parse_file_list_row


def test_parse_nbk1116_row() -> None:
    row = 'ca/84/gene_NBK1116.tar.gz,GeneReviews(R),"University of Washington, Seattle",1993,NBK1116,2026-05-10 03:32:37'
    parsed = parse_file_list_row(row)
    assert parsed is not None
    assert parsed.nbk_id == "NBK1116"
    assert parsed.last_updated == "2026-05-10 03:32:37"
    assert parsed.relpath == "ca/84/gene_NBK1116.tar.gz"


def test_unrelated_row_returns_none() -> None:
    row = "aa/01/other.tar.gz,Other Book,Author,2020,NBK9999,2024-01-01 00:00:00"
    parsed = parse_file_list_row(row, nbk_filter="NBK1116")
    assert parsed is None
