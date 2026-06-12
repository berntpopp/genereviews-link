"""Unit tests for /passages/search using TestClient + dependency overrides."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
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
    # Parallel-retrieval path: dense branch returns the same candidate so RRF fires.
    repo._dense_candidates_filtered.return_value = [{"passage_id": "p1", "dense_score": 0.85}]
    repo.fetch_passages_by_ids.return_value = {}
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
        assert "passage_role" in p
        assert p["passage_role"] is None
        # score_breakdown is opt-in; absent by default
        assert "score_breakdown" not in p

    @pytest.mark.asyncio
    async def test_returns_top_level_rank_fields_by_default(
        self, http_client: AsyncClient, fake_repo: Any
    ) -> None:
        fake_repo.search_passages.return_value = [
            LexicalPassageRow(
                passage=_make_passage_row(),
                phrase_rank=0.5,
                strict_rank=0.4,
                recall_rank=0.3,
                recall_overlap_count=2,
                lexical_rank=0.6,
            )
        ]
        resp = await http_client.get("/passages/search?q=BRCA1&rerank=rrf")
        assert resp.status_code == 200
        p = resp.json()["results"][0]
        assert p["rrf_score"] is not None
        assert p["lexical_score"] == 0.6
        assert p["lexical_rank_position"] == 1
        assert p["dense_rank_position"] == 1
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
            chapter_ingested_at=datetime.now(UTC),
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
    repo._dense_candidates_filtered = AsyncMock(return_value=[])
    repo.fetch_passages_by_ids = AsyncMock(return_value={})
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
    assert "passage_role" in results[0]
    assert results[0]["passage_role"] is None


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
    assert body["_meta"]["diagnostics"]["rerank_used"] == "rrf"


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
async def test_search_filter_canonicalizes_zero_padded_nbk_id() -> None:
    repo = _make_brief_repo(rows=1)
    app = _make_brief_app(repo)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get(
            "/passages/search",
            params={"q": "BRCA1", "nbk_id": "NBK0001247"},
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
    assert sb["adjusted_score"] is None
    assert sb["role_multiplier"] == 1.0
    assert sb["intent_section_boost"] == 0.0
    assert sb["passage_role"] is None
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
async def test_search_propagates_passage_role_and_score_adjustment_fields() -> None:
    """Search result and score_breakdown preserve role/adjustment fields from rows."""
    from unittest.mock import MagicMock

    repo = MagicMock()
    repo.search_passages = AsyncMock(
        return_value=[
            LexicalPassageRow(
                passage=PassageRow(
                    nbk_id="NBK1",
                    passage_id="NBK1:0001",
                    chapter_section="management",
                    heading_path="Management",
                    section_level=1,
                    chunk_index=1,
                    text="role-aware passage",
                    chapter_title="Chapter",
                    gene_symbols=("TG",),
                    passage_role="evidence",
                ),
                phrase_rank=1.0,
                strict_rank=0.5,
                recall_rank=0.4,
                recall_overlap_count=1,
                lexical_rank=1.0,
                adjusted_score=1.25,
                role_multiplier=1.2,
                intent_section_boost=0.05,
            )
        ]
    )
    repo.active_embedding_table = AsyncMock(return_value="t")
    repo.dense_scores_for_passages = AsyncMock(return_value={})
    repo._dense_candidates_filtered = AsyncMock(return_value=[])
    repo.fetch_passages_by_ids = AsyncMock(return_value={})
    app = _make_brief_app(repo)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get(
            "/passages/search",
            params={"q": "BRCA1", "include": "score_breakdown"},
        )

    assert resp.status_code == 200
    result = resp.json()["results"][0]
    assert result["passage_role"] == "evidence"
    sb = result["score_breakdown"]
    assert sb["adjusted_score"] == 1.25
    assert sb["role_multiplier"] == 1.2
    assert sb["intent_section_boost"] == 0.05
    assert sb["passage_role"] == "evidence"


@pytest.mark.asyncio
async def test_search_normalizes_unknown_passage_role_to_none() -> None:
    """Unexpected DB passage_role strings must not make the route return 500."""
    from unittest.mock import MagicMock

    repo = MagicMock()
    repo.search_passages = AsyncMock(
        return_value=[
            LexicalPassageRow(
                passage=PassageRow(
                    nbk_id="NBK1",
                    passage_id="NBK1:0001",
                    chapter_section="management",
                    heading_path="Management",
                    section_level=1,
                    chunk_index=1,
                    text="role-aware passage",
                    chapter_title="Chapter",
                    gene_symbols=("TG",),
                    passage_role="unexpected_role",
                ),
                phrase_rank=1.0,
                strict_rank=0.5,
                recall_rank=0.4,
                recall_overlap_count=1,
                lexical_rank=1.0,
            )
        ]
    )
    repo.active_embedding_table = AsyncMock(return_value="t")
    repo.dense_scores_for_passages = AsyncMock(return_value={})
    repo._dense_candidates_filtered = AsyncMock(return_value=[])
    repo.fetch_passages_by_ids = AsyncMock(return_value={})
    app = _make_brief_app(repo)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get(
            "/passages/search",
            params={"q": "BRCA1", "include": "score_breakdown"},
        )

    assert resp.status_code == 200
    result = resp.json()["results"][0]
    assert result["passage_role"] is None
    assert result["score_breakdown"]["passage_role"] is None


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
    repo._dense_candidates_filtered = AsyncMock(return_value=[])
    repo.fetch_passages_by_ids = AsyncMock(return_value={})
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
async def test_search_nonzero_results_includes_diagnostics() -> None:
    """Successful brief searches include always-on _meta.diagnostics."""
    repo = _make_brief_repo(rows=2)
    app = _make_brief_app(repo)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get("/passages/search", params={"q": "BRCA1"})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["results"]) > 0
    diag = data["_meta"].get("diagnostics")
    assert diag is not None
    assert diag["rerank_used"] == "rrf"
    assert diag["lexical_candidate_count"] == 2
    assert diag["dense_candidate_count"] == 0
    assert diag["suggestions"] == []


@pytest.mark.asyncio
async def test_search_old_top_hits_emit_stale_corpus_suggestion() -> None:
    """Old chapter ingest timestamps on top hits warn that the corpus may be stale."""
    old_ingest = datetime.now(UTC) - timedelta(days=181)
    repo = _make_brief_repo(rows=3)
    repo.search_passages.return_value = [
        LexicalPassageRow(
            passage=PassageRow(
                nbk_id="NBK1",
                passage_id=f"NBK1:000{i}",
                chapter_section="management",
                heading_path="Management > X",
                section_level=2,
                chunk_index=i,
                text="full text here",
                chapter_title="Chapter",
                chapter_last_updated=None,
                chapter_ingested_at=old_ingest,
                gene_symbols=("TG",),
            ),
            phrase_rank=1.0,
            strict_rank=0.5,
            recall_rank=0.4,
            recall_overlap_count=1,
            lexical_rank=1.0,
            snippet=f"**bold{i}**",
        )
        for i in range(3)
    ]
    app = _make_brief_app(repo)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get("/passages/search", params={"q": "BRCA1", "rerank": "lexical"})

    assert resp.status_code == 200
    suggestions = resp.json()["_meta"]["diagnostics"]["suggestions"]
    assert "corpus-may-be-stale" in suggestions


@pytest.mark.asyncio
async def test_search_diagnostics_includes_management_query_intent() -> None:
    """Management-oriented queries expose detected query_intents in diagnostics."""
    repo = _make_brief_repo(rows=2)
    app = _make_brief_app(repo)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get("/passages/search", params={"q": "BRCA1 treatment options"})

    assert resp.status_code == 200
    diag = resp.json()["_meta"]["diagnostics"]
    assert diag["query_intents"] == ["management"]


@pytest.mark.asyncio
async def test_search_diagnostics_query_intents_empty_for_neutral_query() -> None:
    """Neutral queries expose an empty query_intents list in diagnostics."""
    repo = _make_brief_repo(rows=2)
    app = _make_brief_app(repo)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get("/passages/search", params={"q": "BRCA1"})

    assert resp.status_code == 200
    diag = resp.json()["_meta"]["diagnostics"]
    assert diag["query_intents"] == []


@pytest.mark.asyncio
async def test_search_empty_filtered_results_probe_unfiltered_once() -> None:
    """Empty filtered results issue one unfiltered probe and expose drop diagnostics."""
    from unittest.mock import MagicMock

    repo = MagicMock()
    repo.search_passages = AsyncMock(side_effect=[[], [_brief_row("NBK1:0001", "**hit**")]])
    repo.active_embedding_table = AsyncMock(return_value="t")
    repo.dense_scores_for_passages = AsyncMock(return_value={})
    repo._dense_candidates_filtered = AsyncMock(return_value=[])
    repo.fetch_passages_by_ids = AsyncMock(return_value={})
    app = _make_brief_app(repo)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get(
            "/passages/search",
            params={"q": "BRCA1", "sections": "management"},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["results"] == []
    diag = data["_meta"]["diagnostics"]
    assert diag["lexical_candidate_count"] == 0
    assert diag["unfiltered_lexical_count"] == 1
    assert "section-filter-drops-all" in diag["suggestions"]
    assert repo.search_passages.call_count == 2
    first_call, second_call = repo.search_passages.call_args_list
    assert first_call.kwargs["sections"] == ["management"]
    assert second_call.kwargs["gene_symbol"] is None
    assert second_call.kwargs["nbk_id"] is None
    assert second_call.kwargs["sections"] is None


@pytest.mark.asyncio
async def test_search_nonempty_filtered_results_do_not_probe_unfiltered() -> None:
    """Non-empty filtered results do not issue the empty-result unfiltered probe."""
    repo = _make_brief_repo(rows=1)
    app = _make_brief_app(repo)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get(
            "/passages/search",
            params={"q": "BRCA1", "sections": "management"},
        )

    assert resp.status_code == 200
    assert repo.search_passages.call_count == 1


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
    for key in (
        "rerank_used",
        "lexical_candidate_count",
        "dense_candidate_count",
        "section_filters",
        "unfiltered_lexical_count",
        "applied_filters",
        "suggestions",
        "query_intents",
    ):
        assert key in diag, f"Missing diagnostics key: {key}"
    assert isinstance(diag["applied_filters"], list)
    assert isinstance(diag["section_filters"], list)
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


# ---------------------------------------------------------------------------
# passage_type exposure tests
# ---------------------------------------------------------------------------


def _make_table_row() -> LexicalPassageRow:
    """Return a LexicalPassageRow with passage_type='table'."""
    return LexicalPassageRow(
        passage=PassageRow(
            nbk_id="NBK1",
            passage_id="NBK1:0099",
            chapter_section="management",
            heading_path="Management > Table 1",
            section_level=2,
            chunk_index=99,
            text="Table cell content",
            chapter_title="Chapter",
            chapter_last_updated=None,
            gene_symbols=("TG",),
            passage_type="table",
        ),
        phrase_rank=1.0,
        strict_rank=0.5,
        recall_rank=0.4,
        recall_overlap_count=1,
        lexical_rank=1.0,
        snippet="**Table** cell content",
    )


@pytest.mark.asyncio
async def test_search_exposes_passage_type_table() -> None:
    """When a result has passage_type='table', the JSON shape exposes it."""
    from unittest.mock import MagicMock

    repo = MagicMock()
    repo.search_passages = AsyncMock(return_value=[_make_table_row()])
    repo.active_embedding_table = AsyncMock(return_value="t")
    repo.dense_scores_for_passages = AsyncMock(return_value={})
    repo._dense_candidates_filtered = AsyncMock(return_value=[])
    repo.fetch_passages_by_ids = AsyncMock(return_value={})

    app = _make_brief_app(repo)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get("/passages/search", params={"q": "table"})

    assert resp.status_code == 200
    results = resp.json()["results"]
    assert len(results) == 1
    assert results[0]["passage_type"] == "table"


@pytest.mark.asyncio
async def test_search_narrative_passage_type_default() -> None:
    """A standard narrative passage exposes passage_type='narrative'."""
    repo = _make_brief_repo(rows=1)
    app = _make_brief_app(repo)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get("/passages/search", params={"q": "BRCA1"})

    assert resp.status_code == 200
    results = resp.json()["results"]
    assert results[0]["passage_type"] == "narrative"


# ---------------------------------------------------------------------------
# Gene index validation tests (Task 33)
# ---------------------------------------------------------------------------


def _make_indexed_app(repo: Any, symbols: frozenset[str] | None) -> FastAPI:
    """Build a minimal FastAPI app with an optional GeneIndex on app.state."""
    from genereview_link.api.routes import passages as passages_routes
    from genereview_link.services.gene_index import GeneIndex

    app = FastAPI()
    app.include_router(passages_routes.router)
    app.state.repository = repo
    app.state.embedder = FakeEmbeddingProvider(dim=384)
    if symbols is not None:
        app.state.gene_index = GeneIndex(symbols=symbols)
    else:
        app.state.gene_index = None
    return app


@pytest.mark.asyncio
async def test_search_unknown_gene_returns_structured_400() -> None:
    """Unknown gene symbol with a populated index returns 400 with code=gene_not_indexed."""
    repo = _make_brief_repo(rows=1)
    app = _make_indexed_app(repo, frozenset({"BRCA1", "BRCA2"}))

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get("/passages/search", params={"q": "x", "gene": "BRCA9"})

    assert resp.status_code == 400
    detail = resp.json()["detail"]
    assert detail["code"] == "gene_not_indexed"
    assert "next_commands" in detail


@pytest.mark.asyncio
async def test_search_unknown_gene_field_errors_contain_suggestions() -> None:
    """field_errors for an unknown gene include close-match suggestions."""
    repo = _make_brief_repo(rows=1)
    app = _make_indexed_app(repo, frozenset({"BRCA1", "BRCA2"}))

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get("/passages/search", params={"q": "x", "gene": "BRCA9"})

    assert resp.status_code == 400
    detail = resp.json()["detail"]
    # field_errors should be populated with gene + valid_values from rapidfuzz
    field_errors = detail.get("field_errors", [])
    assert len(field_errors) > 0
    gene_error = field_errors[0]
    assert gene_error["field"] == "gene"
    valid = gene_error.get("valid_values") or []
    # BRCA9 should fuzzy-match BRCA1 and/or BRCA2
    assert any("BRCA" in v for v in valid)


@pytest.mark.asyncio
async def test_search_valid_gene_with_index_returns_200() -> None:
    """Known gene symbol with a populated index passes through to 200."""
    repo = _make_brief_repo(rows=1)
    app = _make_indexed_app(repo, frozenset({"BRCA1", "BRCA2"}))

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get("/passages/search", params={"q": "x", "gene": "BRCA1"})

    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_search_gene_index_none_falls_through_to_200() -> None:
    """When gene_index is None (startup failed), unknown gene symbols are not blocked."""
    repo = _make_brief_repo(rows=1)
    app = _make_indexed_app(repo, symbols=None)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get("/passages/search", params={"q": "x", "gene": "TOTALLY_UNKNOWN"})

    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_search_next_commands_populated_for_unknown_gene() -> None:
    """next_commands carry search_passages tool suggestions for each close match."""
    repo = _make_brief_repo(rows=1)
    app = _make_indexed_app(repo, frozenset({"BRCA1", "BRCA2"}))

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get("/passages/search", params={"q": "diagnosis", "gene": "BRCA9"})

    assert resp.status_code == 400
    detail = resp.json()["detail"]
    cmds = detail.get("next_commands", [])
    assert len(cmds) > 0
    # Each command references search_passages
    assert all(cmd["tool"] == "search_passages" for cmd in cmds)
    # Each command has a gene argument from the suggestions
    genes_in_commands = [cmd["arguments"]["gene"] for cmd in cmds]
    assert any("BRCA" in g for g in genes_in_commands)


# ---------------------------------------------------------------------------
# ids_only mode tests (Task 5 — Spec D1)
# ---------------------------------------------------------------------------


def _make_ids_only_repo(passage_ids: list[str], sections: list[str] | None = None) -> Any:
    """Build a fake repo returning rows for the given passage_ids."""
    from unittest.mock import MagicMock

    _sections = sections or ["management"] * len(passage_ids)
    rows = [
        LexicalPassageRow(
            passage=PassageRow(
                nbk_id=pid.split(":")[0],
                passage_id=pid,
                chapter_section=sec,
                heading_path=f"{sec.capitalize()} > X",
                section_level=2,
                chunk_index=int(pid.split(":")[1]),
                text="Some passage text for testing purposes.",
                chapter_title="Chapter",
                chapter_last_updated=None,
                gene_symbols=("BRCA1",),
            ),
            phrase_rank=1.0,
            strict_rank=0.5,
            recall_rank=0.4,
            recall_overlap_count=1,
            lexical_rank=0.9 - i * 0.1,
            snippet=f"**bold** snippet {i}",
        )
        for i, (pid, sec) in enumerate(zip(passage_ids, _sections, strict=True))
    ]
    repo = MagicMock()
    repo.search_passages = AsyncMock(return_value=rows)
    repo.active_embedding_table = AsyncMock(return_value="t")
    repo.dense_scores_for_passages = AsyncMock(return_value={})
    repo._dense_candidates_filtered = AsyncMock(return_value=[])
    repo.fetch_passages_by_ids = AsyncMock(return_value={})
    return repo


@pytest.mark.asyncio
async def test_search_ids_only_mode_returns_lean_shape() -> None:
    """mode='ids_only' returns only the advertised five slim fields per result."""
    repo = _make_ids_only_repo(
        passage_ids=["NBK1247:0010", "NBK1247:0011"],
        sections=["management", "diagnosis"],
    )
    app = _make_brief_app(repo)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get(
            "/passages/search", params={"q": "BRCA1", "mode": "ids_only", "limit": 5}
        )

    assert resp.status_code == 200
    data = resp.json()
    assert "results" in data
    assert len(data["results"]) == 2
    first = data["results"][0]
    expected_keys = {
        "passage_id",
        "nbk_id",
        "rrf_score",
        "lexical_rank_position",
        "chapter_section",
    }
    assert set(first.keys()) == expected_keys
    assert all(set(result.keys()) == expected_keys for result in data["results"])
    assert first["passage_id"].startswith("NBK1247:")
    assert first["nbk_id"] == "NBK1247"
    assert isinstance(first["rrf_score"], (float, type(None)))
    assert first["lexical_rank_position"] == 1
    # Crucially, none of these keys appear:
    for forbidden in (
        "text",
        "snippet",
        "chapter_title",
        "score_breakdown",
        "recommended_citation",
        "heading_path_array",
        "passage_type",
        "passage_role",
        "table_id",
        "source_url",
    ):
        assert forbidden not in first, f"Forbidden key present: {forbidden}"
    assert "_meta" in data
    assert "corpus_version" in data["_meta"]
    diag = data["_meta"].get("diagnostics")
    assert diag is not None
    assert diag["rerank_used"] == "rrf"


@pytest.mark.asyncio
async def test_search_ids_only_mode_is_rejected_for_invalid_mode() -> None:
    """An unknown mode value is rejected with 422."""
    repo = _make_brief_repo(rows=1)
    app = _make_brief_app(repo)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get("/passages/search", params={"q": "BRCA1", "mode": "bogus_mode"})

    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_search_ids_only_mode_respects_limit() -> None:
    """ids_only mode respects the limit parameter."""
    repo = _make_ids_only_repo(
        passage_ids=[f"NBK1247:{i:04d}" for i in range(10)],
    )
    app = _make_brief_app(repo)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get(
            "/passages/search", params={"q": "BRCA1", "mode": "ids_only", "limit": 3}
        )

    assert resp.status_code == 200
    data = resp.json()
    assert len(data["results"]) <= 3


@pytest.mark.asyncio
async def test_search_ids_only_corpus_version_in_meta() -> None:
    """ids_only response carries _meta.corpus_version from app.state."""
    repo = _make_ids_only_repo(passage_ids=["NBK1247:0001"])
    app = _make_brief_app(repo)
    app.state.corpus_version = "2026-03-01"

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get("/passages/search", params={"q": "BRCA1", "mode": "ids_only"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["_meta"]["corpus_version"] == "2026-03-01"


# ---------------------------------------------------------------------------
# snippet_chars tests (Task 6 — Spec D2)
# ---------------------------------------------------------------------------


def _fake_lex_row(
    passage_id: str,
    *,
    section: str = "management",
    lexical_rank: float = 0.9,
    rrf_score: float | None = None,
    text: str = "A" * 5000,
    heading_path: str | None = None,
    chapter_title: str = "Chapter",
    chapter_last_updated: Any = None,
    passage_type: str = "narrative",
    table_id: str | None = None,
) -> LexicalPassageRow:
    """Build a LexicalPassageRow with controllable text for snippet_chars tests."""
    from datetime import date as _date

    last_updated: _date | None
    if isinstance(chapter_last_updated, str):
        last_updated = _date.fromisoformat(chapter_last_updated)
    else:
        last_updated = chapter_last_updated

    return LexicalPassageRow(
        passage=PassageRow(
            nbk_id=passage_id.split(":")[0],
            passage_id=passage_id,
            chapter_section=section,
            heading_path=heading_path
            if heading_path is not None
            else f"{section.capitalize()} > X",
            section_level=2,
            chunk_index=int(passage_id.split(":")[1]),
            text=text,
            chapter_title=chapter_title,
            chapter_last_updated=last_updated,
            gene_symbols=("BRCA1",),
            passage_type=passage_type,
            table_id=table_id,
        ),
        phrase_rank=1.0,
        strict_rank=0.5,
        recall_rank=0.4,
        recall_overlap_count=1,
        lexical_rank=lexical_rank,
        rrf_score=rrf_score,
        # snippet is None here; the fake repo populates it from snippet_max_words
    )


def _build_app_with_fake_repo(rows: list[LexicalPassageRow]) -> FastAPI:
    """Build a minimal FastAPI app backed by a fake repo that honours snippet_max_words."""
    from unittest.mock import AsyncMock, MagicMock

    from genereview_link.api.routes import passages as passages_routes

    def _make_search_passages(source_rows: list[LexicalPassageRow]) -> Any:
        """Return an async callable that injects a snippet scaled by snippet_max_words."""

        async def _search(
            query: str,
            *,
            gene_symbol: str | None = None,
            nbk_id: str | None = None,
            sections: list[str] | None = None,
            heading_path_contains: str | None = None,
            limit: int = 20,
            brief: bool = False,
            snippet_max_fragments: int = 2,
            snippet_max_words: int = 30,
            gene_role: str = "any",
        ) -> list[LexicalPassageRow]:
            del heading_path_contains
            if not brief:
                return source_rows
            # Produce a snippet whose length is proportional to snippet_max_words
            # so the test can verify that smaller params => shorter snippet.
            result = []
            for row in source_rows:
                snippet_text = row.passage.text[: snippet_max_words * 6]
                result.append(
                    LexicalPassageRow(
                        passage=row.passage,
                        phrase_rank=row.phrase_rank,
                        strict_rank=row.strict_rank,
                        recall_rank=row.recall_rank,
                        recall_overlap_count=row.recall_overlap_count,
                        lexical_rank=row.lexical_rank,
                        snippet=snippet_text,
                    )
                )
            return result

        return _search

    repo = MagicMock()
    repo.search_passages = _make_search_passages(rows)
    repo.active_embedding_table = AsyncMock(return_value="t")
    repo.dense_scores_for_passages = AsyncMock(return_value={})
    repo._dense_candidates_filtered = AsyncMock(return_value=[])
    repo.fetch_passages_by_ids = AsyncMock(return_value={})

    app = FastAPI()
    app.include_router(passages_routes.router)
    app.state.repository = repo
    app.state.embedder = FakeEmbeddingProvider(dim=384)
    return app


@pytest.mark.asyncio
async def test_search_snippet_chars_controls_brief_mode_snippet_size() -> None:
    """snippet_chars=80 produces shorter snippets than snippet_chars=800 for the
    same query against the same fake-repo result set."""
    rows = [
        _fake_lex_row(
            "NBK1247:0010",
            section="management",
            lexical_rank=0.9,
            text="A" * 5000,  # long text so ts_headline has room to expand
        )
    ]
    app = _build_app_with_fake_repo(rows)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp_small = await c.get(
            "/passages/search",
            params={"q": "BRCA1", "mode": "brief", "snippet_chars": 80, "limit": 1},
        )
        resp_big = await c.get(
            "/passages/search",
            params={"q": "BRCA1", "mode": "brief", "snippet_chars": 800, "limit": 1},
        )
    assert resp_small.status_code == resp_big.status_code == 200
    small_snippet = resp_small.json()["results"][0]["snippet"]
    big_snippet = resp_big.json()["results"][0]["snippet"]
    assert len(small_snippet) < len(big_snippet)


@pytest.mark.asyncio
async def test_search_snippet_chars_out_of_range_returns_422() -> None:
    app = _build_app_with_fake_repo([])

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        for value in (0, 79, 801, 5000):
            resp = await c.get(
                "/passages/search",
                params={"q": "x", "snippet_chars": value},
            )
            assert resp.status_code == 422, f"snippet_chars={value} should reject"


@pytest.mark.asyncio
async def test_lexical_variant_query_with_context_keeps_brca_hits() -> None:
    rows = [
        _fake_lex_row(
            "NBK1247:0010",
            section="management",
            lexical_rank=0.9,
            text="BRCA1 c.5266dupC founder variant in Ashkenazi populations.",
        )
    ]
    app = _build_app_with_fake_repo(rows)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        response = await c.get(
            "/passages/search",
            params={
                "q": "c.5266dupC BRCA1 founder variant Ashkenazi",
                "rerank": "lexical",
                "limit": 5,
            },
        )

    assert response.status_code == 200
    data = response.json()
    assert any(row["nbk_id"] == "NBK1247" for row in data["results"])


# ---------------------------------------------------------------------------
# _meta.dense_model_id + _meta.embedding_dim under include=score_breakdown
# (Task 10 — Spec G2)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_score_breakdown_surfaces_dense_model_id_and_embedding_dim() -> None:
    """When include=score_breakdown, _meta.dense_model_id + _meta.embedding_dim populate."""
    rows = [_fake_lex_row("NBK1247:0010", section="management", lexical_rank=0.9, rrf_score=0.04)]
    app = _build_app_with_fake_repo(rows)
    app.state.dense_model_id = "BAAI/bge-small-en-v1.5"
    app.state.embedding_dim = 384

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get(
            "/passages/search",
            params={"q": "BRCA1", "limit": 1, "include": "score_breakdown", "rerank": "rrf"},
        )
    assert resp.status_code == 200
    meta = resp.json()["_meta"]
    assert meta["dense_model_id"] == "BAAI/bge-small-en-v1.5"
    assert meta["embedding_dim"] == 384


@pytest.mark.asyncio
async def test_search_without_score_breakdown_omits_model_meta() -> None:
    """Without include=score_breakdown, _meta.dense_model_id and _meta.embedding_dim are None."""
    rows = [_fake_lex_row("NBK1247:0010", section="management", lexical_rank=0.9, rrf_score=0.04)]
    app = _build_app_with_fake_repo(rows)
    app.state.dense_model_id = "BAAI/bge-small-en-v1.5"
    app.state.embedding_dim = 384

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get("/passages/search", params={"q": "BRCA1", "limit": 1})
    assert resp.status_code == 200
    meta = resp.json()["_meta"]
    # dense_model_id and embedding_dim must be absent or None
    assert meta.get("dense_model_id") is None
    assert meta.get("embedding_dim") is None


# ---------------------------------------------------------------------------
# heading_path_array opt-in tests (Task 11 — Spec H1)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_heading_path_array_absent_by_default() -> None:
    """heading_path_array is absent from results unless include=heading_path_array."""
    rows = [
        _fake_lex_row(
            "NBK1247:0010",
            section="management",
            lexical_rank=0.9,
            heading_path="Management > Treatment > Targeted Therapies",
        )
    ]
    app = _build_app_with_fake_repo(rows)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get("/passages/search", params={"q": "BRCA1", "limit": 1})
    assert resp.status_code == 200
    result = resp.json()["results"][0]
    assert result.get("heading_path_array") is None


@pytest.mark.asyncio
async def test_search_heading_path_array_opt_in() -> None:
    """include=heading_path_array splits heading_path on ' > ' and returns the array."""
    rows = [
        _fake_lex_row(
            "NBK1247:0010",
            section="management",
            lexical_rank=0.9,
            heading_path="Management > Treatment > Targeted Therapies",
        )
    ]
    app = _build_app_with_fake_repo(rows)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get(
            "/passages/search",
            params={"q": "BRCA1", "limit": 1, "include": "heading_path_array"},
        )
    assert resp.status_code == 200
    arr = resp.json()["results"][0]["heading_path_array"]
    assert arr == ["Management", "Treatment", "Targeted Therapies"]


@pytest.mark.asyncio
async def test_search_ids_only_mode_never_includes_heading_path_array() -> None:
    """ids_only mode early-return is not affected by include=heading_path_array."""
    rows = [
        _fake_lex_row(
            "NBK1247:0010",
            section="management",
            lexical_rank=0.9,
            heading_path="Management > X",
        )
    ]
    app = _build_app_with_fake_repo(rows)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get(
            "/passages/search",
            params={"q": "BRCA1", "mode": "ids_only", "include": "heading_path_array"},
        )
    assert resp.status_code == 200
    first = resp.json()["results"][0]
    assert "heading_path_array" not in first


# ---------------------------------------------------------------------------
# recommended_citation + table_id tests (Task 12 — Spec I1)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_recommended_citation_format() -> None:
    """recommended_citation is always present and uses the canonical format."""
    rows = [
        _fake_lex_row(
            "NBK1247:0020",
            section="management",
            lexical_rank=0.9,
            chapter_title="BRCA1- and BRCA2-Associated HBOC",
            chapter_last_updated="2026-03-25",
        )
    ]
    app = _build_app_with_fake_repo(rows)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get("/passages/search", params={"q": "BRCA1", "limit": 1})
    assert resp.status_code == 200
    citation = resp.json()["results"][0]["recommended_citation"]
    assert citation == (
        "BRCA1- and BRCA2-Associated HBOC. NBK1247. Updated 2026-03-25. Passage NBK1247:0020."
    )


@pytest.mark.asyncio
async def test_search_recommended_citation_handles_null_date() -> None:
    """recommended_citation emits 'date n/a' when chapter_last_updated is None."""
    rows = [
        _fake_lex_row(
            "NBK9999:0001",
            section="diagnosis",
            lexical_rank=0.5,
            chapter_title="Unrevised Chapter",
            chapter_last_updated=None,
        )
    ]
    app = _build_app_with_fake_repo(rows)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get("/passages/search", params={"q": "x", "limit": 1})
    assert resp.status_code == 200
    citation = resp.json()["results"][0]["recommended_citation"]
    assert "Updated date n/a" in citation
    assert "Passage NBK9999:0001" in citation


@pytest.mark.asyncio
async def test_search_table_id_populated_for_table_type_hits() -> None:
    """table_id is populated for table-type passages and absent for narrative passages."""
    rows = [
        _fake_lex_row(
            "NBK1247:0030",
            section="management",
            lexical_rank=0.9,
            passage_type="table",
            table_id="mgmt.T.targeted_therapies",
        ),
        _fake_lex_row(
            "NBK1247:0031",
            section="management",
            lexical_rank=0.8,
            passage_type="narrative",
        ),
    ]
    app = _build_app_with_fake_repo(rows)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get("/passages/search", params={"q": "BRCA1", "limit": 2})
    assert resp.status_code == 200
    results = resp.json()["results"]
    by_id = {r["passage_id"]: r for r in results}
    assert by_id["NBK1247:0030"]["table_id"] == "mgmt.T.targeted_therapies"
    assert by_id["NBK1247:0031"].get("table_id") is None


@pytest.mark.asyncio
async def test_ids_only_mode_omits_recommended_citation_and_table_id() -> None:
    """ids_only early-return does not include recommended_citation or table_id."""
    rows = [
        _fake_lex_row(
            "NBK1247:0030",
            section="management",
            lexical_rank=0.9,
            passage_type="table",
            table_id="mgmt.T.x",
        )
    ]
    app = _build_app_with_fake_repo(rows)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get("/passages/search", params={"q": "x", "mode": "ids_only", "limit": 1})
    assert resp.status_code == 200
    first = resp.json()["results"][0]
    assert "recommended_citation" not in first
    assert "table_id" not in first


# ---------------------------------------------------------------------------
# source_url tests (Pass-3-A)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_source_url_present_on_every_hit() -> None:
    """source_url is chapter-level NCBI Bookshelf URL on every search hit."""
    repo = _make_brief_repo(rows=3)
    app = _make_brief_app(repo)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get("/passages/search", params={"q": "BRCA1"})
    assert resp.status_code == 200
    body = resp.json()
    for hit in body["results"]:
        assert hit["source_url"] == f"https://www.ncbi.nlm.nih.gov/books/{hit['nbk_id']}/"


@pytest.mark.asyncio
async def test_ids_only_mode_omits_source_url() -> None:
    """ids_only mode must not carry source_url."""
    repo = _make_ids_only_repo(passage_ids=["NBK1247:0010", "NBK1247:0011"])
    app = _make_brief_app(repo)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get("/passages/search", params={"q": "BRCA1", "mode": "ids_only"})
    assert resp.status_code == 200
    body = resp.json()
    for hit in body["results"]:
        assert "source_url" not in hit
