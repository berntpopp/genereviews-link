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
from genereview_link.models.genereview_models import (
    FullTextData,
    FullTextMetadata,
    GeneReview,
    GeneReviewSection,
)
from genereview_link.server_manager import UnifiedServerManager
from genereview_link.services.service_manager import get_managed_service


def _make_section(title: str, content: str) -> GeneReviewSection:
    return GeneReviewSection(title=title, content=content, level=1, subsections={})


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
        title="Test Chapter",
        sections={},
        metadata=FullTextMetadata(),
    )
    return GeneReview(
        gene_symbol="BRCA1",
        pubmed_id="20301425",
        book_url=book_url,
        title="Test Chapter",
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
        title="Test Chapter",
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
    assert body["_meta"].get("next_commands") in (None, [])


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
    assert len(body["summary"]["content"]) == 1000
    assert len(body["diagnosis"]["content"]) == 2000
    assert len(body["management"]["content"]) == 3000
    assert body["_meta"].get("truncated", False) is False
    assert body["_meta"].get("next_commands") in (None, [])


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
    assert len(body["summary"]["content"]) == 10000
    assert len(body["diagnosis"]["content"]) == 20000
    assert len(body["management"]["content"]) == 20000
    assert body["_meta"].get("truncated", False) is False
    assert body["_meta"].get("next_commands") in (None, [])


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
    assert len(body["summary"]["content"]) == 80
    assert len(body["diagnosis"]["content"]) == 20
    assert body["management"]["content"] == ""
    assert body["other_sections"]["references"]["content"] == ""
    # Total kept content must not exceed the cap.
    total_kept = (
        len(body["summary"]["content"])
        + len(body["diagnosis"]["content"])
        + len(body["management"]["content"])
        + sum(s["content"].__len__() for s in body["other_sections"].values())
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
