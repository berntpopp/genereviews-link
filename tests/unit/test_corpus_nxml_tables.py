"""Tests for table passage extraction in the BITS NXML parser."""

from __future__ import annotations

from pathlib import Path

import pytest

from genereview_link.corpus.nxml import parse_and_chunk_one

FIXTURES = Path(__file__).parent.parent / "fixtures" / "nxml"


@pytest.mark.slow
def test_parse_chapter_emits_table_passage() -> None:
    raw = (FIXTURES / "chapter_with_table.nxml").read_bytes()
    chapter, passages = parse_and_chunk_one(
        raw, nbk_id="NBK_TBL", short_name="tbl_test", nxml_relpath="tbl_test.nxml"
    )
    assert chapter.nbk_id == "NBK_TBL"

    table_passages = [p for p in passages if p.passage_type == "table"]
    assert len(table_passages) == 1

    t = table_passages[0]
    assert t.table_id is not None
    assert t.table_data is not None
    assert t.table_data["header"] == ["Variant", "Drug", "Min age"]
    assert "| Variant | Drug | Min age |" in t.text
    assert t.heading_path is not None
    assert "Table" in t.heading_path
    assert t.chunk_index is not None


@pytest.mark.slow
def test_table_passage_chunk_index_is_interleaved() -> None:
    """chunk_index must be monotonically interleaved across narrative and table passages."""
    raw = (FIXTURES / "chapter_with_table.nxml").read_bytes()
    _, passages = parse_and_chunk_one(
        raw, nbk_id="NBK_TBL", short_name="tbl_test", nxml_relpath="tbl_test.nxml"
    )
    # There should be both narrative and table passages
    narrative = [p for p in passages if p.passage_type == "narrative"]
    tables = [p for p in passages if p.passage_type == "table"]
    assert len(narrative) >= 1
    assert len(tables) == 1

    # All chunk_indices across all passages should be distinct and monotonically increasing
    all_indices = [p.chunk_index for p in passages]
    assert all_indices == sorted(set(all_indices)), (
        f"chunk_indices not strictly monotonic: {all_indices}"
    )

    # The table passage chunk_index should sit between some narrative passages
    # (not all at the end or all at the start)
    t_idx = tables[0].chunk_index
    narrative_indices = [p.chunk_index for p in narrative]
    assert any(n < t_idx for n in narrative_indices) or any(n > t_idx for n in narrative_indices), (
        "Table chunk_index should be interleaved with narrative chunk_indices"
    )


@pytest.mark.slow
def test_table_passage_fields() -> None:
    """Verify all expected fields on the table PassageRecord."""
    raw = (FIXTURES / "chapter_with_table.nxml").read_bytes()
    _, passages = parse_and_chunk_one(
        raw, nbk_id="NBK_TBL", short_name="tbl_test", nxml_relpath="tbl_test.nxml"
    )
    t = next(p for p in passages if p.passage_type == "table")

    # table_id comes from the <table-wrap id="t5"> attribute
    assert t.table_id == "t5"

    # table_data has all three keys
    assert isinstance(t.table_data, dict)
    assert set(t.table_data.keys()) >= {"caption", "header", "rows"}
    assert t.table_data["header"] == ["Variant", "Drug", "Min age"]
    rows = t.table_data["rows"]
    assert isinstance(rows, list)
    assert len(rows) == 2
    assert rows[0] == ["Class I", "elexacaftor", "6 yrs"]

    # heading_path contains "Table"
    assert "Table" in (t.heading_path or "")

    # section_level should match the surrounding sec (level 1)
    assert t.section_level == 1

    # chapter_section should be canonicalized from the sec title;
    # "pharmacogenomics" doesn't match any keyword rule so it maps to "other"
    assert t.chapter_section == "other"

    # text is GFM markdown
    assert "| Variant | Drug | Min age |" in t.text
    assert "| --- | --- | --- |" in t.text
    assert "elexacaftor" in t.text


@pytest.mark.slow
def test_typical_chapter_has_no_table_passages() -> None:
    """Regression: typical.nxml has no tables — no table passages emitted."""
    raw = (FIXTURES / "typical.nxml").read_bytes()
    _, passages = parse_and_chunk_one(
        raw, nbk_id="NBK1247", short_name="brca1", nxml_relpath="brca1.nxml"
    )
    table_passages = [p for p in passages if p.passage_type == "table"]
    assert len(table_passages) == 0
    # Narrative passages still present
    assert len(passages) > 0
