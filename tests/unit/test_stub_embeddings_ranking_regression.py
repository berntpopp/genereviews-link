"""Ranking regression: a stub embedding provider must never fuse into `rerank=rrf`.

The production symptom this reproduces, measured live on 2026-09-01:

    q = "BRCA1 breast cancer surveillance"
    #1  MTHFR homocystinuria (prenatal testing)   lexical_score 0,  lexical rank 265,
                                                  won on dense_rank 2
    #2  CHEK2 surveillance table                  lexical_score 7.5, lexical rank 3
    #3  CDH1 breast cancer                        lexical rank 5

A stub provider hashes the query into a 384-d vector that is uncorrelated with the stored
BGE vectors, so its "nearest neighbours" are arbitrary passages. Reciprocal-rank fusion
then treats those arbitrary passages as a second, independent evidence signal and promotes
them over genuinely matching lexical hits: 1/(60+265) + 1/(60+2) beats 1/(60+3).

The failure is therefore NOT "ranking is a bit worse". Correct answers are actively
displaced by unrelated ones, and the response still reports `dense_model_id:
"BAAI/bge-small-en-v1.5"`.

`test_the_fixture_reproduces_the_displacement_bug` deliberately asserts the BROKEN
behaviour with the gate forced open. Without it these tests would pass on the unfixed
code and this suite would not be a regression test at all.
"""

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
from genereview_link.retrieval.model_identity import BGE_MODEL_NAME
from genereview_link.retrieval.repository import (
    GeneReviewRepository,
    LexicalPassageRow,
    PassageRow,
)
from genereview_link.server_manager import UnifiedServerManager

QUERY = "BRCA1 breast cancer surveillance"

#: The genuinely relevant hit: strong lexical match, and the answer a reader wants.
CORRECT_ID = "NBK1:CHEK2-surveillance"

#: The unrelated passage the stub's random neighbours promoted in production.
DISPLACING_ID = "NBK9:MTHFR-prenatal"


class FakeClient:
    async def search_genereviews(self, *a: Any, **kw: Any) -> dict:
        return {"count": 0, "retmax": 20, "retstart": 0, "ids": [], "webenv": "", "querykey": ""}

    async def fetch_abstract(self, *a: Any, **kw: Any) -> dict:
        return {}

    async def get_all_links(self, *a: Any, **kw: Any) -> dict:
        return {"urls": []}

    async def scrape_genereview_comprehensive(self, *a: Any, **kw: Any) -> dict:
        return {"nbk_id": "1", "url": "", "title": "", "sections": {}, "metadata": {}}


def _passage(passage_id: str, nbk_id: str, text: str) -> PassageRow:
    # Every fixture passage shares one section and role so that RRF is the only thing
    # separating them: no section boost or role multiplier can explain the ordering.
    return PassageRow(
        nbk_id=nbk_id,
        passage_id=passage_id,
        chapter_section="management",
        heading_path="Management > Surveillance",
        section_level=2,
        chunk_index=0,
        text=text,
        passage_role="evidence",
    )


def _lexical(passage_id: str, nbk_id: str, text: str, rank: float) -> LexicalPassageRow:
    return LexicalPassageRow(
        passage=_passage(passage_id, nbk_id, text),
        phrase_rank=rank,
        strict_rank=rank,
        recall_rank=rank,
        recall_overlap_count=3,
        lexical_rank=rank,
    )


#: Three real lexical matches, best first.
LEXICAL_HITS = [
    _lexical(CORRECT_ID, "NBK1", "BRCA1-related breast cancer surveillance schedule.", 7.5),
    _lexical("NBK2:CDH1-breast", "NBK2", "CDH1 hereditary breast cancer surveillance.", 5.0),
    _lexical("NBK3:BRCA1-dx", "NBK3", "BRCA1 diagnosis and testing strategy.", 3.2),
]

#: Three arbitrary passages a stub's query vector happened to sit near. In production
#: these clustered in a 0.1594-0.1680 dense band -- the signature of random unit vectors
#: in 384 dimensions -- and carried zero lexical score.
DENSE_ONLY = {
    DISPLACING_ID: _passage(
        DISPLACING_ID, "NBK9", "MTHFR deficiency: prenatal testing considerations."
    ),
    "NBK8:TREM2-counsel": _passage("NBK8:TREM2-counsel", "NBK8", "TREM2: risk to family members."),
    "NBK7:SCARB2-counsel": _passage(
        "NBK7:SCARB2-counsel", "NBK7", "SCARB2: risk to family members."
    ),
}

