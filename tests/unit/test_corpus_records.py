"""Tests for corpus record dataclasses."""

from __future__ import annotations

from datetime import date

from genereview_link.corpus.records import ChapterRecord, PassageRecord


def test_chapter_record_is_frozen() -> None:
    rec = ChapterRecord(
        nbk_id="NBK1247",
        short_name="brca1",
        title="BRCA1- and BRCA2-Associated Hereditary Breast and Ovarian Cancer",
        pubmed_id="20301425",
        gene_symbols=("BRCA1", "BRCA2"),
        omim_ids=("113705", "600185"),
        authors="Petrucelli N, Daly MB, Pal T",
        initial_pub_date=date(1998, 9, 4),
        last_updated_date=date(2023, 9, 21),
        nxml_relpath="gene_NBK1116/brca1.nxml",
        raw_metadata={},
    )
    assert rec.nbk_id == "NBK1247"
    assert "BRCA1" in rec.gene_symbols


def test_passage_record_text_hash_property() -> None:
    rec = PassageRecord(
        nbk_id="NBK1247",
        passage_id="NBK1247:0001",
        chapter_section="summary",
        heading_path="Summary",
        section_level=1,
        chunk_index=0,
        text="The hallmark of hereditary breast and ovarian cancer.",
        char_count=53,
        token_estimate=10,
    )
    assert rec.text_hash.startswith(
        ("0", "1", "2", "3", "4", "5", "6", "7", "8", "9", "a", "b", "c", "d", "e", "f")
    )
    assert len(rec.text_hash) == 64
