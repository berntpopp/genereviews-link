"""Unit tests for GeneReviewService.

The service is exercised with a fake EutilsClient injected via constructor.
This isolates the service logic from HTTP, NCBI flakiness, and parsing details.
"""

from __future__ import annotations

from typing import Any

import pytest

from genereview_link.retrieval.repository import ChapterRow
from genereview_link.services.genereview_service import (
    DataNotFoundError,
    GeneReviewService,
)


class FakeEutilsClient:
    """Minimal stand-in for EutilsClient that records calls and returns canned data."""

    def __init__(
        self,
        *,
        search_pmid: str | None = "12345",
        book_url: str | None = "https://www.ncbi.nlm.nih.gov/books/NBK1247/",
        scrape_book: dict[str, Any] | None = None,
        search_results: dict[str, Any] | None = None,
        abstract: dict[str, Any] | None = None,
        all_links: dict[str, Any] | None = None,
        comprehensive: dict[str, Any] | None = None,
        abstract_error: Exception | None = None,
        links_error: Exception | None = None,
        comprehensive_error: Exception | None = None,
    ) -> None:
        self._search_pmid = search_pmid
        self._book_url = book_url
        self._scrape_book = scrape_book if scrape_book is not None else {}
        self._search_results = (
            search_results
            if search_results is not None
            else {"ids": ["12345"], "count": 1, "retmax": 1, "retstart": 0}
        )
        self._abstract = abstract
        self._all_links = all_links
        self._comprehensive = comprehensive
        self._abstract_error = abstract_error
        self._links_error = links_error
        self._comprehensive_error = comprehensive_error
        self.closed = False
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    async def search_genereview_pmid(self, gene_symbol: str) -> str | None:
        self.calls.append(("search_genereview_pmid", (gene_symbol,)))
        return self._search_pmid

    async def get_book_url_from_pmid(self, pubmed_id: str) -> str | None:
        self.calls.append(("get_book_url_from_pmid", (pubmed_id,)))
        return self._book_url

    async def scrape_genereview_book(self, book_url: str) -> dict[str, Any]:
        self.calls.append(("scrape_genereview_book", (book_url,)))
        return self._scrape_book

    async def search_genereviews(self, gene_symbol: str, retmax: int = 20) -> dict[str, Any]:
        self.calls.append(("search_genereviews", (gene_symbol, retmax)))
        return self._search_results

    async def fetch_abstract(self, pubmed_id: str) -> dict[str, Any]:
        self.calls.append(("fetch_abstract", (pubmed_id,)))
        if self._abstract_error is not None:
            raise self._abstract_error
        return self._abstract or {}

    async def get_all_links(self, pubmed_id: str) -> dict[str, Any]:
        self.calls.append(("get_all_links", (pubmed_id,)))
        if self._links_error is not None:
            raise self._links_error
        return self._all_links or {"urls": []}

    async def scrape_genereview_comprehensive(self, book_url: str) -> dict[str, Any]:
        self.calls.append(("scrape_genereview_comprehensive", (book_url,)))
        if self._comprehensive_error is not None:
            raise self._comprehensive_error
        return self._comprehensive or {}

    async def close(self) -> None:
        self.closed = True


def _make_service(**kwargs: Any) -> tuple[GeneReviewService, FakeEutilsClient]:
    client = FakeEutilsClient(**kwargs)
    # Cast to satisfy the EutilsClient type; we only rely on duck-typing here.
    service = GeneReviewService(client=client)  # type: ignore[arg-type]
    return service, client


class TestGetGenereview:
    """Tests for the legacy ``_get_genereview_impl`` workflow."""

    @pytest.mark.asyncio
    async def test_raises_when_no_pmid_found(self) -> None:
        service, _ = _make_service(search_pmid=None)
        with pytest.raises(DataNotFoundError, match="GeneReview not found"):
            await service.get_genereview("UNKNOWNGENE")

    @pytest.mark.asyncio
    async def test_raises_when_no_book_url(self) -> None:
        service, _ = _make_service(search_pmid="42", book_url=None)
        with pytest.raises(DataNotFoundError, match="NCBI Bookshelf"):
            await service.get_genereview("BRCA1")

    @pytest.mark.asyncio
    async def test_raises_when_scrape_returns_empty(self) -> None:
        service, _ = _make_service(scrape_book={})
        with pytest.raises(DataNotFoundError, match="Could not scrape"):
            await service.get_genereview("BRCA1")

    @pytest.mark.asyncio
    async def test_close_delegates_to_client(self) -> None:
        service, fake = _make_service()
        await service.close()
        assert fake.closed is True


