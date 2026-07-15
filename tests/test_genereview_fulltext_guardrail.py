"""#37 guardrail tests for GET /genereview/{gene_symbol}.

Covers two behavior changes introduced by the Group B ergonomics bundle:

1. ``include_fulltext`` default is False — the default response is lean.
2. ``max_chars`` (default 16000, ge=0, le=200000) caps fulltext payload size
   when ``include_fulltext=true``; truncated responses set ``_meta.truncated``
   and surface ``_meta.next_commands -> get_chapter_section``.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from genereview_link.config import ServerConfig
from genereview_link.mcp.untrusted_content import fence_untrusted_text
from genereview_link.models.genereview_models import (
    FencedGeneReviewSection,
    FullTextData,
    FullTextMetadata,
    GeneReview,
    GeneReviewSection,
)
from genereview_link.server_manager import UnifiedServerManager
from genereview_link.services.genereview_service import fence_section_prose
from genereview_link.services.service_manager import get_managed_service


def _make_section(title: str, content: str) -> FencedGeneReviewSection:
    # Build via the production helper so _raw_content is populated (truncation
    # slices the RAW content), and title/content are fenced consistently.
    return fence_section_prose(
        GeneReviewSection(title=title, content=content),
        doc_id="NBK1247",
        record_path=f"section:{title.lower()}",
    )


def _make_genereview(
    *,
    summary_chars: int = 0,
    diagnosis_chars: int = 0,
    management_chars: int = 0,
    other_section_chars: dict[str, int] | None = None,
    book_url: str = "https://www.ncbi.nlm.nih.gov/books/NBK1247/",
) -> GeneReview:
    """Construct a GeneReview with predictable section sizes for truncation tests."""
    summary = _make_section("Summary", "S" * summary_chars) if summary_chars else None
    diagnosis = _make_section("Diagnosis", "D" * diagnosis_chars) if diagnosis_chars else None
    management = _make_section("Management", "M" * management_chars) if management_chars else None
    other = {
        key: _make_section(key.title(), "O" * size)
        for key, size in (other_section_chars or {}).items()
    }
    full_text_data = FullTextData(
        nbk_id="NBK1247",
        url=book_url,
        title=None,
        sections={},
        metadata=FullTextMetadata(),
    )
    return GeneReview(
        gene_symbol="BRCA1",
        pubmed_id="20301425",
        book_url=book_url,
        title=fence_untrusted_text("Test Chapter", source="genereviews", record_id="NBK1247#title"),
        summary=summary,
        diagnosis=diagnosis,
        management=management,
        other_sections=other,
        full_text_data=full_text_data,
    )


def _build_app_with_service(payloads: dict[str, GeneReview]) -> FastAPI:
    """Build a FastAPI app whose GeneReviewService is replaced with a deterministic fake.

    ``payloads`` keys: ``indexed`` (returned by get_genereview_comprehensive_indexed)
    and ``uncached`` (returned by get_genereview_comprehensive_uncached). Both
    methods return a fresh deep-copy via ``model_copy(deep=True)`` so the route
    can mutate sections without leaking state across requests.
    """
    config = ServerConfig(transport="http", log_level="WARNING", enable_docs=False)
    manager = UnifiedServerManager()
    app = manager.create_fastapi_app(config)

    class FakeService:
        async def get_genereview_comprehensive_indexed(
            self, *args: Any, **kwargs: Any
        ) -> GeneReview:
            return payloads["indexed"].model_copy(deep=True)

        async def get_genereview_comprehensive_uncached(
            self, *args: Any, **kwargs: Any
        ) -> GeneReview:
            return payloads["uncached"].model_copy(deep=True)

    async def _get_service() -> Any:
        yield FakeService()

    app.dependency_overrides[get_managed_service] = _get_service

    # The route always resolves the gene to a corpus chapter first (issue #106 D1),
    # so a defining chapter must be present for the service to be reached.
    class _FakeChapter:
        nbk_id = "NBK1247"
        short_name = "brca1"
        title = "BRCA1- and BRCA2-Associated HBOC"
        pubmed_id = "20301425"
        gene_symbols = ("BRCA1",)

    class _FakeRepo:
        async def get_defining_chapter_by_gene(self, gene_symbol: str) -> _FakeChapter:
            return _FakeChapter()

    app.state.repository = _FakeRepo()
    return app


@pytest.mark.asyncio
async def test_default_response_is_lean() -> None:
    """Default flip: include_fulltext=False, so sections come back empty/None."""
    # Service is called with include_fulltext=False; we model that by returning a
    # GeneReview that already has no section content (matches real service behavior
    # when include_fulltext=False short-circuits the scrape).
    lean = GeneReview(
        gene_symbol="BRCA1",
        pubmed_id="20301425",
        book_url="https://www.ncbi.nlm.nih.gov/books/NBK1247/",
        title=fence_untrusted_text("Test Chapter", source="genereviews", record_id="NBK1247#title"),
    )
    app = _build_app_with_service({"indexed": lean, "uncached": lean})
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get("/genereview/BRCA1?fresh=true")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["gene_symbol"] == "BRCA1"
    assert body["summary"] is None
    assert body["diagnosis"] is None
    assert body["management"] is None
    assert body["other_sections"] == {}
    assert body["_meta"].get("truncated", False) is False
    assert "next_commands" not in body["_meta"]


@pytest.mark.asyncio
async def test_include_fulltext_below_default_cap_is_not_truncated() -> None:
    """include_fulltext=true with total chars < 16000 returns the full payload."""
    payload = _make_genereview(
        summary_chars=1000,
        diagnosis_chars=2000,
        management_chars=3000,
    )
    app = _build_app_with_service({"indexed": payload, "uncached": payload})
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get("/genereview/BRCA1?include_fulltext=true&fresh=true")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["summary"]["content"]["text"]) == 1000
    assert len(body["diagnosis"]["content"]["text"]) == 2000
    assert len(body["management"]["content"]["text"]) == 3000
    assert body["_meta"].get("truncated", False) is False
    assert "next_commands" not in body["_meta"]


@pytest.mark.asyncio
async def test_max_chars_zero_disables_cap() -> None:
    """max_chars=0 means no cap: a 50k-char payload is returned in full."""
    payload = _make_genereview(
        summary_chars=10000,
        diagnosis_chars=20000,
        management_chars=20000,
    )
    app = _build_app_with_service({"indexed": payload, "uncached": payload})
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get("/genereview/BRCA1?include_fulltext=true&max_chars=0&fresh=true")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["summary"]["content"]["text"]) == 10000
    assert len(body["diagnosis"]["content"]["text"]) == 20000
    assert len(body["management"]["content"]["text"]) == 20000
    assert body["_meta"].get("truncated", False) is False
    assert "next_commands" not in body["_meta"]


@pytest.mark.asyncio
async def test_small_max_chars_forces_truncation_and_stamps_meta() -> None:
    """max_chars=100 cuts off all but the first 100 chars across sections."""
    payload = _make_genereview(
        summary_chars=80,
        diagnosis_chars=80,
        management_chars=80,
        other_section_chars={"references": 80},
    )
    app = _build_app_with_service({"indexed": payload, "uncached": payload})
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get("/genereview/BRCA1?include_fulltext=true&max_chars=100&fresh=true")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # Greedy walk: summary (80) fits fully; diagnosis gets only 20 chars; remainder cleared.
    assert len(body["summary"]["content"]["text"]) == 80
    assert len(body["diagnosis"]["content"]["text"]) == 20
    assert body["management"]["content"]["text"] == ""
    assert body["other_sections"]["references"]["content"]["text"] == ""
    # Total kept content must not exceed the cap.
    total_kept = (
        len(body["summary"]["content"]["text"])
        + len(body["diagnosis"]["content"]["text"])
        + len(body["management"]["content"]["text"])
        + sum(len(s["content"]["text"]) for s in body["other_sections"].values())
    )
    assert total_kept <= 100
    # _meta.truncated + next_commands -> get_chapter_section with nbk_id only.
    assert body["_meta"]["truncated"] is True
    next_commands = body["_meta"]["next_commands"]
    assert isinstance(next_commands, list) and len(next_commands) == 1
    assert next_commands[0]["tool"] == "get_chapter_section"
    args = next_commands[0]["arguments"]
    assert args.get("nbk_id") == "NBK1247"
    # Risk Notes: do NOT hardcode a section. Only nbk_id is surfaced.
    assert "section" not in args


@pytest.mark.asyncio
async def test_truncation_digest_hashes_raw_pre_normalization_bytes() -> None:
    """Regression: truncation must fence the RAW upstream slice, so raw_sha256 is
    over the pre-normalization bytes — not the already-NFC-normalized .text."""
    import hashlib
    import unicodedata

    # Decomposed é (e + U+0301 combining acute) — NFC normalization changes bytes.
    raw = "e\u0301" + "X" * 60
    section = fence_section_prose(
        GeneReviewSection(title="Summary", content=raw),
        doc_id="NBK1247",
        record_path="section:summary",
    )
    payload = GeneReview(
        gene_symbol="BRCA1",
        pubmed_id="20301425",
        book_url="https://www.ncbi.nlm.nih.gov/books/NBK1247/",
        title=fence_untrusted_text("T", source="genereviews", record_id="NBK1247#title"),
        summary=section,
    )
    app = _build_app_with_service({"indexed": payload, "uncached": payload})
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get("/genereview/BRCA1?include_fulltext=true&max_chars=10&fresh=true")
    assert resp.status_code == 200, resp.text
    fenced = resp.json()["summary"]["content"]
    raw_slice = raw[:10]
    # raw_sha256 hashes the RAW slice's bytes...
    assert fenced["raw_sha256"] == hashlib.sha256(raw_slice.encode("utf-8")).hexdigest()
    # ...NOT the normalized text's bytes (the bug the review caught).
    norm_slice = unicodedata.normalize("NFC", raw)[:10]
    assert fenced["raw_sha256"] != hashlib.sha256(norm_slice.encode("utf-8")).hexdigest()
    # The emitted text is still NFC-normalized.
    assert fenced["text"] == unicodedata.normalize("NFC", raw_slice)


def _build_app_with_shared_service(
    payload: GeneReview,
    *,
    recorded: list[dict[str, Any]],
    route_via_indexed: bool = True,
) -> FastAPI:
    """Build a FastAPI app whose service returns the SAME payload instance every call.

    Mirrors the production alru_cache behavior: the cached impl hands the same
    object reference to every caller. Used to exercise the route's deep-copy
    guard against in-place mutation of cached results.

    ``recorded`` collects kwargs from each service call so tests can assert on
    what the route forwarded (e.g. include_fulltext=False on default).

    When ``route_via_indexed=True`` (default), installs a fake repository on
    ``app.state.repository`` so the route resolves the indexed code path; this
    is the cached path in production and the one whose mutation regression is
    being verified.
    """
    config = ServerConfig(transport="http", log_level="WARNING", enable_docs=False)
    manager = UnifiedServerManager()
    app = manager.create_fastapi_app(config)

    class SharedService:
        async def get_genereview_comprehensive_indexed(
            self, *args: Any, **kwargs: Any
        ) -> GeneReview:
            recorded.append({"method": "indexed", "kwargs": dict(kwargs)})
            return payload

        async def get_genereview_comprehensive_uncached(
            self, *args: Any, **kwargs: Any
        ) -> GeneReview:
            recorded.append({"method": "uncached", "kwargs": dict(kwargs)})
            return payload

    async def _get_service() -> Any:
        yield SharedService()

    app.dependency_overrides[get_managed_service] = _get_service

    if route_via_indexed:
        from datetime import date
        from unittest.mock import AsyncMock

        from genereview_link.retrieval.repository import ChapterRow

        chapter = ChapterRow(
            nbk_id="NBK1247",
            short_name="NBK1247",
            title="Test Chapter",
            pubmed_id="20301425",
            gene_symbols=("BRCA1",),
            omim_ids=(),
            authors=None,
            initial_pub_date=None,
            last_updated_date=date(2025, 12, 1),
        )
        fake_repo = AsyncMock()
        fake_repo.get_defining_chapter_by_gene = AsyncMock(return_value=chapter)
        app.state.repository = fake_repo

    return app


@pytest.mark.asyncio
async def test_truncation_does_not_poison_shared_service_result_across_requests() -> None:
    """Regression: in-place truncation mutated the cached GeneReview instance.

    Request 1 with max_chars=100 used to truncate the cached object; Request 2
    with max_chars=0 then returned the truncated copy with stale
    _meta.truncated=True. The route now deep-copies after the indexed call so
    the shared instance stays pristine. This test uses a SharedService that
    returns the same payload instance every call (no model_copy) to exercise
    the real cache shape.
    """
    payload = _make_genereview(
        summary_chars=1000,
        diagnosis_chars=2000,
        management_chars=3000,
    )
    recorded: list[dict[str, Any]] = []
    # Drive through the indexed (cached) path — drop fresh=true.
    app = _build_app_with_shared_service(payload, recorded=recorded)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        first = await c.get("/genereview/BRCA1?include_fulltext=true&max_chars=100")
        second = await c.get("/genereview/BRCA1?include_fulltext=true&max_chars=0")
    assert [r["method"] for r in recorded] == ["indexed", "indexed"], (
        "cache-poisoning regression must exercise the indexed (cached) path"
    )
    assert first.status_code == 200 and second.status_code == 200
    # First request truncates: summary kept at 100 chars, rest cleared.
    assert len(first.json()["summary"]["content"]["text"]) == 100
    assert first.json()["_meta"]["truncated"] is True
    # Second request must return un-truncated content. If the cached instance
    # were mutated by the first call, the second would still be 100 chars.
    assert len(second.json()["summary"]["content"]["text"]) == 1000
    assert len(second.json()["diagnosis"]["content"]["text"]) == 2000
    assert len(second.json()["management"]["content"]["text"]) == 3000
    assert second.json()["_meta"].get("truncated", False) is False
    assert "next_commands" not in second.json()["_meta"]
    # The shared payload itself must not have been mutated.
    assert payload.summary is not None and len(payload.summary.content.text) == 1000


@pytest.mark.asyncio
async def test_default_route_forwards_include_fulltext_false_to_service() -> None:
    """Default-flip invariant: the route passes include_fulltext=False on bare calls.

    Without this assertion, a regression that hardcodes include_fulltext=True
    in the route handler would still pass test_default_response_is_lean
    (because the FakeService ignored the kwarg).
    """
    payload = _make_genereview(summary_chars=0)
    recorded: list[dict[str, Any]] = []
    # No fresh=true: prove the route forwards include_fulltext=False through
    # the standard (indexed) call path.
    app = _build_app_with_shared_service(payload, recorded=recorded)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get("/genereview/BRCA1")
    assert resp.status_code == 200, resp.text
    assert recorded, "service was not called"
    assert recorded[-1]["kwargs"]["include_fulltext"] is False


@pytest.mark.asyncio
async def test_truncation_hint_omits_next_commands_when_book_url_has_no_nbk_segment() -> None:
    """If book_url lacks the canonical /books/NBK<id>/ path, no get_chapter_section
    hint is emitted (truncated=True is still set). Avoids dead-end recovery
    hints with empty arguments.
    """
    payload = _make_genereview(
        summary_chars=500,
        book_url="https://example.com/no-nbk-here/",
    )
    app = _build_app_with_service({"indexed": payload, "uncached": payload})
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get("/genereview/BRCA1?include_fulltext=true&max_chars=100&fresh=true")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["_meta"]["truncated"] is True
    # No useless hint with empty arguments.
    assert "next_commands" not in body["_meta"]


@pytest.mark.asyncio
async def test_truncation_hint_picks_path_anchored_nbk_not_query_string() -> None:
    """_NBK_ID_PATTERN must anchor on /books/<NBK> path, not match any NBK token."""
    payload = _make_genereview(
        summary_chars=500,
        # Canonical chapter is NBK1247; query-string referer mentions a different ID.
        book_url="https://www.ncbi.nlm.nih.gov/books/NBK1247/?ref=NBK99999",
    )
    app = _build_app_with_service({"indexed": payload, "uncached": payload})
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get("/genereview/BRCA1?include_fulltext=true&max_chars=100&fresh=true")
    body = resp.json()
    args = body["_meta"]["next_commands"][0]["arguments"]
    assert args["nbk_id"] == "NBK1247"


@pytest.mark.asyncio
async def test_max_chars_query_validation_rejects_negative() -> None:
    """ge=0 constraint: negative max_chars is rejected at the query layer."""
    payload = _make_genereview(summary_chars=10)
    app = _build_app_with_service({"indexed": payload, "uncached": payload})
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get("/genereview/BRCA1?include_fulltext=true&max_chars=-1&fresh=true")
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_max_chars_query_validation_rejects_over_max() -> None:
    """le=200000 constraint: above-ceiling max_chars is rejected."""
    payload = _make_genereview(summary_chars=10)
    app = _build_app_with_service({"indexed": payload, "uncached": payload})
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get("/genereview/BRCA1?include_fulltext=true&max_chars=999999&fresh=true")
    assert resp.status_code == 422
