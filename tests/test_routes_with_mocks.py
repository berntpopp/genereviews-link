"""Route-level tests using FastAPI dependency overrides.

These tests exercise each route's handler body without hitting NCBI by
swapping the EutilsClient / GeneReviewService dependencies for fakes.
"""

from __future__ import annotations

from typing import Any

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from genereview_link.api.client_manager import get_managed_client
from genereview_link.config import ServerConfig
from genereview_link.server_manager import UnifiedServerManager
from genereview_link.services.genereview_service import DataNotFoundError
from genereview_link.services.service_manager import get_managed_service


class FakeClient:
    """Pluggable fake for EutilsClient route dependencies."""

    def __init__(
        self,
        *,
        search_result: dict[str, Any] | Exception | None = None,
        abstract: dict[str, Any] | Exception | None = None,
        links: dict[str, Any] | Exception | None = None,
        fulltext: dict[str, Any] | Exception | None = None,
    ) -> None:
        self._search_result = search_result
        self._abstract = abstract
        self._links = links
        self._fulltext = fulltext

    async def search_genereviews(self, gene_symbol: str, retmax: int = 20) -> dict[str, Any]:
        if isinstance(self._search_result, Exception):
            raise self._search_result
        return self._search_result or {
            "count": 0,
            "retmax": retmax,
            "retstart": 0,
            "ids": [],
            "webenv": "",
            "querykey": "",
        }

    async def fetch_abstract(self, pubmed_id: str) -> dict[str, Any]:
        if isinstance(self._abstract, Exception):
            raise self._abstract
        return self._abstract or {}

    async def get_all_links(self, pubmed_id: str) -> dict[str, Any]:
        if isinstance(self._links, Exception):
            raise self._links
        return self._links or {"urls": []}

    async def scrape_genereview_comprehensive(self, book_url: str) -> dict[str, Any]:
        if isinstance(self._fulltext, Exception):
            raise self._fulltext
        return self._fulltext or {
            "nbk_id": "1247",
            "url": book_url,
            "title": "",
            "sections": {},
            "metadata": {},
        }


@pytest.fixture
def fake_client() -> FakeClient:
    return FakeClient()


@pytest_asyncio.fixture
async def app(fake_client: FakeClient) -> FastAPI:
    """Create a fresh app with dependencies overridden."""
    config = ServerConfig(transport="http", log_level="WARNING", enable_docs=False)
    manager = UnifiedServerManager()
    fastapi_app = manager.create_fastapi_app(config)

    async def _get_client() -> Any:
        yield fake_client

    fastapi_app.dependency_overrides[get_managed_client] = _get_client
    return fastapi_app


@pytest_asyncio.fixture
async def http_client(app: FastAPI):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


class TestSearchRoute:
    @pytest.mark.asyncio
    async def test_returns_search_result(
        self, app: FastAPI, http_client: AsyncClient, fake_client: FakeClient
    ) -> None:
        fake_client._search_result = {
            "count": 1,
            "retmax": 20,
            "retstart": 0,
            "ids": ["1"],
            "webenv": "e",
            "querykey": "k",
        }
        resp = await http_client.get("/search/BRCA1")
        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 1
        assert body["ids"] == ["1"]

    @pytest.mark.asyncio
    async def test_search_500_on_client_error(
        self, app: FastAPI, http_client: AsyncClient, fake_client: FakeClient
    ) -> None:
        fake_client._search_result = RuntimeError("boom")
        resp = await http_client.get("/search/BRCA1")
        assert resp.status_code == 500