class TestGetGenereviewComprehensive:
    """Tests for the enhanced ``_get_genereview_comprehensive_impl`` workflow."""

    @pytest.mark.asyncio
    async def test_raises_when_no_ids(self) -> None:
        service, _ = _make_service(
            search_results={"ids": [], "count": 0, "retmax": 0, "retstart": 0}
        )
        with pytest.raises(DataNotFoundError, match="GeneReview not found"):
            await service.get_genereview_comprehensive("UNKNOWN")

    @pytest.mark.asyncio
    async def test_raises_when_no_book_urls_anywhere(self) -> None:
        # No urls from links and fallback also fails.
        service, _ = _make_service(
            all_links={"urls": []},
            book_url=None,
        )
        with pytest.raises(DataNotFoundError, match="NCBI Bookshelf"):
            await service.get_genereview_comprehensive("BRCA1")

    @pytest.mark.asyncio
    async def test_uses_book_url_from_links_when_available(self) -> None:
        service, fake = _make_service(
            all_links={"urls": ["https://www.ncbi.nlm.nih.gov/books/NBK1247/"]},
            comprehensive={
                "nbk_id": "NBK1247",
                "url": "https://www.ncbi.nlm.nih.gov/books/NBK1247/",
                "title": "BRCA1 GeneReview",
                "sections": {
                    "summary": {"title": "Summary", "content": "summary content"},
                    "diagnosis": {"title": "Diagnosis", "content": "diagnosis content"},
                    "management": {"title": "Management", "content": "management content"},
                    "extra": {"title": "Extra", "content": "extra content"},
                },
                "metadata": {"authors": "Some Authors", "update_info": "2024"},
            },
            abstract={
                "pmid": "12345",
                "title": "Abstract Title",
                "abstract": "Abstract body",
                "authors": ["A", "B"],
                "journal": "JMed",
                "publication_date": "2024",
            },
        )

        result = await service.get_genereview_comprehensive("BRCA1")

        assert result.gene_symbol == "BRCA1"
        assert result.pubmed_id == "12345"
        assert result.title.text == "BRCA1 GeneReview"
        assert result.summary is not None
        assert result.diagnosis is not None
        assert result.management is not None
        assert "extra" in result.other_sections
        assert result.abstract_data is not None
        assert result.abstract_data.pmid == "12345"
        assert result.all_links is not None
        assert result.full_text_data is not None
        assert result.full_text_data.nbk_id == "NBK1247"
        # Should not have called get_book_url_from_pmid since links provided URL.
        assert not any(c[0] == "get_book_url_from_pmid" for c in fake.calls)

    @pytest.mark.asyncio
    async def test_fulltext_data_canonicalizes_bare_nbk_id(self) -> None:
        service, _ = _make_service(
            all_links={"urls": ["https://www.ncbi.nlm.nih.gov/books/NBK501979/"]},
            comprehensive={
                "nbk_id": "501979",
                "url": "https://www.ncbi.nlm.nih.gov/books/NBK501979/",
                "title": "GRIN2B GeneReview",
                "sections": {"summary": {"title": "Summary", "content": "summary content"}},
                "metadata": {},
            },
        )

        result = await service.get_genereview_comprehensive("GRIN2B")

        assert result.full_text_data is not None
        assert result.full_text_data.nbk_id == "NBK501979"

    @pytest.mark.asyncio
    async def test_link_lookup_still_resolves_book_url_when_links_excluded(self) -> None:
        service, fake = _make_service(
            all_links={"urls": ["https://www.ncbi.nlm.nih.gov/books/NBK501979/"]},
            book_url=None,
            comprehensive={
                "nbk_id": "NBK501979",
                "url": "https://www.ncbi.nlm.nih.gov/books/NBK501979/",
                "title": "GRIN2B GeneReview",
                "sections": {"summary": {"title": "Summary", "content": "summary content"}},
                "metadata": {},
            },
        )

        result = await service.get_genereview_comprehensive(
            "GRIN2B",
            include_abstract=False,
            include_links=False,
            include_fulltext=True,
        )

        assert result.book_url == "https://www.ncbi.nlm.nih.gov/books/NBK501979/"
        assert result.all_links is None
        assert ("get_all_links", ("12345",)) in fake.calls

    @pytest.mark.asyncio
    async def test_falls_back_to_book_url_lookup_when_no_book_links(self) -> None:
        service, fake = _make_service(
            all_links={"urls": ["https://example.com/other"]},
            book_url="https://www.ncbi.nlm.nih.gov/books/NBK1247/",
            comprehensive={
                "url": "https://www.ncbi.nlm.nih.gov/books/NBK1247/",
                "title": "T",
                "sections": {},
                "metadata": {},
            },
        )

        result = await service.get_genereview_comprehensive("BRCA1")
        assert result.book_url == "https://www.ncbi.nlm.nih.gov/books/NBK1247/"
        assert any(c[0] == "get_book_url_from_pmid" for c in fake.calls)

    @pytest.mark.asyncio
    async def test_continues_when_abstract_fetch_fails(self) -> None:
        service, _ = _make_service(
            abstract_error=RuntimeError("boom"),
            all_links={"urls": ["https://www.ncbi.nlm.nih.gov/books/NBK1247/"]},
            comprehensive={
                "url": "https://www.ncbi.nlm.nih.gov/books/NBK1247/",
                "title": "T",
                "sections": {},
                "metadata": {},
            },
        )

        result = await service.get_genereview_comprehensive("BRCA1")
        assert result.abstract_data is None
        # Title comes from the comprehensive scrape.
        assert result.title.text == "T"

    @pytest.mark.asyncio
    async def test_continues_when_links_fetch_fails(self) -> None:
        service, _ = _make_service(
            links_error=RuntimeError("link boom"),
            book_url="https://www.ncbi.nlm.nih.gov/books/NBK1247/",
            comprehensive={
                "url": "https://www.ncbi.nlm.nih.gov/books/NBK1247/",
                "title": "Fallback Title",
                "sections": {},
                "metadata": {},
            },
        )
        result = await service.get_genereview_comprehensive("BRCA1")
        assert result.all_links is None
        assert result.book_url == "https://www.ncbi.nlm.nih.gov/books/NBK1247/"
        assert result.title.text == "Fallback Title"

    @pytest.mark.asyncio
    async def test_continues_when_comprehensive_scrape_fails(self) -> None:
        service, _ = _make_service(
            comprehensive_error=RuntimeError("scrape boom"),
            all_links={"urls": ["https://www.ncbi.nlm.nih.gov/books/NBK1247/"]},
            # Basic scrape fallback returns nothing useful either.
            scrape_book={},
            abstract={
                "pmid": "12345",
                "title": "Abstract Used As Title",
                "abstract": "x",
                "authors": [],
                "journal": "",
                "publication_date": "",
            },
        )

        result = await service.get_genereview_comprehensive("BRCA1")
        # Title should fall back to abstract.title since both scrapes produced nothing.
        assert result.title.text == "Abstract Used As Title"
        assert result.full_text_data is None

    @pytest.mark.asyncio
    async def test_default_title_when_nothing_available(self) -> None:
        service, _ = _make_service(
            comprehensive={"url": "u", "title": "", "sections": {}, "metadata": {}},
            all_links={"urls": ["https://www.ncbi.nlm.nih.gov/books/NBK1247/"]},
            abstract=None,
        )
        result = await service.get_genereview_comprehensive("BRCA1")
        assert result.title.text == "GeneReview for BRCA1"

    @pytest.mark.asyncio
    async def test_repository_chapter_title_survives_empty_scrape_title(self) -> None:
        service, _ = _make_service(
            comprehensive={
                "url": "https://www.ncbi.nlm.nih.gov/books/NBK1247/",
                "title": "",
                "sections": {},
                "metadata": {},
            },
            abstract={
                "pmid": "20301425",
                "title": "Abstract Title",
                "abstract": "x",
                "authors": [],
                "journal": "",
                "publication_date": "",
            },
        )
        chapter = ChapterRow(
            nbk_id="NBK1247",
            short_name="brca1",
            title="Repository Chapter Title",
            pubmed_id="20301425",
            gene_symbols=("BRCA1",),
            omim_ids=(),
            authors=None,
            initial_pub_date=None,
            last_updated_date=None,
        )

        result = await service.get_genereview_comprehensive_uncached("BRCA1", chapter=chapter)

        assert result.title.text == "Repository Chapter Title"

    @pytest.mark.asyncio
    async def test_indexed_comprehensive_method_caches_by_chapter(self) -> None:
        service, fake = _make_service(
            comprehensive={
                "url": "https://www.ncbi.nlm.nih.gov/books/NBK1247/",
                "title": "Indexed Title",
                "sections": {},
                "metadata": {},
            },
        )
        chapter = ChapterRow(
            nbk_id="NBK1247",
            short_name="brca1",
            title="Repository Chapter Title",
            pubmed_id="20301425",
            gene_symbols=("BRCA1",),
            omim_ids=(),
            authors=None,
            initial_pub_date=None,
            last_updated_date=None,
        )

        first = await service.get_genereview_comprehensive_indexed("BRCA1", chapter=chapter)
        second = await service.get_genereview_comprehensive_indexed("BRCA1", chapter=chapter)

        assert first.title.text == second.title.text == "Indexed Title"
        assert [call[0] for call in fake.calls].count("scrape_genereview_comprehensive") == 1

    @pytest.mark.asyncio
    async def test_optional_data_can_be_disabled(self) -> None:
        service, fake = _make_service(
            book_url="https://www.ncbi.nlm.nih.gov/books/NBK1247/",
        )
        result = await service.get_genereview_comprehensive(
            "BRCA1",
            include_abstract=False,
            include_links=False,
            include_fulltext=False,
        )
        assert result.abstract_data is None
        assert result.all_links is None
        assert result.full_text_data is None
        # Should not have called the optional client methods.
        called = {c[0] for c in fake.calls}
        assert "fetch_abstract" not in called
        assert "get_all_links" not in called
        assert "scrape_genereview_comprehensive" not in called

    @pytest.mark.asyncio
    async def test_cached_comprehensive_method_rejects_repository_kwarg(self) -> None:
        service, _ = _make_service()

        with pytest.raises(TypeError, match="repository"):
            await service.get_genereview_comprehensive("BRCA1", repository=object())
