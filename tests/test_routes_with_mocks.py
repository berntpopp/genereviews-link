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
from genereview_link.api.errors import StructuredHTTPException
from genereview_link.api.orchestration import live_corpus_version, stamp_response_version
from genereview_link.config import ServerConfig
from genereview_link.mcp.untrusted_content import fence_untrusted_text
from genereview_link.models.genereview_models import AbstractData
from genereview_link.server_manager import UnifiedServerManager
from genereview_link.services.genereview_service import DataNotFoundError, GeneReviewService
from genereview_link.services.service_manager import get_managed_service


def assert_structured_error_detail(body: dict[str, Any]) -> dict[str, Any]:
    detail = body["detail"]
    assert detail["code"]
    assert detail["recovery_hint"]
    assert "next_commands" in detail
    return detail


class FakeClient:
    """Pluggable fake for EutilsClient route dependencies."""

    def __init__(
        self,
        *,
        search_result: dict[str, Any] | Exception | None = None,
        abstract: dict[str, Any] | Exception | None = None,
        links: dict[str, Any] | Exception | None = None,
        fulltext: dict[str, Any] | Exception | None = None,
        book_url: str | Exception | None = None,
    ) -> None:
        self._search_result = search_result
        self._abstract = abstract
        self._links = links
        self._fulltext = fulltext
        self._book_url = book_url
        self.book_url_calls: list[str] = []
        self.abstract_calls: list[str] = []

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
        self.abstract_calls.append(pubmed_id)
        if isinstance(self._abstract, Exception):
            raise self._abstract
        return self._abstract or {}

    async def get_all_links(self, pubmed_id: str) -> dict[str, Any]:
        if isinstance(self._links, Exception):
            raise self._links
        return self._links or {"urls": []}

    async def get_book_url_from_pmid(self, pubmed_id: str) -> str | None:
        self.book_url_calls.append(pubmed_id)
        if isinstance(self._book_url, Exception):
            raise self._book_url
        return self._book_url

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
    async def test_uses_repository_first(
        self, app: FastAPI, http_client: AsyncClient, fake_client: FakeClient
    ) -> None:
        class FakeChapter:
            nbk_id = "NBK1247"
            short_name = "brca1"
            title = "BRCA1- and BRCA2-Associated HBOC"
            pubmed_id = "20301425"
            gene_symbols = ("BRCA1", "BRCA2")

        class FakeRepo:
            async def get_chapters_by_gene(self, gene_symbol: str) -> list[FakeChapter]:
                assert gene_symbol == "BRCA1"
                return [FakeChapter()]

        fake_client._search_result = RuntimeError("live client should not be called")
        app.state.repository = FakeRepo()
        app.state.corpus_version = "2026-05-10-r6"

        resp = await http_client.get("/search/BRCA1")

        assert resp.status_code == 200
        body = resp.json()
        assert body["ids"] == ["20301425"]
        assert body["corpus_version"] == "2026-05-10-r6"
        assert body["_meta"]["corpus_version"] == "2026-05-10-r6"

    @pytest.mark.asyncio
    async def test_uses_all_repository_chapter_matches(
        self, app: FastAPI, http_client: AsyncClient, fake_client: FakeClient
    ) -> None:
        class FakeChapterOne:
            pubmed_id = "20301425"

        class FakeChapterTwo:
            pubmed_id = "99999999"

        class FakeRepo:
            async def get_chapters_by_gene(
                self, gene_symbol: str
            ) -> list[FakeChapterOne | FakeChapterTwo]:
                assert gene_symbol == "BRCA1"
                return [FakeChapterOne(), FakeChapterTwo()]

        fake_client._search_result = RuntimeError("live client should not be called")
        app.state.repository = FakeRepo()
        app.state.corpus_version = "2026-05-10-r6"

        resp = await http_client.get("/search/BRCA1")

        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 2
        assert body["ids"] == ["20301425", "99999999"]
        assert body["corpus_version"] == "2026-05-10-r6"
        assert body["_meta"]["corpus_version"] == "2026-05-10-r6"

    @pytest.mark.asyncio
    async def test_live_fallback_stamps_live_version(
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
        app.state.corpus_version = "2026-05-10-r6"

        resp = await http_client.get("/search/BRCA1")

        assert resp.status_code == 200
        body = resp.json()
        assert body["ids"] == ["1"]
        # live NCBI search (repo miss / no repo) -> live provenance, not null.
        assert body["corpus_version"].startswith("live:")
        assert body["_meta"]["corpus_version"] == body["corpus_version"]

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
        detail = assert_structured_error_detail(resp.json())
        assert detail["next_commands"][0]["tool"] == "search_passages"

    @pytest.mark.asyncio
    async def test_empty_search_result_includes_agent_recovery_hints(
        self, http_client: AsyncClient, fake_client: FakeClient
    ) -> None:
        fake_client._search_result = {
            "count": 0,
            "retmax": 20,
            "retstart": 0,
            "ids": [],
            "webenv": "",
            "querykey": "",
        }

        resp = await http_client.get("/search/NO_SUCH_GENE")

        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 0
        assert body["ids"] == []
        assert body["recovery_hint"]
        assert body["_meta"]["next_commands"] == [
            {"tool": "search_passages", "arguments": {"gene": "NO_SUCH_GENE", "q": "NO_SUCH_GENE"}}
        ]


class TestAbstractRoute:
    @pytest.mark.asyncio
    async def test_returns_abstract(
        self, app: FastAPI, http_client: AsyncClient, fake_client: FakeClient
    ) -> None:
        app.state.corpus_version = "2026-05-10-r6"
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
        # get_abstract always fetches live from PubMed -> version reflects live provenance,
        # never the local corpus version (which the retrieval did not use).
        assert body["corpus_version"].startswith("live:")
        assert body["_meta"]["corpus_version"] == body["corpus_version"]

    @pytest.mark.asyncio
    async def test_abstract_404_when_empty(
        self, app: FastAPI, http_client: AsyncClient, fake_client: FakeClient
    ) -> None:
        fake_client._abstract = {}
        resp = await http_client.get("/abstract/99")
        assert resp.status_code == 404
        assert_structured_error_detail(resp.json())

    @pytest.mark.asyncio
    async def test_abstract_500_on_error(
        self, app: FastAPI, http_client: AsyncClient, fake_client: FakeClient
    ) -> None:
        fake_client._abstract = RuntimeError("boom")
        resp = await http_client.get("/abstract/1")
        assert resp.status_code == 502
        detail = assert_structured_error_detail(resp.json())
        assert detail["code"] == "upstream_ncbi_unavailable"

    @pytest.mark.asyncio
    async def test_abstract_rejects_non_numeric_pubmed_id_before_client_call(
        self, http_client: AsyncClient, fake_client: FakeClient
    ) -> None:
        fake_client._abstract = RuntimeError("live client should not be called")

        resp = await http_client.get("/abstract/not_a_real_pmid")

        assert resp.status_code == 422
        detail = assert_structured_error_detail(resp.json())
        assert detail["code"] == "invalid_pubmed_id"
        assert fake_client.abstract_calls == []

    @pytest.mark.asyncio
    async def test_abstract_reraises_structured_http_exception(
        self, app: FastAPI, http_client: AsyncClient, fake_client: FakeClient
    ) -> None:
        fake_client._abstract = StructuredHTTPException(
            status_code=409,
            code="custom_abstract_error",
            message="custom",
            recovery_hint="use the custom recovery path",
            next_commands=[{"tool": "custom_tool", "arguments": {"pmid": "1"}}],
        )
        resp = await http_client.get("/abstract/1")
        assert resp.status_code == 409
        detail = assert_structured_error_detail(resp.json())
        assert detail["code"] == "custom_abstract_error"
        assert detail["next_commands"][0]["tool"] == "custom_tool"


class TestLinksRoute:
    @pytest.mark.asyncio
    async def test_returns_links(
        self, app: FastAPI, http_client: AsyncClient, fake_client: FakeClient
    ) -> None:
        app.state.corpus_version = "2026-05-10-r6"
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
        # get_links always fetches live from NCBI -> version reflects live provenance.
        assert body["corpus_version"].startswith("live:")
        assert body["_meta"]["corpus_version"] == body["corpus_version"]

    @pytest.mark.asyncio
    async def test_links_500_on_error(
        self, app: FastAPI, http_client: AsyncClient, fake_client: FakeClient
    ) -> None:
        fake_client._links = RuntimeError("boom")
        resp = await http_client.get("/links/1")
        assert resp.status_code == 502
        detail = assert_structured_error_detail(resp.json())
        assert detail["code"] == "upstream_ncbi_unavailable"

    @pytest.mark.asyncio
    async def test_links_reraises_structured_http_exception(
        self, app: FastAPI, http_client: AsyncClient, fake_client: FakeClient
    ) -> None:
        fake_client._links = StructuredHTTPException(
            status_code=409,
            code="custom_links_error",
            message="custom",
            recovery_hint="use the custom recovery path",
            next_commands=[{"tool": "custom_tool", "arguments": {"pmid": "1"}}],
        )
        resp = await http_client.get("/links/1")
        assert resp.status_code == 409
        detail = assert_structured_error_detail(resp.json())
        assert detail["code"] == "custom_links_error"
        assert detail["next_commands"][0]["tool"] == "custom_tool"


class TestFulltextRoute:
    @pytest.mark.asyncio
    async def test_returns_fulltext(
        self, app: FastAPI, http_client: AsyncClient, fake_client: FakeClient
    ) -> None:
        app.state.corpus_version = "2026-05-10-r6"
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
        assert body["nbk_id"] == "NBK1247"
        assert "summary" in body["sections"]
        assert body["_meta"]["attribution"].startswith("GeneReviews")
        # get_fulltext always live-scrapes NCBI Bookshelf -> version reflects live provenance.
        assert body["corpus_version"].startswith("live:")
        assert body["_meta"]["corpus_version"] == body["corpus_version"]

    @pytest.mark.asyncio
    async def test_fulltext_400_when_invalid_id(
        self, app: FastAPI, http_client: AsyncClient
    ) -> None:
        resp = await http_client.get("/fulltext/not-a-number")
        assert resp.status_code == 400
        assert_structured_error_detail(resp.json())

    @pytest.mark.asyncio
    async def test_fulltext_404_when_scrape_returns_error(
        self, app: FastAPI, http_client: AsyncClient, fake_client: FakeClient
    ) -> None:
        fake_client._fulltext = {"error": "page not found"}
        resp = await http_client.get("/fulltext/NBK99999")
        assert resp.status_code == 404
        assert_structured_error_detail(resp.json())

    @pytest.mark.asyncio
    async def test_fulltext_502_on_client_error(
        self, app: FastAPI, http_client: AsyncClient, fake_client: FakeClient
    ) -> None:
        fake_client._fulltext = RuntimeError("boom")
        resp = await http_client.get("/fulltext/NBK1247")
        assert resp.status_code == 502
        detail = assert_structured_error_detail(resp.json())
        assert detail["code"] == "upstream_ncbi_unavailable"

    @pytest.mark.asyncio
    async def test_fulltext_reraises_structured_http_exception(
        self, app: FastAPI, http_client: AsyncClient, fake_client: FakeClient
    ) -> None:
        fake_client._fulltext = StructuredHTTPException(
            status_code=409,
            code="custom_fulltext_error",
            message="custom",
            recovery_hint="use the custom recovery path",
            next_commands=[{"tool": "custom_tool", "arguments": {"nbk_id": "NBK1247"}}],
        )
        resp = await http_client.get("/fulltext/NBK1247")
        assert resp.status_code == 409
        detail = assert_structured_error_detail(resp.json())
        assert detail["code"] == "custom_fulltext_error"
        assert detail["next_commands"][0]["tool"] == "custom_tool"

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
        assert sub["title"]["text"] == "Clinical Features"
        assert sub["content"]["kind"] == "untrusted_text"
        assert sub["content"]["text"] == "cf"
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

    @pytest.mark.asyncio
    async def test_fulltext_references_is_json_array(
        self, app: FastAPI, http_client: AsyncClient, fake_client: FakeClient
    ) -> None:
        """Regression for #34: metadata.references must be a JSON array, not a string."""
        fake_client._fulltext = {
            "nbk_id": "1247",
            "url": "https://www.ncbi.nlm.nih.gov/books/NBK1247/",
            "title": "T",
            "sections": {"summary": {"title": "Summary", "content": "s"}},
            "metadata": {
                "references": [
                    "Smith AB et al. J Genet. 2020;1:1-10.",
                    "Jones CD et al. Nat Genet. 2021;2:11-20.",
                ]
            },
        }
        resp = await http_client.get("/fulltext/NBK1247")
        assert resp.status_code == 200
        body = resp.json()
        refs = body["metadata"]["references"]
        assert isinstance(refs, list), (
            f"metadata.references in JSON response must be an array, got {type(refs).__name__!r}"
        )
        assert len(refs) == 2
        # v1.1: each reference is a fenced untrusted_text object.
        assert all(r["kind"] == "untrusted_text" for r in refs)
        assert refs[0]["text"] == "Smith AB et al. J Genet. 2020;1:1-10."


class TestGenereviewRoute:
    @pytest.mark.asyncio
    async def test_uses_repository_chapter_book_url_without_live_resolver(
        self, app: FastAPI, http_client: AsyncClient, fake_client: FakeClient
    ) -> None:
        class FakeChapter:
            nbk_id = "NBK1247"
            short_name = "brca1"
            title = "BRCA1- and BRCA2-Associated HBOC"
            pubmed_id = "20301425"
            gene_symbols = ("BRCA1", "BRCA2")

        class FakeRepo:
            async def get_chapter_by_gene(self, gene_symbol: str) -> FakeChapter:
                assert gene_symbol == "BRCA1"
                return FakeChapter()

        fake_client._search_result = {"ids": ["20301425"]}
        fake_client._book_url = RuntimeError("live resolver should not be called")
        app.state.repository = FakeRepo()
        app.state.corpus_version = "2026-05-10-r6"

        real_service = GeneReviewService(client=fake_client)

        async def _get_service() -> Any:
            yield real_service

        app.dependency_overrides[get_managed_service] = _get_service

        resp = await http_client.get("/genereview/BRCA1")

        assert resp.status_code == 200
        body = resp.json()
        assert body["gene_symbol"] == "BRCA1"
        assert body["pubmed_id"] == "20301425"
        assert body["book_url"] == "https://www.ncbi.nlm.nih.gov/books/NBK1247/"
        assert body["corpus_version"] == "2026-05-10-r6"
        assert body["_meta"]["corpus_version"] == "2026-05-10-r6"
        assert fake_client.book_url_calls == []

    @pytest.mark.asyncio
    async def test_indexed_chapter_degrades_to_minimal_summary_when_service_fails(
        self, app: FastAPI, http_client: AsyncClient
    ) -> None:
        class FakeChapter:
            nbk_id = "NBK501979"
            short_name = "grin2b"
            title = "GRIN2B-Related Neurodevelopmental Disorder"
            pubmed_id = "29851452"
            gene_symbols = ("GRIN2B",)

        class FakeRepo:
            async def get_chapter_by_gene(self, gene_symbol: str) -> FakeChapter:
                assert gene_symbol == "GRIN2B"
                return FakeChapter()

        class FakeService:
            async def get_genereview_comprehensive_indexed(self, *args: Any, **kwargs: Any) -> Any:
                raise DataNotFoundError("live enrichment failed")

        async def _get_service() -> Any:
            yield FakeService()

        app.state.repository = FakeRepo()
        app.state.corpus_version = "2026-05-10-r6"
        app.dependency_overrides[get_managed_service] = _get_service

        resp = await http_client.get("/genereview/GRIN2B?include_fulltext=true")

        assert resp.status_code == 200
        body = resp.json()
        assert body["gene_symbol"] == "GRIN2B"
        assert body["pubmed_id"] == "29851452"
        assert body["book_url"] == "https://www.ncbi.nlm.nih.gov/books/NBK501979/"
        assert body["title"]["text"] == "GRIN2B-Related Neurodevelopmental Disorder"
        assert body["_meta"]["corpus_version"] == "2026-05-10-r6"

    @pytest.mark.asyncio
    async def test_repo_miss_live_fallback_stamps_live_version(
        self, app: FastAPI, http_client: AsyncClient, fake_client: FakeClient
    ) -> None:
        class FakeRepo:
            async def get_chapter_by_gene(self, gene_symbol: str) -> None:
                assert gene_symbol == "BRCA1"
                return None

        fake_client._search_result = {"ids": ["20301425"]}
        fake_client._book_url = "https://www.ncbi.nlm.nih.gov/books/NBK1247/"
        app.state.repository = FakeRepo()
        app.state.corpus_version = "2026-05-10-r6"

        real_service = GeneReviewService(client=fake_client)

        async def _get_service() -> Any:
            yield real_service

        app.dependency_overrides[get_managed_service] = _get_service

        resp = await http_client.get("/genereview/BRCA1")

        assert resp.status_code == 200
        body = resp.json()
        assert body["gene_symbol"] == "BRCA1"
        assert body["pubmed_id"] == "20301425"
        assert body["book_url"] == "https://www.ncbi.nlm.nih.gov/books/NBK1247/"
        # repo miss -> live NCBI fallback -> version reflects live provenance, not null.
        assert body["corpus_version"].startswith("live:")
        assert body["_meta"]["corpus_version"] == body["corpus_version"]

    @pytest.mark.asyncio
    async def test_absent_repository_live_fallback_stamps_live_version(
        self, app: FastAPI, http_client: AsyncClient, fake_client: FakeClient
    ) -> None:
        fake_client._search_result = {"ids": ["20301425"]}
        fake_client._book_url = "https://www.ncbi.nlm.nih.gov/books/NBK1247/"
        app.state.corpus_version = "2026-05-10-r6"

        real_service = GeneReviewService(client=fake_client)

        async def _get_service() -> Any:
            yield real_service

        app.dependency_overrides[get_managed_service] = _get_service

        resp = await http_client.get("/genereview/BRCA1")

        assert resp.status_code == 200
        body = resp.json()
        # absent repository -> live NCBI fallback -> version reflects live provenance, not null.
        assert body["corpus_version"].startswith("live:")
        assert body["_meta"]["corpus_version"] == body["corpus_version"]

    @pytest.mark.asyncio
    async def test_returns_404_when_service_raises(self, fake_client: FakeClient) -> None:
        config = ServerConfig(transport="http", log_level="WARNING", enable_docs=False)
        manager = UnifiedServerManager()
        fastapi_app = manager.create_fastapi_app(config)

        class FakeService:
            async def get_genereview_comprehensive_uncached(self, *args: Any, **kwargs: Any) -> Any:
                raise DataNotFoundError("nope")

        async def _get_service() -> Any:
            yield FakeService()

        fastapi_app.dependency_overrides[get_managed_service] = _get_service

        transport = ASGITransport(app=fastapi_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/genereview/UNKNOWN")
            assert resp.status_code == 404
            detail = assert_structured_error_detail(resp.json())
            assert detail["next_commands"][0]["tool"] == "search_passages"

    @pytest.mark.asyncio
    async def test_returns_500_on_unexpected_error(self) -> None:
        config = ServerConfig(transport="http", log_level="WARNING", enable_docs=False)
        manager = UnifiedServerManager()
        fastapi_app = manager.create_fastapi_app(config)

        class FakeService:
            async def get_genereview_comprehensive_uncached(self, *args: Any, **kwargs: Any) -> Any:
                raise RuntimeError("boom")

        async def _get_service() -> Any:
            yield FakeService()

        fastapi_app.dependency_overrides[get_managed_service] = _get_service

        transport = ASGITransport(app=fastapi_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/genereview/BRCA1")
            assert resp.status_code == 500
            detail = assert_structured_error_detail(resp.json())
            assert detail["next_commands"][0]["tool"] == "search_passages"

    @pytest.mark.asyncio
    async def test_returns_200_on_success(self) -> None:
        from genereview_link.models.genereview_models import GeneReview

        config = ServerConfig(transport="http", log_level="WARNING", enable_docs=False)
        manager = UnifiedServerManager()
        fastapi_app = manager.create_fastapi_app(config)

        class FakeService:
            async def get_genereview_comprehensive_uncached(
                self, *args: Any, **kwargs: Any
            ) -> GeneReview:
                return GeneReview(
                    gene_symbol="BRCA1",
                    pubmed_id="1",
                    book_url="https://www.ncbi.nlm.nih.gov/books/NBK1247/",
                    title=fence_untrusted_text(
                        "T", source="genereviews", record_id="NBK1247#title"
                    ),
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
        assert body["_meta"]["corpus_version"] == body["corpus_version"]
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
        assert body["_meta"]["corpus_version"] == body["corpus_version"]
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
        assert body["_meta"]["corpus_version"] == body["corpus_version"]
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
        assert body["_meta"]["corpus_version"] == body["corpus_version"]
        assert "license" not in body

    @pytest.mark.asyncio
    async def test_search_no_fresh_live_search_stamps_live_version(
        self, app: FastAPI, http_client: AsyncClient, fake_client: FakeClient
    ) -> None:
        """With no corpus, the live NCBI search stamps live provenance, not null."""
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
        assert body["corpus_version"].startswith("live:")
        # License never appears on per-record responses (dedicated /license endpoint).
        assert "license" not in body


class TestOrchestrationHelpers:
    def test_stamp_response_version_updates_top_level_and_meta(self) -> None:
        response = AbstractData(
            pmid="1",
            title=fence_untrusted_text("T", source="genereviews", record_id="1#title"),
            abstract=fence_untrusted_text("A", source="genereviews", record_id="1#doc"),
            authors=[],
            journal=fence_untrusted_text("J", source="genereviews", record_id="1#journal"),
            publication_date="2024",
        )

        stamp_response_version(response, corpus_version="2026-05-13")

        assert response.corpus_version == "2026-05-13"
        assert response.meta.corpus_version == "2026-05-13"

    def test_live_corpus_version_uses_live_prefix(self) -> None:
        assert live_corpus_version().startswith("live:")
