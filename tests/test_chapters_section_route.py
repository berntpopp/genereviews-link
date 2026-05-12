"""Unit tests for /chapters/{nbk_id}/sections/{section} using TestClient + dependency overrides."""

from __future__ import annotations

from datetime import date
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from genereview_link.api.client_manager import get_managed_client
from genereview_link.api.routes import chapters as chapters_routes
from genereview_link.api.routes.passages import get_embedding_provider, get_repository
from genereview_link.config import ServerConfig
from genereview_link.retrieval.embeddings import FakeEmbeddingProvider
from genereview_link.retrieval.repository import ChapterRow, GeneReviewRepository, PassageRow
from genereview_link.server_manager import UnifiedServerManager


class FakeClient:
    async def search_genereviews(self, *a: Any, **kw: Any) -> dict:
        return {"count": 0, "retmax": 20, "retstart": 0, "ids": [], "webenv": "", "querykey": ""}

    async def fetch_abstract(self, *a: Any, **kw: Any) -> dict:
        return {}

    async def get_all_links(self, *a: Any, **kw: Any) -> dict:
        return {"urls": []}

    async def scrape_genereview_comprehensive(self, *a: Any, **kw: Any) -> dict:
        return {"nbk_id": "1", "url": "", "title": "", "sections": {}, "metadata": {}}


def _make_passages() -> list[PassageRow]:
    return [
        PassageRow(
            nbk_id="NBK1247",
            passage_id="p1",
            chapter_section="summary",
            heading_path="Summary",
            section_level=1,
            chunk_index=0,
            text="First chunk of the summary section.",
        ),
        PassageRow(
            nbk_id="NBK1247",
            passage_id="p2",
            chapter_section="summary",
            heading_path="Summary",
            section_level=1,
            chunk_index=1,
            text="Second chunk of the summary section.",
        ),
    ]


@pytest.fixture
def fake_repo() -> GeneReviewRepository:
    repo = AsyncMock(spec=GeneReviewRepository)
    repo.get_section.return_value = _make_passages()
    return repo


@pytest_asyncio.fixture
async def app(fake_repo: GeneReviewRepository) -> FastAPI:
    config = ServerConfig(transport="http", log_level="WARNING", enable_docs=False)
    manager = UnifiedServerManager()
    fastapi_app = manager.create_fastapi_app(config)

    async def _get_client() -> Any:
        yield FakeClient()

    async def _get_repo() -> GeneReviewRepository:
        return fake_repo

    async def _get_embedder() -> FakeEmbeddingProvider:
        return FakeEmbeddingProvider(dim=384)

    fastapi_app.dependency_overrides[get_managed_client] = _get_client
    fastapi_app.dependency_overrides[get_repository] = _get_repo
    fastapi_app.dependency_overrides[get_embedding_provider] = _get_embedder
    return fastapi_app


@pytest_asyncio.fixture
async def http_client(app: FastAPI) -> AsyncClient:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