class TestAbstractRoute:
    @pytest.mark.asyncio
    async def test_returns_abstract(
        self, app: FastAPI, http_client: AsyncClient, fake_client: FakeClient
    ) -> None:
        fake_client._abstract = {
            "pmid": "1",
            "title": "T",
            "abstract": "A",
            "authors": ["Doe"],
            "journal": "J",
            "publication_date": "2024",
        }
        resp = await http_client.get("/abstract/1")
        assert resp.status_code == 200
        body = resp.json()
        assert body["pmid"] == "1"
        assert body["_meta"]["attribution"].startswith("GeneReviews")

    @pytest.mark.asyncio
    async def test_abstract_404_when_empty(
        self, app: FastAPI, http_client: AsyncClient, fake_client: FakeClient
    ) -> None:
        fake_client._abstract = {}
        resp = await http_client.get("/abstract/99")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_abstract_500_on_error(
        self, app: FastAPI, http_client: AsyncClient, fake_client: FakeClient
    ) -> None:
        fake_client._abstract = RuntimeError("boom")
        resp = await http_client.get("/abstract/1")
        assert resp.status_code == 500


class TestLinksRoute:
    @pytest.mark.asyncio
    async def test_returns_links(
        self, app: FastAPI, http_client: AsyncClient, fake_client: FakeClient
    ) -> None:
        fake_client._links = {
            "urls": ["https://example.com/a"],
            "link_entries": [
                {
                    "url": "https://example.com/a",
                    "link_type": "llinks",
                    "provider": "Example",
                }
            ],
            "by_type": {"llinks": ["https://example.com/a"]},
        }
        resp = await http_client.get("/links/1")
        assert resp.status_code == 200
        body = resp.json()
        assert body["urls"] == ["https://example.com/a"]
        assert body["_meta"]["attribution"].startswith("GeneReviews")
        assert body["link_entries"][0]["link_type"] == "llinks"
        assert body["by_type"]["llinks"] == ["https://example.com/a"]

    @pytest.mark.asyncio
    async def test_links_500_on_error(
        self, app: FastAPI, http_client: AsyncClient, fake_client: FakeClient
    ) -> None:
        fake_client._links = RuntimeError("boom")
        resp = await http_client.get("/links/1")
        assert resp.status_code == 500


