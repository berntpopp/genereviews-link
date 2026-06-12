"""Unit tests for genereview_link.api.routes.table_enrichment.table_fields."""

from __future__ import annotations

from datetime import date

from genereview_link.api.routes.table_enrichment import table_fields
from genereview_link.retrieval.repository import PassageRow


def _table_row(
    *,
    passage_type: str = "table",
    table_data: dict | None = None,
    text: str = "| Gene | Phenotype |\n| --- | --- |\n| BRCA1 | HBOC |",
) -> PassageRow:
    return PassageRow(
        nbk_id="NBK1247",
        passage_id="NBK1247:0099",
        chapter_section="management",
        heading_path="Management > Table 1",
        section_level=2,
        chunk_index=99,
        text=text,
        chapter_title="BRCA1- and BRCA2-Associated HBOC",
        chapter_last_updated=date(2025, 12, 1),
        gene_symbols=("BRCA1",),
        passage_type=passage_type,
        table_data=table_data,
    )


def _narrative_row() -> PassageRow:
    return PassageRow(
        nbk_id="NBK1247",
        passage_id="NBK1247:0010",
        chapter_section="management",
        heading_path="Management > Overview",
        section_level=2,
        chunk_index=10,
        text="Risk-reducing surgery is recommended for BRCA1 carriers.",
        chapter_title="BRCA1- and BRCA2-Associated HBOC",
        chapter_last_updated=date(2025, 12, 1),
        gene_symbols=("BRCA1",),
        passage_type="narrative",
        table_data=None,
    )


# ---------------------------------------------------------------------------
# want=False — always null regardless of passage type or table_data
# ---------------------------------------------------------------------------


def test_table_fields_want_false_table_row() -> None:
    """When want=False, all three fields are None even for table passages with data."""
    row = _table_row(
        table_data={
            "header": ["Gene", "Phenotype"],
            "rows": [["BRCA1", "HBOC"]],
            "caption": "Table 1",
        }
    )
    result = table_fields(row, want=False)
    assert result == {"header": None, "rows": None, "markdown_table": None}


# ---------------------------------------------------------------------------
# narrative passage — always null
# ---------------------------------------------------------------------------


def test_table_fields_narrative_passage_want_true() -> None:
    """Narrative passages return all None even when want=True."""
    result = table_fields(_narrative_row(), want=True)
    assert result == {"header": None, "rows": None, "markdown_table": None}


# ---------------------------------------------------------------------------
# table passage, want=True, valid data
# ---------------------------------------------------------------------------


def test_table_fields_valid_table_data_populates_all_three() -> None:
    """Valid table_data with matching widths populates header, rows, markdown_table."""
    row = _table_row(
        table_data={
            "caption": "Gene-phenotype correlations",
            "header": ["Gene", "Phenotype"],
            "rows": [["BRCA1", "HBOC"], ["BRCA2", "HBOC/PC"]],
        }
    )
    result = table_fields(row, want=True)

    assert result["header"] == ["Gene", "Phenotype"]
    assert result["rows"] == [["BRCA1", "HBOC"], ["BRCA2", "HBOC/PC"]]
    assert result["markdown_table"] is not None
    md = result["markdown_table"]
    assert "Gene" in md
    assert "Phenotype" in md
    assert "BRCA1" in md
    assert "---" in md


def test_table_fields_markdown_includes_caption() -> None:
    """The markdown_table begins with the caption text."""
    row = _table_row(
        table_data={
            "caption": "My Caption",
            "header": ["A", "B"],
            "rows": [["x", "y"]],
        }
    )
    result = table_fields(row, want=True)
    assert result["markdown_table"] is not None
    assert result["markdown_table"].startswith("My Caption")


# ---------------------------------------------------------------------------
# width invariant — mismatched row widths
# ---------------------------------------------------------------------------


def test_table_fields_mismatched_width_returns_all_null() -> None:
    """If any row has a different cell count than the header, all three fields are None."""
    row = _table_row(
        table_data={
            "caption": "Bad table",
            "header": ["A", "B", "C"],
            "rows": [["x", "y"]],  # 2 cells, header has 3
        }
    )
    result = table_fields(row, want=True)
    assert result == {"header": None, "rows": None, "markdown_table": None}


def test_table_fields_mixed_width_rows_all_null() -> None:
    """If SOME rows are width-correct but one is not, all fields are None."""
    row = _table_row(
        table_data={
            "caption": "Partial",
            "header": ["A", "B"],
            "rows": [["x", "y"], ["z"]],  # second row too short
        }
    )
    result = table_fields(row, want=True)
    assert result == {"header": None, "rows": None, "markdown_table": None}


# ---------------------------------------------------------------------------
# edge cases: missing or malformed table_data
# ---------------------------------------------------------------------------


def test_table_fields_no_table_data_returns_null() -> None:
    """table_data=None on a table passage → all None."""
    row = _table_row(table_data=None)
    result = table_fields(row, want=True)
    assert result == {"header": None, "rows": None, "markdown_table": None}


def test_table_fields_empty_table_data_returns_null() -> None:
    """table_data={} on a table passage → all None."""
    row = _table_row(table_data={})
    result = table_fields(row, want=True)
    assert result == {"header": None, "rows": None, "markdown_table": None}


def test_table_fields_missing_header_key_returns_null() -> None:
    """table_data without 'header' key → all None."""
    row = _table_row(table_data={"rows": [["a", "b"]], "caption": "C"})
    result = table_fields(row, want=True)
    assert result == {"header": None, "rows": None, "markdown_table": None}


def test_table_fields_missing_rows_key_returns_null() -> None:
    """table_data without 'rows' key → all None."""
    row = _table_row(table_data={"header": ["A", "B"], "caption": "C"})
    result = table_fields(row, want=True)
    assert result == {"header": None, "rows": None, "markdown_table": None}


def test_table_fields_empty_rows_allowed() -> None:
    """An empty rows list with a valid header is permissible."""
    row = _table_row(
        table_data={
            "caption": "",
            "header": ["A", "B"],
            "rows": [],
        }
    )
    result = table_fields(row, want=True)
    assert result["header"] == ["A", "B"]
    assert result["rows"] == []
    assert result["markdown_table"] is not None