class TestChapterSectionRoute:
    @pytest.mark.asyncio
    async def test_returns_section_with_passages(self, http_client: AsyncClient) -> None:
        resp = await http_client.get("/chapters/NBK1247/sections/summary")
        assert resp.status_code == 200
        body = resp.json()
        assert body["nbk_id"] == "NBK1247"
        assert body["chapter_section"] == "summary"
        assert len(body["passages"]) == 2
        # concatenated_text is opt-in; must be absent by default
        assert "concatenated_text" not in body
        # License lives at the dedicated /license endpoint, not inlined here.
        assert "license" not in body

    @pytest.mark.asyncio
    async def test_returns_section_with_concatenated_text_when_opted_in(
        self, http_client: AsyncClient
    ) -> None:
        resp = await http_client.get(
            "/chapters/NBK1247/sections/summary",
            params={"include": "concatenated_text"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "concatenated_text" in body
        assert "First chunk" in body["concatenated_text"]
        assert "Second chunk" in body["concatenated_text"]

    @pytest.mark.asyncio
    async def test_returns_404_when_section_not_found(
        self, http_client: AsyncClient, fake_repo: Any
    ) -> None:
        fake_repo.get_section.return_value = []
        resp = await http_client.get("/chapters/NBK1247/sections/management")
        assert resp.status_code == 404


def _make_chapter_row(nbk_id: str = "NBK1247") -> ChapterRow:
    return ChapterRow(
        nbk_id=nbk_id,
        short_name=nbk_id,
        title="BRCA1- and BRCA2-Associated Hereditary Breast and Ovarian Cancer",
        pubmed_id="20301425",
        gene_symbols=("BRCA1", "BRCA2"),
        omim_ids=(),
        authors=None,
        initial_pub_date=None,
        last_updated_date=date(2025, 12, 1),
    )


_DEFAULT_CHAPTER = object()


def _build_app(
    *, passages: list[PassageRow], chapter: ChapterRow | None | object = _DEFAULT_CHAPTER
) -> FastAPI:
    app = FastAPI()
    app.include_router(chapters_routes.router)
    repo = MagicMock()
    repo.get_section = AsyncMock(return_value=passages)
    repo.get_chapter_by_nbk = AsyncMock(
        return_value=_make_chapter_row() if chapter is _DEFAULT_CHAPTER else chapter
    )
    app.state.repository = repo
    return app


@pytest.mark.asyncio
async def test_returns_passages_with_chapter_title_envelope() -> None:
    pr = PassageRow(
        nbk_id="NBK1",
        passage_id="NBK1:0001",
        chapter_section="management",
        heading_path="Management > X",
        section_level=2,
        chunk_index=0,
        text="sample text",
        chapter_title="Test Chapter Title",
        chapter_last_updated=date(2025, 12, 1),
        gene_symbols=("TG",),
    )
    app = _build_app(passages=[pr])

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get(
            "/chapters/NBK1/sections/management",
            params={"include": "concatenated_text"},
        )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["nbk_id"] == "NBK1"
    assert body["chapter_section"] == "management"
    assert body["chapter_title"] == "Test Chapter Title"
    assert body["chapter_last_updated"] == "2025-12-01"
    assert body["passages"][0]["passage_id"] == "NBK1:0001"
    assert body["concatenated_text"] == "sample text"


@pytest.mark.asyncio
async def test_old_path_param_name_does_not_match() -> None:
    """If someone reverts the rename, this test will fail because the
    old route had a path param called `nbk`; the new one is `nbk_id`.
    The path itself doesn't change — only the function signature does —
    so this test asserts the call still returns 200 (route path is
    unchanged) and that the response envelope keys use `nbk_id`.
    """
    pr = PassageRow(
        nbk_id="NBK1",
        passage_id="NBK1:0001",
        chapter_section="management",
        heading_path=None,
        section_level=1,
        chunk_index=0,
        text="t",
        chapter_title="C",
        chapter_last_updated=None,
        gene_symbols=(),
    )
    app = _build_app(passages=[pr])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get("/chapters/NBK1/sections/management")
    body = resp.json()
    assert "nbk_id" in body
    assert "nbk" not in body or body.get("nbk_id") == body.get("nbk")


@pytest.mark.asyncio
async def test_section_not_found_returns_structured_payload():
    app = _build_app(passages=[])  # empty list -> 404
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get("/chapters/NBK1247/sections/management")
    assert resp.status_code == 404
    detail = resp.json()["detail"]
    assert detail["code"] == "section_empty_for_chapter"
    assert detail["recovery_hint"]
    assert "no rows" in detail["recovery_hint"]
    assert "no passages" in detail["message"]
    # next_commands suggests search_passages:
    nc = detail["next_commands"][0]
    assert nc["tool"] == "search_passages"
    assert nc["arguments"]["nbk_id"] == "NBK1247"


@pytest.mark.asyncio
async def test_summary_section_returns_noted_empty_response_for_known_chapter() -> None:
    app = _build_app(passages=[], chapter=_make_chapter_row())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get("/chapters/NBK1247/sections/summary")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["nbk_id"] == "NBK1247"
    assert body["chapter_section"] == "summary"
    assert body["passages"] == []
    assert body["passage_count"] == 0
    assert "https://www.ncbi.nlm.nih.gov/books/NBK1247/" in body["note"]


@pytest.mark.asyncio
async def test_summary_section_returns_404_for_unknown_chapter() -> None:
    app = _build_app(passages=[], chapter=None)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get("/chapters/NBK9999999/sections/summary")

    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "chapter_not_found"


@pytest.mark.asyncio
async def test_section_response_includes_meta_attribution() -> None:
    """Chapter section response wraps payload in an envelope with _meta.attribution."""
    pr = PassageRow(
        nbk_id="NBK1",
        passage_id="NBK1:0001",
        chapter_section="management",
        heading_path="Management > X",
        section_level=2,
        chunk_index=0,
        text="t",
        chapter_title="Test",
        chapter_last_updated=date(2025, 12, 1),
        gene_symbols=("TG",),
    )
    app = _build_app(passages=[pr])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get("/chapters/NBK1/sections/management")
    body = resp.json()
    assert "_meta" in body
    assert body["_meta"]["attribution"].startswith("GeneReviews")


@pytest.mark.asyncio
async def test_section_response_includes_corpus_version_from_app_state() -> None:
    """Chapter section response surfaces app.state.corpus_version on _meta."""
    pr = PassageRow(
        nbk_id="NBK1",
        passage_id="NBK1:0001",
        chapter_section="management",
        heading_path="Management > X",
        section_level=2,
        chunk_index=0,
        text="t",
        chapter_title="Test",
        chapter_last_updated=date(2025, 12, 1),
        gene_symbols=("TG",),
    )
    app = _build_app(passages=[pr])
    app.state.corpus_version = "2026-03-10"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get("/chapters/NBK1/sections/management")
    body = resp.json()
    assert body["_meta"]["corpus_version"] == "2026-03-10"


@pytest.mark.asyncio
async def test_chapter_section_default_omits_concatenated_text() -> None:
    """Default response must NOT contain the concatenated_text key at all."""
    pr = PassageRow(
        nbk_id="NBK1247",
        passage_id="NBK1247:0001",
        chapter_section="summary",
        heading_path="Summary",
        section_level=1,
        chunk_index=0,
        text="some text",
    )
    app = _build_app(passages=[pr])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get("/chapters/NBK1247/sections/summary")
    assert resp.status_code == 200
    data = resp.json()
    assert "passages" in data
    assert "concatenated_text" not in data


@pytest.mark.asyncio
async def test_chapter_section_include_concatenated_returns_both() -> None:
    """include=concatenated_text adds the joined string alongside passages."""
    pr = PassageRow(
        nbk_id="NBK1247",
        passage_id="NBK1247:0001",
        chapter_section="summary",
        heading_path="Summary",
        section_level=1,
        chunk_index=0,
        text="some text",
    )
    app = _build_app(passages=[pr])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get(
            "/chapters/NBK1247/sections/summary",
            params={"include": "concatenated_text"},
        )
    assert resp.status_code == 200
    data = resp.json()
    assert "passages" in data
    assert "concatenated_text" in data
    assert isinstance(data["concatenated_text"], str)


# ---------------------------------------------------------------------------
# Dedupe parameter tests
# ---------------------------------------------------------------------------

# Two passages where the second begins with the same 34-char string that ends
# the first passage.  This exceeds min_overlap=30 so _strip_overlap removes it.
_OVERLAP = "BCDEFGHIJKLMNOPQRSTUVWXYZABCDEFGHIJ"  # 35 chars
_PART1 = "A" * 100 + _OVERLAP
_PART2 = _OVERLAP + "B" * 100
_EXPECTED_DEDUPED = "A" * 100 + _OVERLAP + "B" * 100
_EXPECTED_PLAIN = _PART1 + "\n\n" + _PART2


def _build_app_with_overlap() -> FastAPI:
    """Return a minimal app whose get_section yields two overlapping passages."""
    passages = [
        PassageRow(
            nbk_id="NBK9999",
            passage_id="NBK9999:0000",
            chapter_section="management",
            heading_path="Management",
            section_level=1,
            chunk_index=0,
            text=_PART1,
        ),
        PassageRow(
            nbk_id="NBK9999",
            passage_id="NBK9999:0001",
            chapter_section="management",
            heading_path="Management",
            section_level=1,
            chunk_index=1,
            text=_PART2,
        ),
    ]
    return _build_app(passages=passages)


@pytest.mark.asyncio
async def test_dedupe_true_strips_overlap_region() -> None:
    """dedupe=true with include=concatenated_text removes the shared suffix/prefix."""
    app = _build_app_with_overlap()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get(
            "/chapters/NBK9999/sections/management",
            params={"include": "concatenated_text", "dedupe": "true"},
        )
    assert resp.status_code == 200
    data = resp.json()
    assert "concatenated_text" in data
    text = data["concatenated_text"]
    assert text == _EXPECTED_DEDUPED
    # Overlap region appears exactly once
    assert text.count(_OVERLAP) == 1


@pytest.mark.asyncio
async def test_dedupe_false_default_preserves_overlap() -> None:
    """Default (dedupe=false) keeps the naive join with separator — no stripping."""
    app = _build_app_with_overlap()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get(
            "/chapters/NBK9999/sections/management",
            params={"include": "concatenated_text"},
        )
    assert resp.status_code == 200
    data = resp.json()
    text = data["concatenated_text"]
    assert text == _EXPECTED_PLAIN
    # Overlap region appears twice (once per chunk)
    assert text.count(_OVERLAP) == 2


@pytest.mark.asyncio
async def test_dedupe_without_include_has_no_effect() -> None:
    """dedupe=true without include=concatenated_text must not add the field."""
    app = _build_app_with_overlap()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get(
            "/chapters/NBK9999/sections/management",
            params={"dedupe": "true"},
        )
    assert resp.status_code == 200
    data = resp.json()
    assert "concatenated_text" not in data


# ---------------------------------------------------------------------------
# Task 7: passage_count + concatenated_char_count (Spec E1)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chapter_section_default_includes_passage_count_without_concatenated_char_count() -> (
    None
):
    """passage_count is always present; concatenated_char_count is absent when not opted in."""
    pr1 = PassageRow(
        nbk_id="NBK1247",
        passage_id="NBK1247:0001",
        chapter_section="diagnosis",
        heading_path="Diagnosis",
        section_level=1,
        chunk_index=0,
        text="First passage.",
    )
    pr2 = PassageRow(
        nbk_id="NBK1247",
        passage_id="NBK1247:0002",
        chapter_section="diagnosis",
        heading_path="Diagnosis",
        section_level=1,
        chunk_index=1,
        text="Second passage.",
    )
    app = _build_app(passages=[pr1, pr2])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get("/chapters/NBK1247/sections/diagnosis")
    assert resp.status_code == 200
    body = resp.json()
    assert "passage_count" in body
    assert isinstance(body["passage_count"], int)
    assert body["passage_count"] == len(body["passages"])
    # concatenated_char_count must be absent when concatenated_text was not requested
    assert "concatenated_char_count" not in body
    assert "concatenated_text" not in body


@pytest.mark.asyncio
async def test_chapter_section_concatenated_text_includes_char_count() -> None:
    """include=concatenated_text also populates concatenated_char_count."""
    pr = PassageRow(
        nbk_id="NBK1247",
        passage_id="NBK1247:0001",
        chapter_section="diagnosis",
        heading_path="Diagnosis",
        section_level=1,
        chunk_index=0,
        text="Hello world.",
    )
    app = _build_app(passages=[pr])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get(
            "/chapters/NBK1247/sections/diagnosis",
            params={"include": "concatenated_text"},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["concatenated_text"] is not None
    assert body["concatenated_char_count"] == len(body["concatenated_text"])


# ---------------------------------------------------------------------------
# heading_path_contains filter tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chapter_section_heading_path_contains_filters_passages() -> None:
    """heading_path_contains is forwarded to repo.get_section as a kwarg."""
    app = FastAPI()
    app.include_router(chapters_routes.router)
    repo = MagicMock()
    repo.get_section = AsyncMock(
        return_value=[
            PassageRow(
                nbk_id="NBK1247",
                passage_id="NBK1247:0001",
                chapter_section="diagnosis",
                heading_path="Diagnosis > Clinical Diagnosis",
                section_level=2,
                chunk_index=0,
                text="Clinical diagnosis text.",
            )
        ]
    )
    app.state.repository = repo

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get(
            "/chapters/NBK1247/sections/diagnosis",
            params={"heading_path_contains": "Clinical Diagnosis"},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body["passages"], list)
    assert body["passage_count"] == 1
    # Verify the kwarg was forwarded to get_section
    repo.get_section.assert_awaited_once()
    call_kwargs = repo.get_section.call_args.kwargs
    assert call_kwargs["heading_path_contains"] == "Clinical Diagnosis"


@pytest.mark.asyncio
async def test_chapter_section_heading_path_contains_over_200_chars_returns_422() -> None:
    """heading_path_contains values exceeding max_length=200 must be rejected with 422."""
    app = FastAPI()
    app.include_router(chapters_routes.router)
    repo = MagicMock()
    repo.get_section = AsyncMock(return_value=[])
    app.state.repository = repo

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get(
            "/chapters/NBK1247/sections/diagnosis",
            params={"heading_path_contains": "x" * 201},
        )

    assert resp.status_code == 422
