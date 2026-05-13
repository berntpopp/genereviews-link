"""NCBI E-utils client for GeneReviews data retrieval and web scraping.

This module provides the EutilsClient class for interacting with NCBI
E-utilities and scraping GeneReviews content with enhanced hierarchical
section extraction.
"""

import asyncio
import re
import warnings
import xml.etree.ElementTree as _StdET  # type-only import; parsing uses defusedxml
from typing import Any

import httpx
from bs4 import BeautifulSoup, Tag, XMLParsedAsHTMLWarning
from defusedxml import ElementTree as ET  # noqa: N817 - drop-in replacement for stdlib ET

from genereview_link.config import settings
from genereview_link.logging_config import PerformanceLogger, get_logger

# Suppress XML parsing warnings when using BeautifulSoup on HTML content
warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

logger = get_logger(__name__)


def _itertext(elem: _StdET.Element | None) -> str:
    if elem is None:
        return ""
    return " ".join("".join(elem.itertext()).split())


class EutilsClient:
    """A client for interacting with NCBI E-utils and scraping GeneReviews."""

    def __init__(self) -> None:
        """Initialize the EutilsClient with HTTP client and rate limiting."""
        self.base_url = settings.EUTILS_BASE_URL

        # Headers to mimic a real browser request
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": (
                "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8"
            ),
            "Accept-Language": "en-US,en;q=0.5",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Cache-Control": "max-age=0",
        }

        self.client = httpx.AsyncClient(
            timeout=60.0,
            headers=headers,
            follow_redirects=True,
            limits=httpx.Limits(max_keepalive_connections=5, max_connections=10),
        )
        self.rate_limit_delay = 0.11 if settings.NCBI_API_KEY else 0.34

    async def _make_request(self, endpoint: str, params: dict[str, Any]) -> dict[str, Any]:
        """Centralized request maker with rate limiting (JSON responses)."""
        if settings.NCBI_API_KEY:
            params["api_key"] = settings.NCBI_API_KEY

        # Use distributed rate limiting if available, otherwise fall back to
        # local
        if hasattr(self, "_distributed_wait"):
            await self._distributed_wait()
        else:
            await asyncio.sleep(self.rate_limit_delay)  # Respect NCBI rate
            # limits

        try:
            response = await self.client.get(f"{self.base_url}/{endpoint}", params=params)
            response.raise_for_status()
            return response.json()  # type: ignore[no-any-return]
        except httpx.ConnectError as e:
            logger.error(
                "Connection failed to NCBI E-utils",
                endpoint=endpoint,
                base_url=self.base_url,
                error=str(e),
            )
            raise ConnectionError(
                "Unable to connect to NCBI E-utilities. Please check your internet connection."
            ) from e
        except httpx.TimeoutException as e:
            logger.error(
                "Request timeout to NCBI E-utils",
                endpoint=endpoint,
                base_url=self.base_url,
                error=str(e),
            )
            raise TimeoutError("Request to NCBI E-utilities timed out. Please try again.") from e
        except httpx.HTTPStatusError as e:
            logger.error(
                "HTTP error from NCBI E-utils",
                endpoint=endpoint,
                url=str(e.request.url),
                status_code=e.response.status_code,
                error=str(e),
            )
            if e.response.status_code == 429:
                raise Exception(
                    "Rate limit exceeded. Please wait before making more requests."
                ) from e
            elif e.response.status_code == 403:
                raise Exception(
                    "Access forbidden. Please check your API key or request parameters."
                ) from e
            raise
        except Exception as e:
            logger.error(
                "Request failed to NCBI E-utils",
                endpoint=endpoint,
                error_type=type(e).__name__,
                error=str(e),
            )
            raise

    async def _make_web_request(self, url: str, max_retries: int = 3) -> httpx.Response:
        """Make a web request with retries and proper rate limiting for scraping."""
        for attempt in range(max_retries):
            try:
                # Longer delay for web scraping to be respectful
                if hasattr(self, "_distributed_wait"):
                    await self._distributed_wait()
                    await asyncio.sleep(self.rate_limit_delay * 2)
                else:
                    await asyncio.sleep(self.rate_limit_delay * 3)

                response = await self.client.get(url)
                response.raise_for_status()
                return response

            except httpx.HTTPStatusError as e:
                if e.response.status_code == 403 and attempt < max_retries - 1:
                    # Exponential backoff for 403 errors
                    wait_time = (2**attempt) * self.rate_limit_delay * 5
                    logger.warning(
                        f"403 error on attempt {attempt + 1}, retrying in {wait_time:.2f}s"
                    )
                    await asyncio.sleep(wait_time)
                    continue
                logger.error(f"HTTP error for {url}: {e}")
                raise
            except Exception as e:
                if attempt < max_retries - 1:
                    wait_time = (2**attempt) * self.rate_limit_delay * 2
                    logger.warning(
                        f"Request failed on attempt {attempt + 1}, "
                        f"retrying in {wait_time:.2f}s: {e}"
                    )
                    await asyncio.sleep(wait_time)
                    continue
                logger.error(f"Request failed after {max_retries} attempts: {e}")
                raise

        # Should be unreachable: every loop iteration either returns or raises.
        raise RuntimeError(f"Request to {url} exhausted retries without response")

    async def _make_xml_request(self, endpoint: str, params: dict[str, Any]) -> _StdET.Element:
        """Centralized request maker for XML responses."""
        if settings.NCBI_API_KEY:
            params["api_key"] = settings.NCBI_API_KEY

        # Use distributed rate limiting if available, otherwise fall back to
        # local
        if hasattr(self, "_distributed_wait"):
            await self._distributed_wait()
        else:
            await asyncio.sleep(self.rate_limit_delay)  # Respect NCBI rate
            # limits

        try:
            response = await self.client.get(f"{self.base_url}/{endpoint}", params=params)
            response.raise_for_status()
            return ET.fromstring(response.text)
        except httpx.ConnectError as e:
            logger.error(
                "Connection failed to NCBI E-utils XML endpoint",
                endpoint=endpoint,
                base_url=self.base_url,
                error=str(e),
            )
            raise ConnectionError(
                "Unable to connect to NCBI E-utilities. Please check your internet connection."
            ) from e
        except httpx.TimeoutException as e:
            logger.error(
                "Request timeout to NCBI E-utils XML endpoint",
                endpoint=endpoint,
                base_url=self.base_url,
                error=str(e),
            )
            raise TimeoutError("Request to NCBI E-utilities timed out. Please try again.") from e
        except httpx.HTTPStatusError as e:
            logger.error(
                "HTTP error from NCBI E-utils XML endpoint",
                endpoint=endpoint,
                url=str(e.request.url),
                status_code=e.response.status_code,
                error=str(e),
            )
            if e.response.status_code == 429:
                raise Exception(
                    "Rate limit exceeded. Please wait before making more requests."
                ) from e
            elif e.response.status_code == 403:
                raise Exception(
                    "Access forbidden. Please check your API key or request parameters."
                ) from e
            raise
        except Exception as e:
            logger.error(
                "Request failed to NCBI E-utils XML endpoint",
                endpoint=endpoint,
                error_type=type(e).__name__,
                error=str(e),
            )
            raise

    async def search_genereview_pmid(self, gene_symbol: str) -> str | None:
        """Search for a GeneReview PubMed ID using a gene symbol."""
        params = {
            "db": "pubmed",
            "term": f"{gene_symbol}[All Fields] AND GeneReviews[book]",
            "retmode": "json",
        }
        data = await self._make_request("esearch.fcgi", params)
        id_list = data.get("esearchresult", {}).get("idlist", [])
        return id_list[0] if id_list else None

    async def get_book_url_from_pmid(self, pubmed_id: str) -> str | None:
        """Get the NCBI Bookshelf URL from a PubMed ID."""
        params = {
            "dbfrom": "pubmed",
            "id": pubmed_id,
            "retmode": "json",
        }
        data = await self._make_request("elink.fcgi", params)
        linksets = data.get("linksets", [])
        if not linksets:
            return None

        linksetdbs = linksets[0].get("linksetdbs", [])
        for db in linksetdbs:
            dbto = str(db.get("dbto", "")).lower()
            if "book" not in dbto:
                continue
            links = db.get("links", [])
            if not links:
                continue
            book_id = str(links[0])
            if book_id.upper().startswith("NBK"):
                return f"https://www.ncbi.nlm.nih.gov/books/{book_id.upper()}/"
            return f"https://www.ncbi.nlm.nih.gov/books/NBK{book_id}/"
        return None

    async def scrape_genereview_book(self, book_url: str) -> dict[str, Any]:
        """Scrape the main sections of a GeneReview book page using enhanced parsing."""
        scrape_logger = logger.bind(url=book_url, operation="enhanced_scrape")

        with PerformanceLogger(scrape_logger, "book_scraping") as perf:
            try:
                scrape_logger.debug("Starting enhanced book scraping")
                response = await self._make_web_request(book_url)
                perf.log_milestone("response_received", response_size=len(response.text))

                soup = BeautifulSoup(response.text, "lxml")

                # Use enhanced content finding strategy
                content_div = self._find_main_content(soup)
                if not content_div:
                    scrape_logger.warning("No main content found with enhanced strategies")
                    return {}

                results: dict[str, Any] = {}

                # Extract title using enhanced strategy
                title = self._extract_title(soup, content_div)
                if title and title != "Unknown Document":
                    results["title"] = title
                    perf.log_milestone("title_extracted", title_length=len(title))

                # Extract hierarchical sections using enhanced strategy
                sections = self._extract_hierarchical_sections(content_div)
                if sections:
                    results["content"] = sections
                    perf.log_milestone("sections_extracted", section_count=len(sections))

                # Extract metadata using enhanced strategy
                metadata = self._extract_metadata(soup, content_div)
                if metadata:
                    results["metadata"] = metadata
                    perf.log_milestone("metadata_extracted", metadata_fields=len(metadata))

                sections_found = len(sections) if sections else 0
                perf.add_context(
                    sections_found=sections_found,
                    has_title=bool(title),
                    has_metadata=bool(metadata),
                )

                scrape_logger.info(
                    "Enhanced scraping completed successfully",
                    sections_found=sections_found,
                    has_title=bool(title),
                    has_metadata=bool(metadata),
                    total_content_length=sum(len(str(v)) for v in results.values()),
                )

                return results

            except Exception as e:
                scrape_logger.error(
                    "Enhanced scraping failed",
                    error_type=type(e).__name__,
                    error_message=str(e),
                    exc_info=True,
                )
                return {"error": str(e)}

    async def search_genereviews(self, gene_symbol: str, retmax: int = 20) -> dict[str, Any]:
        """Enhanced search for GeneReviews returning multiple results with metadata."""
        params = {
            "db": "pubmed",
            "term": f"{gene_symbol}[All Fields] AND GeneReviews[book]",
            "retmode": "json",
            "retmax": retmax,
            "usehistory": "y",
        }
        data = await self._make_request("esearch.fcgi", params)
        result = data.get("esearchresult", {})

        return {
            "count": int(result.get("count", 0)),
            "retmax": int(result.get("retmax", 0)),
            "retstart": int(result.get("retstart", 0)),
            "ids": result.get("idlist", []),
            "webenv": result.get("webenv", ""),
            "querykey": result.get("querykey", ""),
        }

    async def fetch_abstract(self, pubmed_id: str) -> dict[str, Any]:
        """Fetch abstract and metadata from PubMed using efetch."""
        params = {
            "db": "pubmed",
            "id": pubmed_id,
            "retmode": "xml",
            "rettype": "abstract",
        }

        root = await self._make_xml_request("efetch.fcgi", params)
        article_data = {}

        try:
            # Try regular PubmedArticle first
            article = root.find(".//PubmedArticle")
            if article is not None:
                article_data = self._parse_regular_article(article, pubmed_id)
            else:
                # Try PubmedBookArticle (for GeneReviews)
                book_article = root.find(".//PubmedBookArticle")
                if book_article is not None:
                    article_data = self._parse_book_article(book_article, pubmed_id)

        except Exception as e:
            logger.error(f"Error parsing abstract for PMID {pubmed_id}: {e}")

        return article_data

    def _parse_regular_article(self, article: _StdET.Element, pubmed_id: str) -> dict[str, Any]:
        """Parse regular PubmedArticle XML structure."""
        article_data = {"pmid": pubmed_id}

        # Extract basic information
        medline_citation = article.find(".//MedlineCitation")
        if medline_citation is not None:
            pmid = medline_citation.find(".//PMID")
            if pmid is not None:
                article_data["pmid"] = _itertext(pmid)

        # Extract article details
        article_elem = article.find(".//Article")
        if article_elem is not None:
            # Title
            title = article_elem.find(".//ArticleTitle")
            if title is not None:
                article_data["title"] = _itertext(title)

            # Abstract
            abstract_texts: list[str] = []
            for abstract_text in article_elem.findall(".//Abstract/AbstractText"):
                label = abstract_text.get("Label") or abstract_text.get("NlmCategory") or ""
                text = _itertext(abstract_text)
                if not text:
                    continue
                if label:
                    abstract_texts.append(f"{label}: {text}")
                else:
                    abstract_texts.append(text)

            article_data["abstract"] = " ".join(abstract_texts)

            # Authors
            authors = []
            author_list = article_elem.find(".//AuthorList")
            if author_list is not None:
                for author in author_list.findall(".//Author"):
                    last_name = author.find(".//LastName")
                    first_name = author.find(".//ForeName")
                    if last_name is not None:
                        name = _itertext(last_name)
                        if first_name is not None:
                            first = _itertext(first_name)
                            if first:
                                name = f"{first} {name}"
                        authors.append(name)
            article_data["authors"] = authors  # type: ignore[assignment]

            # Journal
            journal = article_elem.find(".//Journal/Title")
            if journal is not None:
                article_data["journal"] = _itertext(journal)

            # Publication date
            pub_date = article_elem.find(".//PubDate")
            if pub_date is not None:
                year = pub_date.find(".//Year")
                month = pub_date.find(".//Month")
                day = pub_date.find(".//Day")

                date_parts = []
                for part in (year, month, day):
                    text = _itertext(part)
                    if text:
                        date_parts.append(text)

                article_data["publication_date"] = "-".join(date_parts) if date_parts else ""

        return article_data

    def _parse_book_article(self, book_article: _StdET.Element, pubmed_id: str) -> dict[str, Any]:
        """Parse PubmedBookArticle XML structure (for GeneReviews)."""
        article_data = {"pmid": pubmed_id}

        book_document = book_article.find(".//BookDocument")
        if book_document is None:
            return article_data

        # Extract PMID
        pmid = book_document.find(".//PMID")
        if pmid is not None:
            article_data["pmid"] = _itertext(pmid)

        title = book_document.find(".//ArticleTitle")
        if title is None:
            title = book_document.find(".//BookTitle")
        if title is None:
            title = book_document.find(".//Book/BookTitle")
        article_data["title"] = _itertext(title)

        # Extract abstract - handle multiple AbstractText elements
        abstract_texts: list[str] = []
        for abstract_text in book_document.findall(".//Abstract/AbstractText"):
            label = abstract_text.get("Label") or abstract_text.get("NlmCategory") or ""
            text = _itertext(abstract_text)
            if not text:
                continue
            if label and label.upper() != "UNLABELLED":
                abstract_texts.append(f"{label}: {text}")
            else:
                abstract_texts.append(text)

        article_data["abstract"] = "\n\n".join(abstract_texts)

        # Extract authors - look for AuthorList with Type="authors"
        authors = []
        author_lists = book_document.findall(".//AuthorList")
        for author_list in author_lists:
            if author_list.get("Type") == "authors":
                for author in author_list.findall(".//Author"):
                    last_name = author.find(".//LastName")
                    first_name = author.find(".//ForeName")
                    if last_name is not None:
                        name = _itertext(last_name)
                        if first_name is not None:
                            first = _itertext(first_name)
                            if first:
                                name = f"{first} {name}"
                        authors.append(name)
                break  # Use first authors list found
        article_data["authors"] = authors  # type: ignore[assignment]

        # Extract journal/book information
        book_title = book_document.find(".//Book/BookTitle")
        if book_title is not None:
            article_data["journal"] = _itertext(book_title) or "GeneReviews"
        else:
            article_data["journal"] = "GeneReviews"

        # Extract publication date - use ContributionDate or DateRevised
        contrib_date = book_document.find(".//ContributionDate")
        if contrib_date is not None:
            year = contrib_date.find(".//Year")
            month = contrib_date.find(".//Month")
            day = contrib_date.find(".//Day")

            date_parts = []
            for part in (year, month, day):
                text = _itertext(part)
                if text:
                    date_parts.append(text)

            article_data["publication_date"] = "-".join(date_parts) if date_parts else ""
        else:
            # Fallback to book publication date
            book_pub_date = book_document.find(".//Book/PubDate")
            if book_pub_date is not None:
                year = book_pub_date.find(".//Year")
                if year is not None:
                    article_data["publication_date"] = _itertext(year)

        return article_data

    async def get_all_links(self, pubmed_id: str) -> dict[str, Any]:
        """Get all available links from a PubMed ID using elink."""
        params = {"dbfrom": "pubmed", "id": pubmed_id, "cmd": "llinks"}
        root = await self._make_xml_request("elink.fcgi", params)
        entries = self._parse_link_entries(root)
        if not entries:
            params = {"dbfrom": "pubmed", "id": pubmed_id, "cmd": "prlinks"}
            root = await self._make_xml_request("elink.fcgi", params)
            entries = self._parse_link_entries(root)
        link_types = sorted({str(entry["link_type"]) for entry in entries})

        return {
            "urls": [str(entry["url"]) for entry in entries],
            "link_entries": entries,
            "by_type": {
                link_type: [
                    str(entry["url"]) for entry in entries if entry["link_type"] == link_type
                ]
                for link_type in link_types
            },
        }

    def _parse_link_entries(self, root: _StdET.Element) -> list[dict[str, str | None]]:
        entries: list[dict[str, str | None]] = []
        seen: set[tuple[str, str]] = set()

        def add(url: str | None, link_type: str, provider: str | None = None) -> None:
            if not url:
                return
            key = (url, link_type)
            if key in seen:
                return
            seen.add(key)
            entries.append({"url": url, "link_type": link_type, "provider": provider})

        for obj_url in root.findall(".//ObjUrl"):
            provider = _itertext(obj_url.find("Provider/Name"))
            category = _itertext(obj_url.find("Category"))
            link_type = "prlinks" if provider else "llinks"
            add(_itertext(obj_url.find("Url")), link_type, provider or category)

        for link_set_db in root.findall(".//LinkSetDb"):
            link_name = _itertext(link_set_db.find("LinkName"))
            for link in link_set_db.findall("Link"):
                link_id = _itertext(link.find("Id"))
                if link_id and "books" in link_name.lower():
                    nbk_id = link_id if link_id.startswith("NBK") else f"NBK{link_id}"
                    add(f"https://www.ncbi.nlm.nih.gov/books/{nbk_id}/", "books", "NCBI Bookshelf")
                elif link_id and "pmc" in link_name.lower():
                    add(
                        f"https://www.ncbi.nlm.nih.gov/pmc/articles/PMC{link_id}/",
                        "pmc",
                        "PubMed Central",
                    )
        return entries

    async def scrape_genereview_comprehensive(self, book_url: str) -> dict[str, Any]:
        """Comprehensive scraping of a GeneReview book page with improved structure and fault tolerance."""
        try:
            response = await self._make_web_request(book_url)
            soup = BeautifulSoup(response.text, "lxml")

            # Extract NBK ID from URL
            nbk_match = re.search(r"NBK(\d+)", book_url)
            nbk_id = nbk_match.group(1) if nbk_match else None

            results: dict[str, Any] = {
                "nbk_id": nbk_id,
                "url": book_url,
                "title": "",
                "sections": {},
                "metadata": {},
            }

            # Find main content container with multiple fallback strategies
            content_div = self._find_main_content(soup)
            if not content_div:
                return {"error": "Could not find main content"}

            # Extract title with multiple fallback strategies
            title = self._extract_title(soup, content_div)
            results["title"] = title

            # Extract comprehensive metadata
            metadata = self._extract_metadata(soup, content_div)
            results["metadata"] = metadata

            # Extract sections with hierarchical structure
            sections = self._extract_hierarchical_sections(content_div)
            results["sections"] = sections

            return results

        except Exception as e:
            logger.error(f"Error scraping comprehensive content from {book_url}: {e}")
            return {"error": str(e)}

    def _find_main_content(self, soup: BeautifulSoup) -> Tag | None:
        """Find the main content container using GeneReviews-specific strategies."""
        # Strategy 1: Look for the standard GeneReviews main content container
        main_content = soup.find("div", {"class": "main-content lit-style"})
        if isinstance(main_content, Tag):
            return main_content

        # Strategy 2: Look for main-content class variations
        main_content_variations = [
            soup.find("div", {"class": re.compile(r".*main-content.*", re.I)}),
            soup.find("div", {"class": re.compile(r".*main.*content.*", re.I)}),
        ]
        for content in main_content_variations:
            if isinstance(content, Tag) and content.find_all(["h2", "h3"]):
                return content

        # Strategy 3: Look for div with NBK ID that contains actual content
        def _starts_with_nbk(value: str | None) -> bool:
            return bool(value) and value is not None and value.startswith("NBK")

        nbk_divs = soup.find_all("div", {"id": _starts_with_nbk})
        for div in nbk_divs:
            # Check if this div has substantial content (h2/h3 headings)
            if div.find_all(["h2", "h3"]):
                return div

        # Strategy 4: Look for content areas with substantial headings
        content_selectors: list[dict[str, Any]] = [
            {"class": re.compile(r".*content.*", re.I)},
            {"class": re.compile(r".*main.*", re.I)},
            {"class": re.compile(r".*article.*", re.I)},
            {"class": re.compile(r".*chapter.*", re.I)},
            {"class": re.compile(r".*body.*", re.I)},
            {"role": "main"},
        ]

        for selector in content_selectors:
            content_div = soup.find("div", selector)
            if isinstance(content_div, Tag) and content_div.find_all(["h2", "h3"]):
                return content_div

        # Strategy 5: Look for semantic HTML5 elements with content
        for tag in ["main", "article", "section"]:
            semantic_div = soup.find(tag)
            if isinstance(semantic_div, Tag) and semantic_div.find_all(["h2", "h3"]):
                return semantic_div

        # Strategy 6: Find any container with multiple h2/h3 headings
        all_containers = soup.find_all(["div", "section", "article"])
        for container in all_containers:
            headings = container.find_all(["h2", "h3"])
            if len(headings) >= 3:  # Likely the main content if it has multiple sections
                return container

        # Strategy 7: Use the body as fallback
        body = soup.find("body")
        return body if isinstance(body, Tag) else None

    def _extract_title(self, soup: BeautifulSoup, content_div: Tag) -> str:
        """Extract document title using GeneReviews-specific strategies."""
        # Strategy 1: Look for GeneReviews title structure: span.title within h1
        h1_tags = content_div.find_all("h1")
        for h1 in h1_tags:
            # Look for span with class="title" within h1
            title_span = h1.find("span", {"class": "title"})
            if title_span:
                title = title_span.get_text().strip()
                if title and title.lower() not in [
                    "bookshelf",
                    "ncbi bookshelf",
                ]:
                    return str(title)

            # Fallback to h1 text if no title span found
            title = h1.get_text().strip()
            if title and title.lower() not in ["bookshelf", "ncbi bookshelf"]:
                return str(title)

        # Strategy 2: Look for itemprop="name" attribute (common in GeneReviews)
        name_elem = content_div.find(attrs={"itemprop": "name"})
        if name_elem:
            title = name_elem.get_text().strip()
            if title and title.lower() not in ["bookshelf", "ncbi bookshelf"]:
                return str(title)

        # Strategy 3: Page title tag, cleaned up
        title_tag = soup.find("title")
        if title_tag:
            title = title_tag.get_text().strip()
            # Clean up common suffixes
            title = re.sub(r"\s*-\s*NCBI\s*Bookshelf.*$", "", title, flags=re.I)
            title = re.sub(r"\s*-\s*GeneReviews.*$", "", title, flags=re.I)
            title = re.sub(r"\s*-\s*PubMed.*$", "", title, flags=re.I)
            if title and title.lower() not in ["bookshelf", "ncbi bookshelf"]:
                return str(title)

        # Strategy 4: Look for specific title classes or attributes
        title_selectors: list[dict[str, Any]] = [
            {"class": re.compile(r".*title.*", re.I)},
            {"class": re.compile(r".*heading.*", re.I)},
            {"data-title": True},
        ]

        for selector in title_selectors:
            elem = content_div.find(attrs=selector)
            if elem:
                title = elem.get_text().strip()
                if title and title.lower() not in [
                    "bookshelf",
                    "ncbi bookshelf",
                ]:
                    return str(title)

        # Strategy 5: Look for meta tags
        meta_title = soup.find("meta", {"property": "og:title"})
        if meta_title and meta_title.get("content"):
            return str(meta_title["content"]).strip()

        meta_title = soup.find("meta", {"name": "dc.title"})
        if meta_title and meta_title.get("content"):
            return str(meta_title["content"]).strip()

        return "Unknown Document"

    def _extract_metadata(self, soup: BeautifulSoup, content_div: Tag) -> dict[str, Any]:
        """Extract comprehensive metadata from the document."""
        metadata = {}

        # Extract authors
        authors = self._extract_authors(content_div)
        if authors:
            metadata["authors"] = authors

        # Extract update information
        update_info = self._extract_update_info(content_div)
        if update_info:
            metadata["update_info"] = update_info

        # Extract publication info
        pub_info = self._extract_publication_info(content_div)
        if pub_info:
            metadata["publication_info"] = pub_info

        # Extract last updated date
        last_updated = self._extract_last_updated(content_div)
        if last_updated:
            metadata["last_updated"] = last_updated

        # Extract and parse references
        references = self._extract_references(content_div)
        if references:
            metadata["references"] = "\n".join(references)

        return metadata

    def _extract_authors(self, content_div: Tag) -> str | None:
        """Extract author information."""
        patterns = [
            re.compile(r"Author[s]?\s*:", re.I),
            re.compile(r"By\s*:", re.I),
            re.compile(r"Written\s*by", re.I),
        ]

        for pattern in patterns:
            elements = content_div.find_all(string=pattern)
            for elem in elements:
                parent = elem.parent
                if parent:
                    text = parent.get_text().strip()
                    # Extract text after the pattern
                    match = pattern.search(text)
                    if match:
                        author_text = text[match.end() :].strip()
                        if author_text:
                            return str(author_text)

        return None

    def _extract_update_info(self, content_div: Tag) -> str | None:
        """Extract update information."""
        patterns = [
            re.compile(r"Last\s*(Updated?|Revision)", re.I),
            re.compile(r"Updated?\s*:", re.I),
            re.compile(r"Revision\s*History", re.I),
            re.compile(r"Initial\s*Posting", re.I),
        ]

        for pattern in patterns:
            elements = content_div.find_all(string=pattern)
            for elem in elements:
                parent = elem.parent
                if parent:
                    return str(parent.get_text().strip())

        return None

    def _extract_publication_info(self, content_div: Tag) -> str | None:
        """Extract publication and copyright information."""
        patterns = [
            re.compile(r"Copyright", re.I),
            re.compile(r"Published", re.I),
            re.compile(r"Citation", re.I),
        ]

        for pattern in patterns:
            elements = content_div.find_all(string=pattern)
            for elem in elements:
                parent = elem.parent
                if parent:
                    return str(parent.get_text().strip())

        return None

    def _extract_last_updated(self, content_div: Tag) -> str | None:
        """Extract last updated date."""
        # Look for date patterns
        date_pattern = re.compile(
            r"\b\d{4}[-/]\d{1,2}[-/]\d{1,2}\b|"
            r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,2},?\s+\d{4}\b",
            re.I,
        )

        update_elements = content_div.find_all(string=re.compile(r"updated?|revised?", re.I))
        for elem in update_elements:
            parent = elem.parent
            if parent:
                text = parent.get_text()
                match = date_pattern.search(text)
                if match:
                    return match.group().strip()

        return None

    def _extract_references(self, content_div: Tag) -> list[str]:
        """Extract and parse references section as a list of strings."""
        references: list[str] = []

        # Find references section. bs4's overloads do not statically permit
        # combining ``name`` and ``string`` filters together, but the runtime
        # behaviour is documented and used widely.
        ref_headings = content_div.find_all(  # type: ignore[call-overload]
            ["h1", "h2", "h3", "h4", "h5", "h6"],
            string=re.compile(r"references?|bibliography", re.I),
        )

        if not ref_headings:
            return references

        # Get content after references heading
        ref_heading = ref_headings[0]

        # Find all content elements after the references heading until next major heading
        current_pos = ref_heading
        ref_content_elements = []

        while current_pos:
            current_pos = current_pos.find_next()

            # Stop at next major heading
            if (
                current_pos
                and current_pos.name in ["h1", "h2", "h3"]
                and current_pos != ref_heading
            ):
                break

            # Collect content elements that likely contain references
            if (
                current_pos
                and hasattr(current_pos, "name")
                and current_pos.name in ["p", "div", "ul", "ol", "li"]
            ):
                ref_content_elements.append(current_pos)

        # Extract and process references
        for element in ref_content_elements:
            text = element.get_text().strip()
            if text and len(text) > 30:  # Filter out very short text
                # Split on patterns that typically separate references
                # Look for author patterns at start of lines/sentences
                potential_refs = re.split(
                    r"(?=\b[A-Z][a-z]+\s+[A-Z]{1,2}(?:[a-z]*)?(?:,\s*[A-Z][a-z]+\s+[A-Z]{1,2})?.*?\.\s)",
                    text,
                )

                for ref in potential_refs:
                    ref = ref.strip()
                    # Clean up the reference text
                    if len(ref) > 50 and re.search(
                        r"[A-Z][a-z]+.*?\d{4}", ref
                    ):  # Must contain author-like pattern and year
                        # Clean up common formatting issues
                        ref = re.sub(r"\s+", " ", ref)  # Normalize whitespace
                        ref = re.sub(r"^\d+\.\s*", "", ref)  # Remove leading numbers
                        ref = ref.strip()
                        if ref and not ref.lower().startswith(("http", "www", "doi:", "pmid:")):
                            references.append(ref)

        # If the above didn't work well, try a simpler approach
        if len(references) < 5:  # If we didn't get many references, try alternative method
            references = []

            # Get all text after references heading and split on common patterns
            full_text = ""
            current_pos = ref_heading
            while current_pos:
                current_pos = current_pos.find_next()
                if (
                    current_pos
                    and current_pos.name in ["h1", "h2", "h3"]
                    and current_pos != ref_heading
                ):
                    break
                if current_pos and hasattr(current_pos, "get_text"):
                    full_text += " " + current_pos.get_text()

            # Split on numbered references or author patterns
            ref_patterns = [
                r"\d+\.\s*([A-Z][^.]*?(?:\d{4}[^.]*?\.(?:\s*\[PubMed\]|\s*\[PMC[^\]]*\])?)?)",
                r"([A-Z][a-z]+\s+[A-Z]{1,2}[^.]*?\d{4}[^.]*?\.(?:\s*\[PubMed\]|\s*\[PMC[^\]]*\])?)",
            ]

            for pattern in ref_patterns:
                matches = re.findall(pattern, full_text, re.MULTILINE | re.DOTALL)
                for match in matches:
                    ref = match.strip()
                    if len(ref) > 50:
                        ref = re.sub(r"\s+", " ", ref)
                        references.append(ref)

                if len(references) > 5:  # If we found good matches, use them
                    break

        # Remove duplicates while preserving order
        seen = set()
        unique_refs = []
        for ref in references:
            if ref not in seen:
                seen.add(ref)
                unique_refs.append(ref)

        return unique_refs[:50]  # Limit to 50 references max

    def _parse_reference(self, ref_text: str) -> dict[str, Any]:
        """Parse a single reference into structured data."""
        ref_data = {"text": ref_text}

        # Extract PMID
        pmid_match = re.search(r"PMID:?\s*(\d+)", ref_text, re.I)
        if pmid_match:
            ref_data["pmid"] = pmid_match.group(1)

        # Extract year (4 digits)
        year_match = re.search(r"\b(19|20)\d{2}\b", ref_text)
        if year_match:
            ref_data["year"] = year_match.group()

        # Extract authors (before first period or semicolon)
        author_match = re.match(r"^([^.;]+)", ref_text)
        if author_match:
            authors = author_match.group(1).strip()
            if not re.search(r"\d{4}", authors):  # Make sure it doesn't contain year
                ref_data["authors"] = authors

        # Extract title (often in quotes or after authors before journal)
        title_patterns = [
            re.compile(r'"([^"]+)"'),  # Quoted title
            re.compile(r"\.([^.]+)\.\s*[A-Z]"),  # Title between periods
        ]

        for pattern in title_patterns:
            title_match = pattern.search(ref_text)
            if title_match:
                title = title_match.group(1).strip()
                if len(title) > 10:  # Reasonable title length
                    ref_data["title"] = title
                break

        # Extract journal (often italicized or after title)
        journal_patterns = [
            re.compile(r"\.\s*([A-Z][^.]+?)\.\s*\d{4}"),  # Journal before year
            re.compile(
                r"\b([A-Z][a-z]*\s+[A-Z][a-z]*(?:\s+[A-Z][a-z]*)*)\s*\.\s*\d{4}"
            ),  # Multi-word journal
        ]

        for pattern in journal_patterns:
            journal_match = pattern.search(ref_text)
            if journal_match:
                journal = journal_match.group(1).strip()
                if len(journal) > 3 and not re.search(r"\d", journal):
                    ref_data["journal"] = journal
                break

        return ref_data

    def _extract_hierarchical_sections(self, content_div: Tag) -> dict[str, dict[str, Any]]:
        """Extract sections with hierarchical structure optimized for GeneReviews."""
        sections: dict[str, dict[str, Any]] = {}

        # Strategy 1: Look for GeneReviews-specific section divs (preferred method)
        def _is_section_id(value: str | None) -> bool:
            return bool(value) and value is not None and "." in value and not value.startswith("_")

        section_divs = content_div.find_all(
            "div",
            {"id": _is_section_id},
        )

        if section_divs:
            # Process GeneReviews structured sections
            seen_nodes: set[int] = set()
            for section_div in section_divs:
                if id(section_div) in seen_nodes:
                    continue
                # Extract h2 heading from within the div
                h2_heading = section_div.find("h2", recursive=False) or section_div.find("h2")
                if not h2_heading:
                    continue

                section_title = h2_heading.get_text().strip()
                if not section_title or len(section_title) < 3:
                    continue

                # Extract main section content
                section_content_parts = self._collect_direct_content(section_div, seen_nodes)
                subsections: dict[str, dict[str, Any]] = {}

                # Extract h3 subsections
                for child in section_div.children:
                    if not isinstance(child, Tag) or child.name != "h3":
                        continue
                    subsection_title = child.get_text().strip()
                    if not subsection_title or len(subsection_title) < 3:
                        continue

                    # Extract content for this subsection
                    subsection_content = self._collect_until_heading(
                        child, {"h2", "h3"}, seen_nodes
                    )

                    if subsection_content and len(subsection_content) > 30:
                        subsection_key = self._normalize_section_key(subsection_title)
                        subsections[subsection_key] = {
                            "title": subsection_title,
                            "content": subsection_content,
                            "level": 3,
                            "subsections": {},
                        }
                    seen_nodes.add(id(child))

                # Create main section
                main_content = f"{section_title} {' '.join(section_content_parts)}".strip()
                main_content = self._clean_content(main_content)

                if main_content and len(main_content) > 50:
                    section_key = self._normalize_section_key(section_title)
                    sections[section_key] = {
                        "title": section_title,
                        "content": main_content,
                        "level": 2,
                        "subsections": subsections,
                    }
                    seen_nodes.add(id(section_div))

        # Strategy 2: Fallback to heading-based extraction if no structured divs found
        if not sections:
            sections = self._extract_sections_by_headings(content_div)

        return sections

    def _collect_direct_content(self, section_div: Tag, seen_nodes: set[int]) -> list[str]:
        blocks: list[str] = []
        seen_texts: set[str] = set()
        for child in section_div.children:
            if not isinstance(child, Tag) or child.name is None:
                continue
            if child.name == "h3":
                break
            if id(child) in seen_nodes or child.name == "h2":
                continue
            if child.name in {"p", "ul", "ol", "table"}:
                self._append_unique_block_text(child, blocks, seen_nodes, seen_texts)
            elif child.name in {"div", "section"}:
                for block in child.find_all(["p", "ul", "ol", "table"]):
                    self._append_unique_block_text(block, blocks, seen_nodes, seen_texts)
        return blocks

    def _collect_until_heading(
        self, heading: Tag, stop_tags: set[str], seen_nodes: set[int]
    ) -> str:
        blocks: list[str] = []
        seen_texts: set[str] = set()
        current = heading.find_next_sibling()
        while isinstance(current, Tag):
            if current.name in stop_tags:
                break
            if id(current) not in seen_nodes and current.name in {"p", "ul", "ol", "table"}:
                self._append_unique_block_text(current, blocks, seen_nodes, seen_texts)
            elif id(current) not in seen_nodes and current.name in {"div", "section"}:
                for block in current.find_all(["p", "ul", "ol", "table"]):
                    self._append_unique_block_text(block, blocks, seen_nodes, seen_texts)
            current = current.find_next_sibling()
        return self._clean_content(" ".join(blocks))

    def _append_unique_block_text(
        self, block: Tag, blocks: list[str], seen_nodes: set[int], seen_texts: set[str]
    ) -> None:
        if id(block) in seen_nodes:
            return
        text = block.get_text(separator=" ", strip=True)
        seen_nodes.add(id(block))
        if self._is_valid_content(text) and text not in seen_texts:
            blocks.append(text)
            seen_texts.add(text)

    def _extract_subsection_content(self, h3_heading: Tag, section_div: Tag) -> str:
        """Extract content for an h3 subsection within a section div."""
        content_parts = []
        current = h3_heading.find_next_sibling()

        # Collect content until we hit the next h3 or end of section
        while current and current.parent == section_div:
            if current.name == "h3":
                break

            if current.name in ["p", "div", "ul", "ol"]:
                text = current.get_text().strip()
                if self._is_valid_content(text):
                    content_parts.append(text)

            current = current.find_next_sibling()

        content = " ".join(content_parts).strip()
        return self._clean_content(content)

    def _extract_sections_by_headings(self, content_div: Tag) -> dict[str, dict[str, Any]]:
        """Fallback method: extract sections based on h2/h3 heading structure."""
        sections: dict[str, dict[str, Any]] = {}

        # Get all h2 headings as main sections
        h2_headings = content_div.find_all("h2")

        for i, h2 in enumerate(h2_headings):
            section_title = h2.get_text().strip()
            if not section_title or len(section_title) < 3:
                continue

            # Skip navigation headings
            if section_title.lower() in [
                "menu",
                "navigation",
                "skip",
                "search",
                "bookshelf",
            ]:
                continue

            # Find the next h2 to determine section boundaries
            next_h2 = h2_headings[i + 1] if i + 1 < len(h2_headings) else None

            # Extract content and subsections for this h2
            section_content_parts = []
            subsections = {}

            current = h2.find_next_sibling()
            while current and (not next_h2 or current != next_h2):
                if current.name == "h2":
                    break
                elif current.name == "h3":
                    # Process h3 as subsection
                    subsection_title = current.get_text().strip()
                    if subsection_title and len(subsection_title) >= 3:
                        subsection_content = self._extract_heading_content(current, ["h2", "h3"])
                        if subsection_content and len(subsection_content) > 30:
                            subsection_key = self._normalize_section_key(subsection_title)
                            subsections[subsection_key] = {
                                "title": subsection_title,
                                "content": subsection_content,
                                "level": 3,
                                "subsections": {},
                            }
                elif current.name in ["p", "div", "ul", "ol"]:
                    text = current.get_text().strip()
                    if self._is_valid_content(text):
                        section_content_parts.append(text)

                current = current.find_next_sibling()

            # Create main section
            main_content = " ".join(section_content_parts).strip()
            main_content = self._clean_content(main_content)

            if main_content and len(main_content) > 50:
                section_key = self._normalize_section_key(section_title)
                sections[section_key] = {
                    "title": section_title,
                    "content": main_content,
                    "level": 2,
                    "subsections": subsections,
                }

        return sections

    def _extract_heading_content(self, heading: Tag, stop_tags: list[str]) -> str:
        """Extract content following a heading until the next heading of same or higher level."""
        content_parts = []
        current = heading.find_next_sibling()

        while current:
            if current.name in stop_tags:
                break
            elif current.name in ["p", "div", "ul", "ol"]:
                text = current.get_text().strip()
                if self._is_valid_content(text):
                    content_parts.append(text)

            current = current.find_next_sibling()

        content = " ".join(content_parts).strip()
        return self._clean_content(content)

    def _is_valid_content(self, text: str) -> bool:
        """Check if text content is valid and should be included."""
        if not text or len(text) < 15:
            return False

        # Filter out unwanted content
        text_lower = text.lower()
        unwanted_phrases = [
            "federal government",
            "the .gov means",
            "https://",
            "the site is secure",
            "sharing sensitive information",
            "encrypted and transmitted securely",
            "javascript to function",
            "ncbi web site requires",
            "show details",
            "author information and affiliations",
            "email:",
        ]

        return all(phrase not in text_lower for phrase in unwanted_phrases)

    def _clean_content(self, content: str) -> str:
        """Clean and normalize content text."""
        # Remove any remaining HTML tags
        content = re.sub(r"<[^>]+>", "", content)
        # Remove HTML entities
        content = re.sub(r"&[a-zA-Z0-9#]+;", "", content)
        # Normalize whitespace
        content = re.sub(r"\s+", " ", content)
        # Remove extra line breaks
        content = re.sub(r"(\n\s*){3,}", "\n\n", content)
        # Remove common artifacts
        content = re.sub(r"\s*(Show details|Hide details)\s*", "", content, flags=re.I)
        # Remove control characters
        content = re.sub(r"[\x00-\x08\x0B-\x0C\x0E-\x1F\x7F]", "", content)
        return content.strip()

    def _normalize_section_key(self, title: str) -> str:
        """Normalize section title for use as dictionary key."""
        # Remove special characters and normalize spacing
        key = re.sub(r"[^\w\s-]", "", title.lower())
        key = re.sub(r"[-\s]+", "_", key.strip())
        # Remove common prefixes/suffixes
        key = re.sub(r"^(the_|a_|an_)", "", key)
        key = re.sub(r"(_section|_chapter)$", "", key)
        return key[:50]  # Limit length

    async def close(self) -> None:
        """Close the HTTP client and cleanup resources."""
        await self.client.aclose()
