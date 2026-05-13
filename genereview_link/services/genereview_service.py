"""
Service layer for GeneReview business logic.

Orchestrates data retrieval and processing workflows.
"""

from datetime import timedelta

from async_lru import alru_cache

from genereview_link.api.eutils_client import EutilsClient
from genereview_link.config import settings
from genereview_link.logging_config import get_logger
from genereview_link.models.genereview_models import (
    AbstractData,
    FullTextData,
    FullTextMetadata,
    GeneReview,
    GeneReviewSection,
    LinkData,
)
from genereview_link.retrieval.repository import ChapterRow

logger = get_logger(__name__)


class DataNotFoundError(Exception):
    """Custom exception for when data is not found from the external source."""

    pass


class GeneReviewService:
    """Service layer for fetching and processing GeneReviews data."""

    def __init__(self, client: EutilsClient | None = None):
        """Initialize the GeneReview service.

        Args:
            client: Optional EutilsClient instance, creates new one if None.
        """
        self.client = client or EutilsClient()
        self.cache_ttl = timedelta(hours=settings.CACHE_TTL_HOURS)

        # Apply the cache decorator to both implementation methods
        self.get_genereview = alru_cache(maxsize=settings.CACHE_SIZE)(self._get_genereview_impl)
        self.get_genereview_comprehensive = alru_cache(maxsize=settings.CACHE_SIZE)(
            self._get_genereview_comprehensive_cached_impl
        )
        self.get_genereview_comprehensive_indexed = alru_cache(maxsize=settings.CACHE_SIZE)(
            self._get_genereview_comprehensive_indexed_impl
        )

    async def _get_genereview_impl(self, gene_symbol: str) -> GeneReview:
        """Implement the GeneReview fetching logic."""
        # 1. Search for the PubMed ID
        pubmed_id = await self.client.search_genereview_pmid(gene_symbol)
        if not pubmed_id:
            raise DataNotFoundError(f"GeneReview not found for gene: {gene_symbol}")

        # 2. Get the Bookshelf URL
        book_url = await self.client.get_book_url_from_pmid(pubmed_id)
        if not book_url:
            raise DataNotFoundError(f"Could not find NCBI Bookshelf link for PMID: {pubmed_id}")

        # 3. Scrape the content
        scraped_data = await self.client.scrape_genereview_book(book_url)
        if not scraped_data or "title" not in scraped_data:
            raise DataNotFoundError(f"Could not scrape content from URL: {book_url}")

        # 4. Populate the Pydantic model
        title = scraped_data.pop("title")["content"]
        summary = scraped_data.pop("summary", None)
        diagnosis = scraped_data.pop("diagnosis", None)
        management = scraped_data.pop("management", None)

        return GeneReview(
            gene_symbol=gene_symbol.upper(),
            pubmed_id=pubmed_id,
            book_url=book_url,
            title=title,
            summary=GeneReviewSection(**summary) if summary else None,
            diagnosis=GeneReviewSection(**diagnosis) if diagnosis else None,
            management=GeneReviewSection(**management) if management else None,
            other_sections={k: GeneReviewSection(**v) for k, v in scraped_data.items()},
        )

    async def _get_genereview_comprehensive_cached_impl(
        self,
        gene_symbol: str,
        include_abstract: bool = True,
        include_links: bool = True,
        include_fulltext: bool = True,
    ) -> GeneReview:
        return await self._get_genereview_comprehensive_impl(
            gene_symbol,
            include_abstract=include_abstract,
            include_links=include_links,
            include_fulltext=include_fulltext,
        )

    async def _get_genereview_comprehensive_impl(
        self,
        gene_symbol: str,
        include_abstract: bool = True,
        include_links: bool = True,
        include_fulltext: bool = True,
        *,
        chapter: ChapterRow | None = None,
    ) -> GeneReview:
        """Fetch all available data for a GeneReview."""
        if chapter is not None and chapter.pubmed_id:
            pubmed_id = chapter.pubmed_id
            book_url = f"https://www.ncbi.nlm.nih.gov/books/{chapter.nbk_id}/"
            title = chapter.title
        else:
            # 1. Search for GeneReviews
            search_results = await self.client.search_genereviews(gene_symbol, retmax=1)
            if not search_results["ids"]:
                raise DataNotFoundError(f"GeneReview not found for gene: {gene_symbol}")

            pubmed_id = search_results["ids"][0]
            book_url = None
            title = ""

        # 2. Get abstract data if requested
        abstract_data = None
        if include_abstract:
            try:
                abstract_result = await self.client.fetch_abstract(pubmed_id)
                if abstract_result:
                    abstract_data = AbstractData(
                        pmid=abstract_result.get("pmid", pubmed_id),
                        title=abstract_result.get("title", ""),
                        abstract=abstract_result.get("abstract", ""),
                        authors=abstract_result.get("authors", []),
                        journal=abstract_result.get("journal", ""),
                        publication_date=abstract_result.get("publication_date", ""),
                    )
            except Exception as e:
                logger.warning(f"Could not fetch abstract for PMID {pubmed_id}: {e}")

        # 3. Get all links if requested
        all_links = None
        book_urls = [book_url] if book_url else []
        if include_links:
            try:
                links_result = await self.client.get_all_links(pubmed_id)
                all_links = LinkData(urls=links_result.get("urls", []))
                if not book_urls:
                    # Extract book URLs from all URLs
                    book_urls = [
                        url
                        for url in links_result.get("urls", [])
                        if "ncbi.nlm.nih.gov/books/" in url
                    ]
            except Exception as e:
                logger.warning(f"Could not fetch links for PMID {pubmed_id}: {e}")

        # Fallback to original method if no book URLs found
        if not book_urls:
            book_url = await self.client.get_book_url_from_pmid(pubmed_id)
            if book_url:
                book_urls = [book_url]

        if not book_urls:
            raise DataNotFoundError(f"Could not find NCBI Bookshelf link for PMID: {pubmed_id}")

        # Use the first book URL
        book_url = book_urls[0]

        # 4. Get comprehensive full text data if requested
        full_text_data = None
        sections = {}

        if include_fulltext:
            try:
                fulltext_result = await self.client.scrape_genereview_comprehensive(book_url)
                if not fulltext_result.get("error"):
                    # Convert sections
                    sections_data = {}
                    for key, section_data in fulltext_result.get("sections", {}).items():
                        sections_data[key] = GeneReviewSection(
                            title=section_data["title"],
                            content=section_data["content"],
                        )

                    # Convert metadata
                    metadata_dict = fulltext_result.get("metadata", {})
                    metadata = FullTextMetadata(
                        authors=metadata_dict.get("authors"),
                        update_info=metadata_dict.get("update_info"),
                    )

                    full_text_data = FullTextData(
                        nbk_id=fulltext_result.get("nbk_id"),
                        url=fulltext_result.get("url", book_url),
                        title=fulltext_result.get("title", ""),
                        sections=sections_data,
                        metadata=metadata,
                    )

                    scraped_title = fulltext_result.get("title", "")
                    if scraped_title:
                        title = scraped_title
                    sections = sections_data
            except Exception as e:
                logger.warning(f"Could not scrape full text from {book_url}: {e}")

        # Fallback: use basic scraping if comprehensive failed
        if include_fulltext and not sections:
            try:
                scraped_data = await self.client.scrape_genereview_book(book_url)
                if scraped_data and "title" in scraped_data:
                    scraped_title = scraped_data.pop("title")["content"]
                    if scraped_title:
                        title = scraped_title
                    # Convert remaining sections
                    for key, section_data in scraped_data.items():
                        sections[key] = GeneReviewSection(**section_data)
            except Exception as e:
                logger.warning(f"Basic scraping also failed for {book_url}: {e}")

        # Use abstract title as fallback
        if not title and abstract_data and abstract_data.title:
            title = abstract_data.title

        if not title:
            title = f"GeneReview for {gene_symbol}"

        # Extract specific sections for backward compatibility
        summary = sections.pop("summary", None)
        diagnosis = sections.pop("diagnosis", None)
        management = sections.pop("management", None)

        return GeneReview(
            gene_symbol=gene_symbol.upper(),
            pubmed_id=pubmed_id,
            book_url=book_url,
            title=title,
            summary=summary,
            diagnosis=diagnosis,
            management=management,
            other_sections=sections,
            abstract_data=abstract_data,
            all_links=all_links,
            full_text_data=full_text_data,
        )

    async def _get_genereview_comprehensive_indexed_impl(
        self,
        gene_symbol: str,
        include_abstract: bool = True,
        include_links: bool = True,
        include_fulltext: bool = True,
        *,
        chapter: ChapterRow,
    ) -> GeneReview:
        """Fetch a repository-resolved chapter through a chapter-keyed cache."""
        return await self._get_genereview_comprehensive_impl(
            gene_symbol,
            include_abstract=include_abstract,
            include_links=include_links,
            include_fulltext=include_fulltext,
            chapter=chapter,
        )

    async def get_genereview_comprehensive_uncached(
        self,
        gene_symbol: str,
        include_abstract: bool = True,
        include_links: bool = True,
        include_fulltext: bool = True,
        *,
        chapter: ChapterRow | None = None,
    ) -> GeneReview:
        """Fetch a comprehensive GeneReview without using the service cache.

        Route-level orchestration uses this when it has already resolved an
        indexed chapter for the request. The cached public method remains for
        legacy live lookups that do not pass request-scoped repository rows.
        """
        return await self._get_genereview_comprehensive_impl(
            gene_symbol,
            include_abstract=include_abstract,
            include_links=include_links,
            include_fulltext=include_fulltext,
            chapter=chapter,
        )

    async def close(self) -> None:
        """Close the underlying HTTPX client."""
        await self.client.close()
