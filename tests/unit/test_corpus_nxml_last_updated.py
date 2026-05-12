"""Regression test: last_updated_date must be extracted from pub-type="last-revision".

Production NCBI NXMLs use pub-type="last-revision" rather than pub-type="updated".
This test ensures parse_and_chunk_one correctly reads the last-revision element.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from genereview_link.corpus.nxml import parse_and_chunk_one

FIXTURES = Path(__file__).parent.parent / "fixtures" / "nxml"


@pytest.mark.slow
def test_parse_chapter_extracts_last_updated_date_from_last_revision() -> None:
    """last_updated_date must come from pub-date[@pub-type='last-revision']."""
    raw = (FIXTURES / "last_updated_sample.nxml").read_bytes()
    chapter, _, _ = parse_and_chunk_one(
        raw, nbk_id="NBK_TEST", short_name="test_last_rev", nxml_relpath="test.nxml"
    )
    assert chapter.last_updated_date == date(2023, 9, 14)
    assert chapter.initial_pub_date == date(2000, 1, 1)
