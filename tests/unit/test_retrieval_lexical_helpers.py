"""Tests for _recall_terms and _recall_tsquery."""

from __future__ import annotations

from genereview_link.retrieval.lexical import recall_terms, recall_tsquery


def test_recall_terms_lowers_and_dedupes() -> None:
    out = recall_terms("BRCA1 BRCA1 tumor SUPPRESSOR")
    assert "brca1" in out
    assert out.count("brca1") == 1
    assert "tumor" in out


def test_recall_terms_drops_short_tokens() -> None:
    out = recall_terms("a is the cat")
    assert "a" not in out
    assert "is" not in out
    assert "the" not in out
    assert "cat" in out


def test_recall_tsquery_joins_with_or() -> None:
    q = recall_tsquery("BRCA1 tumor")
    assert "|" in q
    assert "brca1" in q.lower()


def test_recall_tsquery_empty_returns_safe_string() -> None:
    q = recall_tsquery("")
    assert q  # safe, parseable