class TestFulltextRoute:
    @pytest.mark.asyncio
    async def test_returns_fulltext(
        self, app: FastAPI, http_client: AsyncClient, fake_client: FakeClient
    ) -> None:
        fake_client._fulltext = {
            "nbk_id": "1247",
            "url": "https://www.ncbi.nlm.nih.gov/books/NBK1247/",
            "title": "T",
            "sections": {"summary": {"title": "Summary", "content": "stuff"}},
            "metadata": {"authors": "X"},
        }
        resp = await http_client.get("/fulltext/NBK1247")
        assert resp.status_code == 200
        body = resp.json()
        assert body["nbk_id"] == "1247"
        assert "summary" in body["sections"]
        assert body["_meta"]["attribution"].startswith("GeneReviews")

    @pytest.mark.asyncio
    async def test_fulltext_400_when_invalid_id(
        self, app: FastAPI, http_client: AsyncClient
    ) -> None:
        resp = await http_client.get("/fulltext/not-a-number")
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_fulltext_404_when_scrape_returns_error(
        self, app: FastAPI, http_client: AsyncClient, fake_client: FakeClient
    ) -> None:
        fake_client._fulltext = {"error": "page not found"}
        resp = await http_client.get("/fulltext/NBK99999")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_fulltext_returns_all_sections_when_no_filter(
        self, app: FastAPI, http_client: AsyncClient, fake_client: FakeClient
    ) -> None:
        fake_client._fulltext = {
            "nbk_id": "1247",
            "url": "https://www.ncbi.nlm.nih.gov/books/NBK1247/",
            "title": "T",
            "sections": {
                "summary": {"title": "Summary", "content": "s"},
                "diagnosis": {"title": "Diagnosis", "content": "d"},
                "management": {"title": "Management", "content": "m"},
            },
            "metadata": {},
        }
        resp = await http_client.get("/fulltext/NBK1247")
        assert resp.status_code == 200
        body = resp.json()
        assert set(body["sections"].keys()) == {"summary", "diagnosis", "management"}

    @pytest.mark.asyncio
    async def test_fulltext_filter_single_section(
        self, app: FastAPI, http_client: AsyncClient, fake_client: FakeClient
    ) -> None:
        fake_client._fulltext = {
            "nbk_id": "1247",
            "url": "https://www.ncbi.nlm.nih.gov/books/NBK1247/",
            "title": "T",
            "sections": {
                "summary": {"title": "Summary", "content": "s"},
                "diagnosis": {"title": "Diagnosis", "content": "d"},
                "management": {"title": "Management", "content": "m"},
            },
            "metadata": {},
        }
        resp = await http_client.get("/fulltext/NBK1247?sections=diagnosis")
        assert resp.status_code == 200
        body = resp.json()
        assert list(body["sections"].keys()) == ["diagnosis"]

    @pytest.mark.asyncio
    async def test_fulltext_filter_multiple_sections(
        self, app: FastAPI, http_client: AsyncClient, fake_client: FakeClient
    ) -> None:
        fake_client._fulltext = {
            "nbk_id": "1247",
            "url": "https://www.ncbi.nlm.nih.gov/books/NBK1247/",
            "title": "T",
            "sections": {
                "summary": {"title": "Summary", "content": "s"},
                "diagnosis": {"title": "Diagnosis", "content": "d"},
                "management": {"title": "Management", "content": "m"},
            },
            "metadata": {},
        }
        resp = await http_client.get("/fulltext/NBK1247?sections=summary,diagnosis")
        assert resp.status_code == 200
        body = resp.json()
        assert set(body["sections"].keys()) == {"summary", "diagnosis"}

    @pytest.mark.asyncio
    async def test_fulltext_filter_fuzzy_substring_match(
        self, app: FastAPI, http_client: AsyncClient, fake_client: FakeClient
    ) -> None:
        fake_client._fulltext = {
            "nbk_id": "1247",
            "url": "https://www.ncbi.nlm.nih.gov/books/NBK1247/",
            "title": "T",
            "sections": {
                "summary": {"title": "Summary", "content": "s"},
                "clinical_summary": {"title": "Clinical Summary", "content": "cs"},
                "diagnosis": {"title": "Diagnosis", "content": "d"},
            },
            "metadata": {},
        }
        resp = await http_client.get("/fulltext/NBK1247?sections=summary")
        assert resp.status_code == 200
        body = resp.json()
        # 'summary' matches its exact key AND 'clinical_summary' via substring
        assert set(body["sections"].keys()) == {"summary", "clinical_summary"}

    @pytest.mark.asyncio
    async def test_fulltext_filter_unknown_section_returns_empty(
        self, app: FastAPI, http_client: AsyncClient, fake_client: FakeClient
    ) -> None:
        fake_client._fulltext = {
            "nbk_id": "1247",
            "url": "https://www.ncbi.nlm.nih.gov/books/NBK1247/",
            "title": "T",
            "sections": {
                "summary": {"title": "Summary", "content": "s"},
                "diagnosis": {"title": "Diagnosis", "content": "d"},
            },
            "metadata": {},
        }
        resp = await http_client.get("/fulltext/NBK1247?sections=nope_does_not_exist")
        assert resp.status_code == 200
        body = resp.json()
        assert body["sections"] == {}

    @pytest.mark.asyncio
    async def test_fulltext_empty_sections_param_returns_all(
        self, app: FastAPI, http_client: AsyncClient, fake_client: FakeClient
    ) -> None:
        fake_client._fulltext = {
            "nbk_id": "1247",
            "url": "https://www.ncbi.nlm.nih.gov/books/NBK1247/",
            "title": "T",
            "sections": {
                "summary": {"title": "Summary", "content": "s"},
                "diagnosis": {"title": "Diagnosis", "content": "d"},
            },
            "metadata": {},
        }
        resp = await http_client.get("/fulltext/NBK1247?sections=")
        assert resp.status_code == 200
        body = resp.json()
        assert set(body["sections"].keys()) == {"summary", "diagnosis"}

    @pytest.mark.asyncio
    async def test_fulltext_propagates_level_and_subsections(
        self, app: FastAPI, http_client: AsyncClient, fake_client: FakeClient
    ) -> None:
        fake_client._fulltext = {
            "nbk_id": "1247",
            "url": "https://www.ncbi.nlm.nih.gov/books/NBK1247/",
            "title": "T",
            "sections": {
                "diagnosis": {
                    "title": "Diagnosis",
                    "content": "d",
                    "level": 2,
                    "subsections": {
                        "clinical_features": {
                            "title": "Clinical Features",
                            "content": "cf",
                            "level": 3,
                            "subsections": {},
                        }
                    },
                }
            },
            "metadata": {},
        }
        resp = await http_client.get("/fulltext/NBK1247")
        assert resp.status_code == 200
        body = resp.json()
        diagnosis = body["sections"]["diagnosis"]
        assert diagnosis["level"] == 2
        assert "clinical_features" in diagnosis["subsections"]
        sub = diagnosis["subsections"]["clinical_features"]
        assert sub["level"] == 3
        assert sub["title"] == "Clinical Features"
        assert sub["content"] == "cf"
        assert sub["subsections"] == {}

    @pytest.mark.asyncio
    async def test_fulltext_defaults_level_when_missing(
        self, app: FastAPI, http_client: AsyncClient, fake_client: FakeClient
    ) -> None:
        # Confirms legacy scraper payloads without level/subsections still work.
        fake_client._fulltext = {
            "nbk_id": "1247",
            "url": "https://www.ncbi.nlm.nih.gov/books/NBK1247/",
            "title": "T",
            "sections": {"summary": {"title": "Summary", "content": "s"}},
            "metadata": {},
        }
        resp = await http_client.get("/fulltext/NBK1247")
        assert resp.status_code == 200
        body = resp.json()
        assert body["sections"]["summary"]["level"] == 1
        assert body["sections"]["summary"]["subsections"] == {}


