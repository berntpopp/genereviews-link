"""ResponseMeta + envelope models for /passages/search and /chapters/.../sections/..."""

from __future__ import annotations

from datetime import date

from genereview_link.models.genereview_models import (
    ATTRIBUTION_TEXT,
    COPYRIGHT_LINE,
    ChapterSectionResponse,
    LicenseNotice,
    PassageDetail,
    PassageSearchResponse,
    PassageWindowResponse,
    ResponseMeta,
)


def test_attribution_text_uses_present_not_year():
    assert "1993–present" in ATTRIBUTION_TEXT  # noqa: RUF001


def test_response_meta_default_attribution_matches_constant():
    m = ResponseMeta()
    assert m.attribution == ATTRIBUTION_TEXT
    assert m.corpus_version is None


def test_response_meta_corpus_version_round_trip():
    m = ResponseMeta(corpus_version="2026-04-01")
    dumped = m.model_dump()
    assert dumped["corpus_version"] == "2026-04-01"
    assert dumped["attribution"] == ATTRIBUTION_TEXT


def test_passage_search_response_meta_alias_is_underscore_meta():
    r = PassageSearchResponse(results=[])
    dumped = r.model_dump(by_alias=True)
    assert "_meta" in dumped
    assert "meta" not in dumped


def test_chapter_section_response_meta_alias_is_underscore_meta() -> None:
    r = ChapterSectionResponse(
        nbk_id="NBK1",
        chapter_title="Test",
        chapter_section="management",
        chapter_last_updated=None,
        passages=[],
        passage_count=0,
    )
    dumped = r.model_dump(by_alias=True)
    assert "_meta" in dumped
    assert "meta" not in dumped


def test_passage_window_response_meta_alias_is_underscore_meta() -> None:
    detail = PassageDetail(
        passage_id="p1",
        nbk_id="NBK1",
        chapter_title="Test",
        chapter_last_updated=date(2024, 1, 1),
        chapter_section="management",
        heading_path=None,
        section_level=1,
        chunk_index=0,
        text="hello",
        char_count=5,
        recommended_citation="Test. NBK1. Updated 2024-01-01. Passage p1.",
        source_url="https://www.ncbi.nlm.nih.gov/books/NBK1/",
    )
    r = PassageWindowResponse(passage=detail)
    dumped = r.model_dump(by_alias=True)
    assert "_meta" in dumped
    assert "meta" not in dumped
    assert dumped["neighbors_before"] == []
    assert dumped["neighbors_after"] == []
    assert dumped["has_more_before"] is False
    assert dumped["has_more_after"] is False


def test_license_notice_and_attribution_share_copyright_year():
    notice = LicenseNotice()
    assert "1993" in notice.copyright
    assert notice.copyright == COPYRIGHT_LINE
    assert COPYRIGHT_LINE in ATTRIBUTION_TEXT
