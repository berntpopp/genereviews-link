"""
Service layer for GeneReview business logic.

Orchestrates data retrieval and processing workflows.
"""

import re
from typing import Any

from async_lru import alru_cache

from genereview_link.api.eutils_client import EutilsClient
from genereview_link.config import settings
from genereview_link.logging_config import get_logger
from genereview_link.mcp.untrusted_content import UntrustedText, fence_untrusted_text
from genereview_link.models.genereview_models import (
    AbstractData,
    FencedGeneReviewSection,
    FullTextData,
    FullTextMetadata,
    GeneReview,
    GeneReviewSection,
    LinkData,
)
from genereview_link.models.sections import canonicalize_nbk_id
from genereview_link.retrieval.repository import ChapterRow

logger = get_logger(__name__)

_NBK_IN_URL = re.compile(r"/books/(NBK\d+)")


def _book_urls_from_links(links_result: dict[str, object]) -> list[str]:
    urls = links_result.get("urls", [])
    if not isinstance(urls, list):
        return []
    return [url for url in urls if isinstance(url, str) and "ncbi.nlm.nih.gov/books/" in url]


def _doc_id(book_url: str | None, pubmed_id: str) -> str:
    """Stable record-id root for get_genereview_summary's fenced sections."""
    match = _NBK_IN_URL.search(book_url or "")
    return match.group(1) if match else f"pmid:{pubmed_id}"


def fence_section_prose(
    section: GeneReviewSection, *, doc_id: str, record_path: str
) -> FencedGeneReviewSection:
    """Build the v1.1-fenced sibling of an internal ``GeneReviewSection``.

    Fences ``title`` and ``content`` (recursively, every nested subsection) as
    ``untrusted_text`` without mutating the internal str-typed model.
    ``record_id`` is ``{doc_id}#{record_path}`` for content, ``…#title`` for the
    heading. Stashes the ORIGINAL raw content in the private ``_raw_content`` so
    get_genereview_summary truncation can slice the RAW bytes.
    """
    base = f"{doc_id}#{record_path}"
    fenced = FencedGeneReviewSection(
        title=fence_untrusted_text(section.title, source="genereviews", record_id=f"{base}#title"),
        content=fence_untrusted_text(section.content, source="genereviews", record_id=base),
        level=section.level,
        subsections={
            key: fence_section_prose(value, doc_id=doc_id, record_path=f"{record_path}/{key}")
            for key, value in section.subsections.items()
        },
    )
    fenced._raw_content = section.content
    return fenced


def fence_fulltext_metadata(metadata_dict: dict[str, object], *, doc_id: str) -> FullTextMetadata:
    """Fence live-scraped FullTextMetadata prose (authors/update/pub/refs).

    Shared by get_fulltext and get_genereview_summary. ``last_updated`` (a date
    token) stays a bare str; every other field is upstream free-text.
    """

    def _fence(value: object, field: str) -> object:
        if value is None:
            return None
        return fence_untrusted_text(
            str(value), source="genereviews", record_id=f"{doc_id}#metadata:{field}"
        )

    references = metadata_dict.get("references", []) or []
    ref_list = references if isinstance(references, list) else []
    return FullTextMetadata(
        authors=_fence(metadata_dict.get("authors"), "authors"),  # type: ignore[arg-type]
        update_info=_fence(metadata_dict.get("update_info"), "update_info"),  # type: ignore[arg-type]
        publication_info=_fence(  # type: ignore[arg-type]
            metadata_dict.get("publication_info"), "publication_info"
        ),
        last_updated=metadata_dict.get("last_updated"),  # type: ignore[arg-type]
        references=[
            fence_untrusted_text(
                str(ref), source="genereviews", record_id=f"{doc_id}#metadata:ref:{i}"
            )
            for i, ref in enumerate(ref_list)
        ],
    )


