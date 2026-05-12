"""Tests for the table-extraction module."""

from __future__ import annotations

from pathlib import Path

from defusedxml import ElementTree as ET  # noqa: N817 - drop-in replacement for stdlib ET

from genereview_link.corpus.tables import extract_table, render_table_markdown

FIXTURES = Path(__file__).parent.parent / "fixtures" / "nxml"


def test_extract_table_returns_id_caption_header_rows() -> None:
    root = ET.fromstring((FIXTURES / "table_sample.nxml").read_text())
    table = extract_table(root, ordinal=5)
    assert table.table_id == "t5"  # NXML id wins over ordinal
    assert table.caption.startswith("Table 5")
    assert table.header == ["Variant", "Drug", "Min age"]
    assert len(table.rows) == 2
    assert table.rows[0] == ["Class I", "elexacaftor", "6 yrs"]


def test_extract_table_falls_back_to_ordinal_when_no_id() -> None:
    xml = (
        "<table-wrap>"
        "<caption><p>x</p></caption>"
        "<table>"
        "<thead><tr><th>a</th></tr></thead>"
        "<tbody><tr><td>b</td></tr></tbody>"
        "</table>"
        "</table-wrap>"
    )
    root = ET.fromstring(xml)
    table = extract_table(root, ordinal=3)
    assert table.table_id == "table-3"


def test_render_table_markdown_produces_gfm() -> None:
    md = render_table_markdown(
        caption="Table X",
        header=["A", "B"],
        rows=[["1", "2"], ["3", "4"]],
    )
    assert "| A | B |" in md
    assert "| --- | --- |" in md
    assert "| 1 | 2 |" in md