DENSE_CANDIDATES = [
    {"passage_id": DISPLACING_ID, "dense_score": 0.1680},
    {"passage_id": "NBK8:TREM2-counsel", "dense_score": 0.1631},
    {"passage_id": "NBK7:SCARB2-counsel", "dense_score": 0.1594},
]


@pytest.fixture
def fake_repo() -> GeneReviewRepository:
    repo = AsyncMock(spec=GeneReviewRepository)
    repo.search_passages.return_value = list(LEXICAL_HITS)
    repo.active_embedding_table.return_value = "genereview_embeddings_bge384"
    repo._dense_candidates_filtered.return_value = list(DENSE_CANDIDATES)
    repo.fetch_passages_by_ids.return_value = dict(DENSE_ONLY)
    return repo


@pytest_asyncio.fixture
async def app(fake_repo: GeneReviewRepository) -> FastAPI:
    config = ServerConfig(transport="http", log_level="WARNING", enable_docs=False)
    fastapi_app = UnifiedServerManager().create_fastapi_app(config)

    async def _get_client() -> Any:
        yield FakeClient()

    async def _get_repo() -> GeneReviewRepository:
        return fake_repo

    async def _get_embedder() -> FakeEmbeddingProvider:
        return FakeEmbeddingProvider(dim=384)

    fastapi_app.dependency_overrides[get_managed_client] = _get_client
    fastapi_app.dependency_overrides[get_repository] = _get_repo
    fastapi_app.dependency_overrides[get_embedding_provider] = _get_embedder

    # What the fixed lifespan records for a stub provider.
    fastapi_app.state.embedding_provider_kind = "fake"
    fastapi_app.state.embedding_provider_real = False
    fastapi_app.state.dense_ranking_enabled = False
    fastapi_app.state.dense_model_id = "fake-embedding"
    fastapi_app.state.embedding_dim = 384
    return fastapi_app


@pytest_asyncio.fixture
async def http_client(app: FastAPI) -> AsyncClient:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


async def _search(client: AsyncClient, **params: Any) -> dict[str, Any]:
    response = await client.get("/passages/search", params={"q": QUERY, **params})
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


@pytest.mark.asyncio
async def test_the_fixture_reproduces_the_displacement_bug(
    app: FastAPI, http_client: AsyncClient
) -> None:
    """With the gate forced open, the stub's random neighbour wins -- as in production."""
    app.state.dense_ranking_enabled = True

    body = await _search(http_client)
    order = [hit["passage_id"] for hit in body["results"]]

    assert order[0] == DISPLACING_ID, (
        f"fixture no longer reproduces the bug, so passing it proves nothing: got {order}"
    )
    assert order.index(CORRECT_ID) > 0


@pytest.mark.asyncio
async def test_a_stub_provider_does_not_displace_the_correct_lexical_hit(
    http_client: AsyncClient,
) -> None:
    """The fix: with a stub provider the correct passage ranks first under rerank=rrf."""
    body = await _search(http_client)
    order = [hit["passage_id"] for hit in body["results"]]

    assert order[0] == CORRECT_ID, f"correct passage did not rank first: {order}"
    assert DISPLACING_ID not in order, (
        "an unrelated dense-only passage entered the results despite a stub provider"
    )


@pytest.mark.asyncio
async def test_a_stub_provider_reports_lexical_ranking_not_rrf(
    http_client: AsyncClient,
) -> None:
    """`rerank_used` must name the ranker that ran, not the one that was requested."""
    body = await _search(http_client)
    diagnostics = body["_meta"]["diagnostics"]

    assert diagnostics["rerank_used"] == "lexical"
    assert diagnostics["dense_candidate_count"] is None


@pytest.mark.asyncio
async def test_a_stub_provider_never_reports_the_reference_model(
    http_client: AsyncClient,
) -> None:
    """Reporting a model that is not loaded is misinformation, not a cosmetic defect."""
    body = await _search(http_client, include="score_breakdown")
    meta = body["_meta"]

    assert meta["dense_model_id"] != BGE_MODEL_NAME
    assert meta["dense_model_id"] == "fake-embedding"


@pytest.mark.asyncio
async def test_a_real_provider_still_fuses_dense_ranks(
    app: FastAPI, http_client: AsyncClient
) -> None:
    """The gate must not disable dense ranking for a genuine provider."""
    app.state.embedding_provider_real = True
    app.state.dense_ranking_enabled = True
    app.state.dense_model_id = BGE_MODEL_NAME

    body = await _search(http_client)

    assert body["_meta"]["diagnostics"]["rerank_used"] == "rrf"
    assert body["_meta"]["diagnostics"]["dense_candidate_count"] == len(DENSE_CANDIDATES)
