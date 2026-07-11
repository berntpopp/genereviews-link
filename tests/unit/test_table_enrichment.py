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


_NULL = {"header": None, "rows": None}


def _cell_texts(fenced: list) -> list[str]:
    return [c.text for c in fenced]


def test_table_fields_want_false_table_row() -> None:
    """When want=False, both fields are None even for table passages with data."""
    row = _table_row(
        table_data={
            "header": ["Gene", "Phenotype"],
            "rows": [["BRCA1", "HBOC"]],
            "caption": "Table 1",
        }
    )
    assert table_fields(row, want=False) == _NULL


# ---------------------------------------------------------------------------
# narrative passage — always null
# ---------------------------------------------------------------------------


def test_table_fields_narrative_passage_want_true() -> None:
    """Narrative passages return None even when want=True."""
    assert table_fields(_narrative_row(), want=True) == _NULL


# ---------------------------------------------------------------------------
# table passage, want=True, valid data → v1.1-fenced cells
# ---------------------------------------------------------------------------


def test_table_fields_valid_table_data_populates_fenced_cells() -> None:
    """Valid table_data populates header/rows as v1.1-fenced untrusted_text cells."""
    row = _table_row(
        table_data={
            "caption": "Gene-phenotype correlations",
            "header": ["Gene", "Phenotype"],
            "rows": [["BRCA1", "HBOC"], ["BRCA2", "HBOC/PC"]],
        }
    )
    result = table_fields(row, want=True)

    assert result["header"][0].kind == "untrusted_text"
    assert _cell_texts(result["header"]) == ["Gene", "Phenotype"]
    assert [_cell_texts(r) for r in result["rows"]] == [["BRCA1", "HBOC"], ["BRCA2", "HBOC/PC"]]
    # record_id is rooted at {nbk_id}#table:{table_id}
    assert result["header"][0].provenance.record_id.startswith("NBK1247#table:")
    # markdown_table was dropped (duplicated the now-fenced cells)
    assert "markdown_table" not in result


def test_table_fields_cell_record_ids_are_coordinate_precise() -> None:
    """Each cell's record_id carries its header/row coordinate for audit precision."""
    row = _table_row(table_data={"caption": "C", "header": ["A", "B"], "rows": [["x", "y"]]})
    result = table_fields(row, want=True)
    assert result["header"][1].provenance.record_id.endswith("#h1")
    assert result["rows"][0][1].provenance.record_id.endswith("#r0c1")


# ---------------------------------------------------------------------------
# width invariant — mismatched row widths
# ---------------------------------------------------------------------------


def test_table_fields_mismatched_width_returns_all_null() -> None:
    """If any row has a different cell count than the header, both fields are None."""
    row = _table_row(
        table_data={
            "caption": "Bad table",
            "header": ["A", "B", "C"],
            "rows": [["x", "y"]],  # 2 cells, header has 3
        }
    )
    assert table_fields(row, want=True) == _NULL


def test_table_fields_mixed_width_rows_all_null() -> None:
    """If SOME rows are width-correct but one is not, both fields are None."""
    row = _table_row(
        table_data={
            "caption": "Partial",
            "header": ["A", "B"],
            "rows": [["x", "y"], ["z"]],  # second row too short
        }
    )
    assert table_fields(row, want=True) == _NULL


# ---------------------------------------------------------------------------
# edge cases: missing or malformed table_data
# ---------------------------------------------------------------------------


def test_table_fields_no_table_data_returns_null() -> None:
    """table_data=None on a table passage → both None."""
    assert table_fields(_table_row(table_data=None), want=True) == _NULL


def test_table_fields_empty_table_data_returns_null() -> None:
    """table_data={} on a table passage → both None."""
    assert table_fields(_table_row(table_data={}), want=True) == _NULL


def test_table_fields_missing_header_key_returns_null() -> None:
    """table_data without 'header' key → both None."""
    row = _table_row(table_data={"rows": [["a", "b"]], "caption": "C"})
    assert table_fields(row, want=True) == _NULL


def test_table_fields_missing_rows_key_returns_null() -> None:
    """table_data without 'rows' key → both None."""
    row = _table_row(table_data={"header": ["A", "B"], "caption": "C"})
    assert table_fields(row, want=True) == _NULL


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
    assert _cell_texts(result["header"]) == ["A", "B"]
    assert result["rows"] == []
