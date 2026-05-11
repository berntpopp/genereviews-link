"""Unit tests for /passages/search using TestClient + dependency overrides."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from genereview_link.api.client_manager import get_managed_client
from genereview_link.api.routes.passages import get_embedding_provider, get_repository
from genereview_link.config import ServerConfig
from genereview_link.retrieval.embeddings import FakeEmbeddingProvider
from genereview_link.retrieval.repository import (
    GeneReviewRepository,
    LexicalPassageRow,
    PassageRow,
)
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


def _make_passage_row(passage_id: str = "p1") -> PassageRow:
    return PassageRow(
        nbk_id="NBK1",
        passage_id=passage_id,
        chapter_section="summary",
        heading_path="Summary",
        section_level=1,
        chunk_index=0,
        text="This is a test passage about BRCA1.",
    )


def _make_lexical_row(passage_id: str = "p1") -> LexicalPassageRow:
    return LexicalPassageRow(
        passage=_make_passage_row(passage_id),
        phrase_rank=0.5,
        strict_rank=0.4,
        recall_rank=0.3,
        recall_overlap_count=2,
        lexical_rank=0.6,
    )


@pytest.fixture
def fake_repo() -> GeneReviewRepository:
    repo = AsyncMock(spec=GeneReviewRepository)
    repo.search_passages.return_value = [_make_lexical_row()]
    repo.active_embedding_table.return_value = "genereview_embeddings_bge384"
    repo.dense_scores_for_passages.return_value = {"p1": 0.85}
    return repo


@pytest.fixture
def fake_embedder() -> FakeEmbeddingProvider:
    return FakeEmbeddingProvider(dim=384)


@pytest_asyncio.fixture
async def app(fake_repo: GeneReviewRepository, fake_embedder: FakeEmbeddingProvider) -> FastAPI:
    config = ServerConfig(transport="http", log_level="WARNING", enable_docs=False)
    manager = UnifiedServerManager()
    fastapi_app = manager.create_fastapi_app(config)

    async def _get_client() -> Any:
        yield FakeClient()

    async def _get_repo() -> GeneReviewRepository:
        return fake_repo

    async def _get_embedder() -> FakeEmbeddingProvider:
        return fake_embedder

    fastapi_app.dependency_overrides[get_managed_client] = _get_client
    fastapi_app.dependency_overrides[get_repository] = _get_repo
    fastapi_app.dependency_overrides[get_embedding_provider] = _get_embedder
    return fastapi_app


@pytest_asyncio.fixture
async def http_client(app: FastAPI) -> AsyncClient:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


class TestPassagesSearchRoute:
    @pytest.mark.asyncio
    async def test_returns_ranked_passage(self, http_client: AsyncClient) -> None:
        resp = await http_client.get("/passages/search?q=BRCA1+diagnosis")
        assert resp.status_code == 200
        body = resp.json()
        assert isinstance(body, dict)
        assert "_meta" in body
        results = body["results"]
        assert isinstance(results, list)
        assert len(results) == 1
        p = results[0]
        assert p["passage_id"] == "p1"
        assert p["nbk_id"] == "NBK1"
        assert p["chapter_section"] == "summary"
        # score_breakdown is opt-in; absent by default
        assert "score_breakdown" not in p

    @pytest.mark.asyncio
    async def test_missing_q_returns_422(self, http_client: AsyncClient) -> None:
        resp = await http_client.get("/passages/search")
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_limit_param(self, http_client: AsyncClient, fake_repo: Any) -> None:
        fake_repo.search_passages.return_value = [_make_lexical_row(f"p{i}") for i in range(10)]
        fake_repo.dense_scores_for_passages.return_value = {
            f"p{i}": 0.9 - i * 0.05 for i in range(10)
        }
        resp = await http_client.get("/passages/search?q=test&limit=3")
        assert resp.status_code == 200
        assert len(resp.json()["results"]) <= 3

    @pytest.mark.asyncio
    async def test_rerank_lexical(self, http_client: AsyncClient) -> None:
        resp = await http_client.get("/passages/search?q=BRCA1&rerank=lexical")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_rerank_off_preserves_repo_order(
        self, http_client: AsyncClient, fake_repo: Any
    ) -> None:
        """rerank=off bypasses section_priority; rows arrive in repo order."""
        # First row is "references" (high section_priority), second is "summary".
        # With section_priority tiebreaker, summary should win;
        # with rerank=off, the repo order is preserved.
        row_refs = LexicalPassageRow(
            passage=PassageRow(
                nbk_id="NBK1",
                passage_id="p_refs",
                chapter_section="references",
                heading_path="References",
                section_level=1,
                chunk_index=0,
                text="ref text",
            ),
            phrase_rank=1.0,
            strict_rank=0.0,
            recall_rank=0.0,
            recall_overlap_count=1,
            lexical_rank=1.0,
        )
        row_summary = LexicalPassageRow(
            passage=PassageRow(
                nbk_id="NBK1",
                passage_id="p_summary",
                chapter_section="summary",
                heading_path="Summary",
                section_level=1,
                chunk_index=0,
                text="summary text",
            ),
            phrase_rank=1.0,
            strict_rank=0.0,
            recall_rank=0.0,
            recall_overlap_count=1,
            lexical_rank=1.0,
        )
        fake_repo.search_passages.return_value = [row_refs, row_summary]
        resp = await http_client.get("/passages/search?q=BRCA1&rerank=off")
        assert resp.status_code == 200
        results = resp.json()["results"]
        assert results[0]["passage_id"] == "p_refs"
        assert results[1]["passage_id"] == "p_summary"

    @pytest.mark.asyncio
    async def test_503_when_repository_not_set(
        self, app: FastAPI, http_client: AsyncClient
    ) -> None:
        # Remove override to simulate missing repository
        async def _no_repo() -> None:
            from fastapi import HTTPException

            raise HTTPException(
                status_code=503,
                detail="DATABASE_URL not configured — Postgres repository unavailable",
            )

        app.dependency_overrides[get_repository] = _no_repo
        resp = await http_client.get("/passages/search?q=test")
        assert resp.status_code == 503


def _brief_row(pid: str, snippet: str) -> LexicalPassageRow:
    return LexicalPassageRow(
        passage=PassageRow(
            nbk_id="NBK1",
            passage_id=pid,
            chapter_section="management",
            heading_path="Management > X",
            section_level=2,
            chunk_index=1,
            text="full text here",
            chapter_title="Chapter",
            chapter_last_updated=None,
            gene_symbols=("TG",),
        ),
        phrase_rank=1.0,
        strict_rank=0.5,
        recall_rank=0.4,
        recall_overlap_count=1,
        lexical_rank=1.0,
        snippet=snippet,
    )


def _make_brief_app(repo: Any) -> FastAPI:
    """Build a minimal FastAPI app wired to ``repo`` for the new mode tests."""
    from genereview_link.api.routes import passages as passages_routes

    app = FastAPI()
    app.include_router(passages_routes.router)
    app.state.repository = repo
    app.state.embedder = FakeEmbeddingProvider(dim=384)
    return app


def _make_brief_repo(rows: int = 7) -> Any:
    from unittest.mock import MagicMock

    repo = MagicMock()
    repo.search_passages = AsyncMock(
        return_value=[_brief_row(f"NBK1:000{i}", f"**bold{i}**") for i in range(rows)]
    )
    repo.active_embedding_table = AsyncMock(return_value="t")
    repo.dense_scores_for_passages = AsyncMock(return_value={})
    return repo


@pytest.mark.asyncio
async def test_search_default_mode_is_brief_and_limit_is_5() -> None:
    """Default response has snippet populated, text null, and <=5 rows."""
    repo = _make_brief_repo(rows=7)
    app = _make_brief_app(repo)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get("/passages/search", params={"q": "BRCA1"})
    assert resp.status_code == 200
    body = resp.json()
    assert "_meta" in body
    results = body["results"]
    assert len(results) == 5
    assert results[0]["snippet"] is not None
    assert results[0]["text"] is None
    assert results[0]["chapter_title"] == "Chapter"


@pytest.mark.asyncio
async def test_search_mode_full_populates_text() -> None:
    """mode=full returns text, snippet null."""
    repo = _make_brief_repo(rows=2)
    app = _make_brief_app(repo)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get("/passages/search", params={"q": "BRCA1", "mode": "full"})
    assert resp.status_code == 200
    body = resp.json()
    results = body["results"]
    assert results[0]["text"] == "full text here"
    assert results[0]["snippet"] is None


@pytest.mark.asyncio
async def test_search_exclude_drops_field() -> None:
    """exclude=score_breakdown removes that key from each row."""
    repo = _make_brief_repo(rows=2)
    app = _make_brief_app(repo)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get(
            "/passages/search",
            params={"q": "BRCA1", "exclude": "score_breakdown"},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert "_meta" in body
    assert "score_breakdown" not in body["results"][0]


@pytest.mark.asyncio
async def test_search_exclude_bogus_returns_422() -> None:
    """Unknown exclude value rejected by FastAPI validation."""
    repo = _make_brief_repo(rows=1)
    app = _make_brief_app(repo)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get(
            "/passages/search",
            params={"q": "BRCA1", "exclude": "bogus"},
        )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_search_filter_uses_nbk_id_not_nbk() -> None:
    """The route accepts ?nbk_id= and forwards it to the repository."""
    repo = _make_brief_repo(rows=1)
    app = _make_brief_app(repo)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get(
            "/passages/search",
            params={"q": "BRCA1", "nbk_id": "NBK1247"},
        )
    assert resp.status_code == 200
    assert repo.search_passages.call_args.kwargs["nbk_id"] == "NBK1247"


@pytest.mark.asyncio
async def test_search_response_includes_meta_attribution() -> None:
    """Search response wraps results in an envelope with _meta.attribution."""
    repo = _make_brief_repo(rows=7)
    app = _make_brief_app(repo)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get("/passages/search", params={"q": "BRCA1"})

    assert resp.status_code == 200
    body = resp.json()
    assert "_meta" in body
    assert body["_meta"]["attribution"].startswith("GeneReviews")
    assert "results" in body
    assert len(body["results"]) == 5


@pytest.mark.asyncio
async def test_search_response_includes_corpus_version_from_app_state() -> None:
    """`_meta.corpus_version` is wired through from app.state.corpus_version."""
    repo = _make_brief_repo(rows=2)
    app = _make_brief_app(repo)
    app.state.corpus_version = "2026-01-15"

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get("/passages/search", params={"q": "BRCA1"})
    body = resp.json()
    assert body["_meta"]["corpus_version"] == "2026-01-15"


@pytest.mark.asyncio
async def test_search_exclude_path_includes_corpus_version() -> None:
    """JSONResponse fallback path also exposes _meta.corpus_version."""
    repo = _make_brief_repo(rows=2)
    app = _make_brief_app(repo)
    app.state.corpus_version = "2026-02-01"

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get(
            "/passages/search",
            params={"q": "BRCA1", "exclude": "score_breakdown"},
        )
    body = resp.json()
    assert body["_meta"]["corpus_version"] == "2026-02-01"
    assert "score_breakdown" not in body["results"][0]


# ---------------------------------------------------------------------------
# score_breakdown opt-in tests (Task 5)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_omits_score_breakdown_by_default() -> None:
    """score_breakdown is absent from results unless include=score_breakdown."""
    repo = _make_brief_repo(rows=2)
    app = _make_brief_app(repo)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get("/passages/search", params={"q": "BRCA1"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["results"]
    assert "score_breakdown" not in body["results"][0]


@pytest.mark.asyncio
async def test_search_includes_score_breakdown_when_requested() -> None:
    """include=score_breakdown adds the field to every result row."""
    repo = _make_brief_repo(rows=2)
    app = _make_brief_app(repo)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get(
            "/passages/search",
            params={"q": "BRCA1", "include": "score_breakdown"},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["results"]
    p = body["results"][0]
    assert "score_breakdown" in p
    sb = p["score_breakdown"]
    assert sb["final_position"] == 1
    # All ScoreBreakdown fields must be present
    for field in (
        "lexical_rank",
        "phrase_rank",
        "strict_rank",
        "recall_rank",
        "section_priority",
        "final_position",
    ):
        assert field in sb, f"Missing ScoreBreakdown field: {field}"


@pytest.mark.asyncio
async def test_search_exclude_score_breakdown_is_noop_after_default_flip() -> None:
    """exclude=score_breakdown is a no-op; field is already absent by default."""
    repo = _make_brief_repo(rows=2)
    app = _make_brief_app(repo)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get(
            "/passages/search",
            params={"q": "BRCA1", "exclude": "score_breakdown"},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["results"]
    assert "score_breakdown" not in body["results"][0]


# ---------------------------------------------------------------------------
# Diagnostics tests (Task 14)
# ---------------------------------------------------------------------------


def _make_empty_repo() -> Any:
    """Repo whose search_passages always returns an empty list."""
    from unittest.mock import MagicMock

    repo = MagicMock()
    repo.search_passages = AsyncMock(return_value=[])
    repo.active_embedding_table = AsyncMock(return_value="t")
    repo.dense_scores_for_passages = AsyncMock(return_value={})
    return repo


@pytest.mark.asyncio
async def test_search_zero_results_emits_diagnostics() -> None:
    """Empty results with a sections filter produce _meta.diagnostics with suggestions."""
    repo = _make_empty_repo()
    app = _make_brief_app(repo)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get(
            "/passages/search",
            params={"q": "xyzzy_definitely_not_in_corpus_zzz", "sections": "management"},
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["results"] == []
    diag = data["_meta"].get("diagnostics")
    assert diag is not None
    assert "suggestions" in diag


@pytest.mark.asyncio
async def test_search_zero_results_long_query_emits_broaden_suggestion() -> None:
    """A very long query triggers the 'broaden q' suggestion."""
    repo = _make_empty_repo()
    app = _make_brief_app(repo)
    long_q = "this is a very long query with more than eight words in total to test broadening"

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get("/passages/search", params={"q": long_q})
    assert resp.status_code == 200
    data = resp.json()
    assert data["results"] == []
    diag = data["_meta"].get("diagnostics")
    assert diag is not None
    assert any("broaden" in s for s in diag["suggestions"])


@pytest.mark.asyncio
async def test_search_nonzero_results_omits_diagnostics() -> None:
    """When results are returned, _meta.diagnostics is absent (None serialises to null/absent)."""
    repo = _make_brief_repo(rows=2)
    app = _make_brief_app(repo)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get("/passages/search", params={"q": "BRCA1"})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["results"]) > 0
    assert data["_meta"].get("diagnostics") is None


@pytest.mark.asyncio
async def test_search_diagnostics_shape() -> None:
    """Diagnostics object carries the expected keys."""
    repo = _make_empty_repo()
    app = _make_brief_app(repo)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get(
            "/passages/search",
            params={"q": "short query", "sections": "management"},
        )
    assert resp.status_code == 200
    diag = resp.json()["_meta"]["diagnostics"]
    assert diag is not None
    for key in ("lexical_hits", "lexical_hits_after_filters", "applied_filters", "suggestions"):
        assert key in diag, f"Missing diagnostics key: {key}"
    assert isinstance(diag["applied_filters"], list)
    assert isinstance(diag["suggestions"], list)


@pytest.mark.asyncio
async def test_search_diagnostics_via_exclude_path() -> None:
    """Diagnostics are present even when the JSONResponse (exclude) branch is taken."""
    repo = _make_empty_repo()
    app = _make_brief_app(repo)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get(
            "/passages/search",
            params={
                "q": "xyzzy_long_query_to_trigger_broaden_suggestion_here",
                "exclude": "heading_path",
            },
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["results"] == []
    diag = data["_meta"].get("diagnostics")
    assert diag is not None
    assert "suggestions" in diag