class TestGenereviewRoute:
    @pytest.mark.asyncio
    async def test_returns_404_when_service_raises(self, fake_client: FakeClient) -> None:
        config = ServerConfig(transport="http", log_level="WARNING", enable_docs=False)
        manager = UnifiedServerManager()
        fastapi_app = manager.create_fastapi_app(config)

        class FakeService:
            async def get_genereview_comprehensive(self, *args: Any, **kwargs: Any) -> Any:
                raise DataNotFoundError("nope")

        async def _get_service() -> Any:
            yield FakeService()

        fastapi_app.dependency_overrides[get_managed_service] = _get_service

        transport = ASGITransport(app=fastapi_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/genereview/UNKNOWN")
            assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_returns_500_on_unexpected_error(self) -> None:
        config = ServerConfig(transport="http", log_level="WARNING", enable_docs=False)
        manager = UnifiedServerManager()
        fastapi_app = manager.create_fastapi_app(config)

        class FakeService:
            async def get_genereview_comprehensive(self, *args: Any, **kwargs: Any) -> Any:
                raise RuntimeError("boom")

        async def _get_service() -> Any:
            yield FakeService()

        fastapi_app.dependency_overrides[get_managed_service] = _get_service

        transport = ASGITransport(app=fastapi_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/genereview/BRCA1")
            assert resp.status_code == 500

    @pytest.mark.asyncio
    async def test_returns_200_on_success(self) -> None:
        from genereview_link.models.genereview_models import GeneReview

        config = ServerConfig(transport="http", log_level="WARNING", enable_docs=False)
        manager = UnifiedServerManager()
        fastapi_app = manager.create_fastapi_app(config)

        class FakeService:
            async def get_genereview_comprehensive(self, *args: Any, **kwargs: Any) -> GeneReview:
                return GeneReview(
                    gene_symbol="BRCA1",
                    pubmed_id="1",
                    book_url="https://www.ncbi.nlm.nih.gov/books/NBK1247/",
                    title="T",
                )

        async def _get_service() -> Any:
            yield FakeService()

        fastapi_app.dependency_overrides[get_managed_service] = _get_service

        transport = ASGITransport(app=fastapi_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/genereview/BRCA1")
            assert resp.status_code == 200
            body = resp.json()
            assert body["gene_symbol"] == "BRCA1"


# ---------------------------------------------------------------------------
# Phase 5: ?fresh=true tests — verify corpus_version and license fields
# ---------------------------------------------------------------------------


class TestFreshParam:
    """Assert that ?fresh=true returns 200 with corpus_version set and license non-null."""

    @pytest.mark.asyncio
    async def test_search_fresh_sets_corpus_version(
        self, app: FastAPI, http_client: AsyncClient, fake_client: FakeClient
    ) -> None:
        fake_client._search_result = {
            "count": 0,
            "retmax": 20,
            "retstart": 0,
            "ids": [],
            "webenv": "",
            "querykey": "",
        }
        resp = await http_client.get("/search/BRCA1?fresh=true")
        assert resp.status_code == 200
        body = resp.json()
        assert body["corpus_version"] is not None
        assert body["corpus_version"].startswith("live:")
        # License is NOT inlined on per-record responses; callers fetch /license once.
        assert "license" not in body

    @pytest.mark.asyncio
    async def test_abstract_fresh_sets_corpus_version(
        self, app: FastAPI, http_client: AsyncClient, fake_client: FakeClient
    ) -> None:
        fake_client._abstract = {
            "pmid": "1",
            "title": "T",
            "abstract": "A",
            "authors": [],
            "journal": "J",
            "publication_date": "2024",
        }
        resp = await http_client.get("/abstract/1?fresh=true")
        assert resp.status_code == 200
        body = resp.json()
        assert body["corpus_version"] is not None
        assert body["corpus_version"].startswith("live:")
        assert "license" not in body

    @pytest.mark.asyncio
    async def test_links_fresh_sets_corpus_version(
        self, app: FastAPI, http_client: AsyncClient, fake_client: FakeClient
    ) -> None:
        fake_client._links = {"urls": ["https://example.com"]}
        resp = await http_client.get("/links/1?fresh=true")
        assert resp.status_code == 200
        body = resp.json()
        assert body["corpus_version"] is not None
        assert body["corpus_version"].startswith("live:")
        assert "license" not in body

    @pytest.mark.asyncio
    async def test_fulltext_fresh_sets_corpus_version(
        self, app: FastAPI, http_client: AsyncClient, fake_client: FakeClient
    ) -> None:
        fake_client._fulltext = {
            "nbk_id": "1247",
            "url": "https://www.ncbi.nlm.nih.gov/books/NBK1247/",
            "title": "T",
            "sections": {},
            "metadata": {},
        }
        resp = await http_client.get("/fulltext/NBK1247?fresh=true")
        assert resp.status_code == 200
        body = resp.json()
        assert body["corpus_version"] is not None
        assert body["corpus_version"].startswith("live:")
        assert "license" not in body

    @pytest.mark.asyncio
    async def test_search_no_fresh_has_null_corpus_version(
        self, app: FastAPI, http_client: AsyncClient, fake_client: FakeClient
    ) -> None:
        """Without ?fresh the corpus_version should be None (index not yet populated)."""
        fake_client._search_result = {
            "count": 0,
            "retmax": 20,
            "retstart": 0,
            "ids": [],
            "webenv": "",
            "querykey": "",
        }
        resp = await http_client.get("/search/BRCA1")
        assert resp.status_code == 200
        body = resp.json()
        assert body["corpus_version"] is None
        # License never appears on per-record responses (dedicated /license endpoint).
        assert "license" not in body
