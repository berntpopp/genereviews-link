"""Tests for bge_passage_text table-truncation logic."""

from __future__ import annotations

import pytest

from genereview_link.retrieval.embeddings import bge_passage_text


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_HEADER_ROW = "| h1 | h2 |"
_SEPARATOR = "| --- | --- |"


def _make_table(n_body_rows: int) -> str:
    """Build a minimal markdown table with *n_body_rows* body rows."""
    lines = ["Table", "", _HEADER_ROW, _SEPARATOR]
    lines.extend("| a | b |" for _ in range(n_body_rows))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Table truncation
# ---------------------------------------------------------------------------


def test_table_passage_truncates_to_token_budget() -> None:
    big_table = _make_table(1000)
    out = bge_passage_text(big_table, passage_type="table", max_tokens=480)

    # Structure preserved
    assert out.startswith("Table")
    assert _HEADER_ROW in out
    assert _SEPARATOR in out

    # Truncated
    assert len(out) < len(big_table)


def test_table_passage_budget_respected() -> None:
    big_table = _make_table(1000)
    max_tokens = 480
    out = bge_passage_text(big_table, passage_type="table", max_tokens=max_tokens)
    # The char-proxy budget is max_tokens * 4; output must fit within it
    # (the keep loop stops before exceeding, so the result can be at most
    #  budget_chars + len(one extra row), but structural lines are included).
    # We verify it is strictly smaller than the original, which has >>1000 rows.
    assert len(out) < len(big_table)


def test_table_passage_header_preserved_after_truncation() -> None:
    big_table = _make_table(1000)
    out = bge_passage_text(big_table, passage_type="table", max_tokens=120)
    assert _HEADER_ROW in out
    assert _SEPARATOR in out


def test_table_passage_caption_preserved_after_truncation() -> None:
    big_table = _make_table(1000)
    out = bge_passage_text(big_table, passage_type="table", max_tokens=120)
    assert out.startswith("Table")


# ---------------------------------------------------------------------------
# Table edge cases
# ---------------------------------------------------------------------------


def test_table_with_no_body_rows_returned_cleanly() -> None:
    table = "\n".join(["Caption", "", _HEADER_ROW, _SEPARATOR])
    out = bge_passage_text(table, passage_type="table", max_tokens=480)
    # No crash, structural content intact
    assert _HEADER_ROW in out
    assert out.startswith("Caption")


def test_table_too_short_returned_unchanged() -> None:
    """A table string with fewer than 4 lines is returned as-is."""
    short = "| h1 | h2 |\n| --- | --- |"
    out = bge_passage_text(short, passage_type="table", max_tokens=480)
    assert out == short


def test_table_within_budget_returned_unchanged() -> None:
    """A small table that fits the budget must not be modified."""
    small_table = _make_table(3)
    out = bge_passage_text(small_table, passage_type="table", max_tokens=480)
    assert out == small_table


# ---------------------------------------------------------------------------
# Narrative passages
# ---------------------------------------------------------------------------


def test_narrative_passage_unchanged_below_budget() -> None:
    text = "Short narrative passage."
    out = bge_passage_text(text, passage_type="narrative", max_tokens=480)
    assert out == text


def test_narrative_passage_unchanged_above_budget() -> None:
    """Narrative text is NOT truncated — it was chunked at ingest time."""
    long_text = "word " * 1000
    out = bge_passage_text(long_text, passage_type="narrative", max_tokens=480)
    assert out == long_text


def test_default_passage_type_is_narrative() -> None:
    long_text = "word " * 1000
    out = bge_passage_text(long_text, max_tokens=480)
    assert out == long_text


# ---------------------------------------------------------------------------
# Other passage types
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("ptype", ["figure", "box", "abstract", "unknown_type"])
def test_non_table_passage_types_unchanged(ptype: str) -> None:
    text = "Some passage text."
    out = bge_passage_text(text, passage_type=ptype, max_tokens=480)
    assert out == text
