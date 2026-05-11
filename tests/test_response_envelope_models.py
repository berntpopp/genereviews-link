"""ResponseMeta + envelope models for /passages/search and /chapters/.../sections/..."""

from __future__ import annotations

from genereview_link.models.genereview_models import (
    ATTRIBUTION_TEXT,
    ChapterSectionResponse,  # noqa: F401  # re-exported smoke import
    LicenseNotice,
    PassageSearchResponse,
    ResponseMeta,
)


def test_attribution_text_uses_present_not_year():
    assert "1993–present" in ATTRIBUTION_TEXT  # noqa: RUF001


def test_response_meta_default_attribution_matches_constant():
    m = ResponseMeta()
    assert m.attribution == ATTRIBUTION_TEXT
    assert m.corpus_version is None


def test_passage_search_response_meta_alias_is_underscore_meta():
    r = PassageSearchResponse(results=[])
    dumped = r.model_dump(by_alias=True)
    assert "_meta" in dumped
    assert "meta" not in dumped


def test_license_notice_and_attribution_share_copyright_year():
    notice = LicenseNotice()
    assert "1993" in notice.copyright
