"""Tests for the BITS NXML parser."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from genereview_link.corpus.nxml import NxmlParseError, parse_and_chunk_one

FIXTURES = Path(__file__).parent.parent / "fixtures" / "nxml"


@pytest.mark.slow
def test_typical_chapter_yields_record_and_passages() -> None:
    raw = (FIXTURES / "typical.nxml").read_bytes()
    chapter, passages, _audit = parse_and_chunk_one(
        raw, nbk_id="NBK1247", short_name="brca1", nxml_relpath="gene_NBK1116/brca1.nxml"
    )
    assert chapter.nbk_id == "NBK1247"
    assert chapter.pubmed_id == "20301425"
    assert chapter.title.startswith("BRCA1")
    assert chapter.last_updated_date == date(2023, 9, 21)
    assert chapter.initial_pub_date == date(1998, 9, 4)
    assert "Petrucelli" in (chapter.authors or "")

    sections = {p.chapter_section for p in passages}
    assert {"summary", "diagnosis", "management"} <= sections
    # heading_path includes nesting
    diag_subs = [p for p in passages if "Establishing" in (p.heading_path or "")]
    assert diag_subs and diag_subs[0].section_level >= 2


@pytest.mark.slow
def test_missing_pubdate_does_not_crash() -> None:
    raw = (FIXTURES / "missing_pubdate.nxml").read_bytes()
    chapter, _, _ = parse_and_chunk_one(
        raw, nbk_id="NBK9999", short_name="nopub", nxml_relpath="x.nxml"
    )
    assert chapter.last_updated_date is None
    assert chapter.initial_pub_date is None


def test_malformed_raises() -> None:
    raw = (FIXTURES / "malformed.nxml").read_bytes()
    with pytest.raises(NxmlParseError):
        parse_and_chunk_one(raw, nbk_id="NBKBAD", short_name="bad", nxml_relpath="bad.nxml")


def test_nxml_parser_prefers_updated_over_revised_when_both_present() -> None:
    """updated must win over revised in <pub-history>; revised is a schema-metadata
    timestamp, updated is the editorial-content timestamp (B1 findings 2026-05-12).
    """
    raw = (FIXTURES / "pub_history_both_dates.nxml").read_bytes()
    chapter, _, _ = parse_and_chunk_one(
        raw,
        nbk_id="NBK1440",
        short_name="test_pub_history",
        nxml_relpath="gene_NBK1116/test_pub_history.nxml",
    )
    # updated=2024-04-11 must win over revised=2005-07-13
    assert chapter.last_updated_date == date(2024, 4, 11)
    assert chapter.initial_pub_date == date(2000, 4, 3)


def test_nxml_parser_null_when_only_created_present() -> None:
    """Chapters with only <date date-type='created'> must have last_updated_date=None.

    Neither 'updated' nor 'revised' is present, so the parser must not fall
    back to 'created' (which is the initial publication date, not an editorial
    update).
    """
    raw = (FIXTURES / "pub_history_created_only.nxml").read_bytes()
    chapter, _, _ = parse_and_chunk_one(
        raw,
        nbk_id="NBK619577",
        short_name="test_pub_history_created_only",
        nxml_relpath="gene_NBK1116/test_pub_history_created_only.nxml",
    )
    assert chapter.last_updated_date is None
    assert chapter.initial_pub_date == date(2025, 12, 4)