def _canonical_fulltext_nbk_id(raw: object) -> str | None:
    if raw is None:
        return None
    value = str(raw)
    if not value.upper().startswith("NBK"):
        value = f"NBK{value}"
    return canonicalize_nbk_id(value.upper())


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
        ttl_seconds = settings.CACHE_TTL_HOURS * 3600

        # Apply the cache decorator to both implementation methods
        self.get_genereview = alru_cache(maxsize=settings.CACHE_SIZE, ttl=ttl_seconds)(
            self._get_genereview_impl
        )
        self.get_genereview_comprehensive = alru_cache(
            maxsize=settings.CACHE_SIZE,
            ttl=ttl_seconds,
        )(self._get_genereview_comprehensive_cached_impl)
        self.get_genereview_comprehensive_indexed = alru_cache(
            maxsize=settings.CACHE_SIZE,
            ttl=ttl_seconds,
        )(self._get_genereview_comprehensive_indexed_impl)

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

        # 4. Populate the Pydantic model (sections v1.1-fenced at this boundary)
        title = scraped_data.pop("title")["content"]
        summary = scraped_data.pop("summary", None)
        diagnosis = scraped_data.pop("diagnosis", None)
        management = scraped_data.pop("management", None)
        doc_id = _doc_id(book_url, pubmed_id)

        def _fence(raw: dict[str, Any] | None, key: str) -> FencedGeneReviewSection | None:
            if not raw:
                return None
            return fence_section_prose(
                GeneReviewSection(**raw), doc_id=doc_id, record_path=f"section:{key}"
            )

        return GeneReview(
            gene_symbol=gene_symbol.upper(),
            pubmed_id=pubmed_id,
            book_url=book_url,
            title=fence_untrusted_text(title, source="genereviews", record_id=f"{doc_id}#title"),
            summary=_fence(summary, "summary"),
            diagnosis=_fence(diagnosis, "diagnosis"),
            management=_fence(management, "management"),
            other_sections={
                k: fence_section_prose(
                    GeneReviewSection(**v), doc_id=doc_id, record_path=f"section:{k}"
                )
                for k, v in scraped_data.items()
            },
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

        # 2. Get abstract data if requested (title/abstract/journal/authors fenced)
        abstract_data = None
        if include_abstract:
            try:
                abstract_result = await self.client.fetch_abstract(pubmed_id)
                if abstract_result:
                    apmid = abstract_result.get("pmid", pubmed_id)

                    def _af(value: object, field: str, rid: str = apmid) -> UntrustedText:
                        return fence_untrusted_text(
                            str(value or ""), source="genereviews", record_id=f"{rid}#{field}"
                        )

                    abstract_data = AbstractData(
                        pmid=apmid,
                        title=_af(abstract_result.get("title", ""), "title"),
                        abstract=_af(abstract_result.get("abstract", ""), "doc"),
                        authors=[
                            _af(author, f"author:{i}")
                            for i, author in enumerate(abstract_result.get("authors", []) or [])
                        ],
                        journal=_af(abstract_result.get("journal", ""), "journal"),
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

        if not book_urls and not include_links:
            try:
                links_result = await self.client.get_all_links(pubmed_id)
                book_urls = _book_urls_from_links(links_result)
            except Exception as e:
                logger.warning(
                    f"Could not resolve Bookshelf link via PubMed links for {pubmed_id}: {e}"
                )

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

                    fulltext_nbk_id = _canonical_fulltext_nbk_id(fulltext_result.get("nbk_id"))
                    fulltext_doc_id = fulltext_nbk_id or f"pmid:{pubmed_id}"
                    # full_text_data.sections is intentionally EMPTY here: the same
                    # section prose is emitted (fenced) via summary/diagnosis/
                    # management/other_sections below. Duplicating it in
                    # full_text_data.sections would violate the v1.1 no-duplication
                    # rule. full_text_data keeps its unique metadata + identifiers.
                    # title=None here (dedup): the chapter title is emitted once,
                    # fenced, on the top-level GeneReview.title below.
                    full_text_data = FullTextData(
                        nbk_id=fulltext_nbk_id,
                        url=fulltext_result.get("url", book_url),
                        title=None,
                        sections={},
                        metadata=fence_fulltext_metadata(
                            fulltext_result.get("metadata", {}), doc_id=fulltext_doc_id
                        ),
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

        # NB: no abstract-title fallback for the chapter title. The article title
        # already lives (fenced, once) on abstract_data.title; borrowing it into
        # GeneReview.title would duplicate the sanitized prose AND compute
        # raw_sha256 over the already-normalized text instead of the raw title.
        # When no chapter title is available, use a server-synthesized placeholder.
        if not title:
            title = f"GeneReview for {gene_symbol}"

        # Extract specific sections for backward compatibility, fencing each
        # section's scraped prose (v1.1) at this MCP serialization boundary.
        summary = sections.pop("summary", None)
        diagnosis = sections.pop("diagnosis", None)
        management = sections.pop("management", None)
        doc_id = _doc_id(book_url, pubmed_id)

        def _fence(sec: GeneReviewSection | None, key: str) -> FencedGeneReviewSection | None:
            if sec is None:
                return None
            return fence_section_prose(sec, doc_id=doc_id, record_path=f"section:{key}")

        return GeneReview(
            gene_symbol=gene_symbol.upper(),
            pubmed_id=pubmed_id,
            book_url=book_url,
            title=fence_untrusted_text(title, source="genereviews", record_id=f"{doc_id}#title"),
            summary=_fence(summary, "summary"),
            diagnosis=_fence(diagnosis, "diagnosis"),
            management=_fence(management, "management"),
            other_sections={
                k: fence_section_prose(sec, doc_id=doc_id, record_path=f"section:{k}")
                for k, sec in sections.items()
            },
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
