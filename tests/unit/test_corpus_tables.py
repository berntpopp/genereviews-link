"""Tests for the table-extraction module."""

from __future__ import annotations

from pathlib import Path

from defusedxml import ElementTree as ET  # noqa: N817 - drop-in replacement for stdlib ET

from genereview_link.corpus.tables import extract_table, render_table_markdown

FIXTURES = Path(__file__).parent.parent / "fixtures" / "nxml"


def load_fixture(name: str):
    return ET.fromstring((FIXTURES / name).read_text())


def test_extract_table_returns_id_caption_header_rows() -> None:
    root = load_fixture("table_sample.nxml")
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


def test_rowspan_propagates_only_declared_rows() -> None:
    table = extract_table(load_fixture("table_with_rowspan.nxml"), ordinal=1)
    assert table.rows[:4] == [
        ["Breast cancer", "Self-exam", "Monthly"],
        ["Breast cancer", "Clinical exam", "Every 6-12 months"],
        ["Breast cancer", "Mammogram", "Annually"],
        ["Breast cancer", "MRI", "Annually"],
    ]
    assert table.rows[4][0] == "Ovarian cancer"


def test_colspan_expands_cells() -> None:
    table = extract_table(load_fixture("table_with_rowspan.nxml"), ordinal=1)
    assert table.rows[4] == ["Ovarian cancer", "No effective screening", "No effective screening"]


def test_mixed_th_td_preserves_source_order() -> None:
    table = extract_table(load_fixture("table_with_rowspan.nxml"), ordinal=1)
    assert table.rows[5] == ["A", "B", "C"]
