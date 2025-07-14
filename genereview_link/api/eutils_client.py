import asyncio
import re
from typing import Any, List, Dict, Optional
from xml.etree import ElementTree as ET

import httpx
from bs4 import BeautifulSoup

from genereview_link.config import settings
from genereview_link.logging_config import get_logger, PerformanceLogger

logger = get_logger(__name__)

class EutilsClient:
    """A client for interacting with NCBI E-utils and scraping GeneReviews."""

    def __init__(self):
        self.base_url = settings.EUTILS_BASE_URL
        
        # Headers to mimic a real browser request
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Cache-Control": "max-age=0"
        }
        
        self.client = httpx.AsyncClient(
            timeout=60.0,
            headers=headers,
            follow_redirects=True,
            limits=httpx.Limits(max_keepalive_connections=5, max_connections=10)
        )
        self.rate_limit_delay = 0.11 if settings.NCBI_API_KEY else 0.34

    async def _make_request(self, endpoint: str, params: dict) -> dict[str, Any]:
        """Centralized request maker with rate limiting (JSON responses)."""
        if settings.NCBI_API_KEY:
            params["api_key"] = settings.NCBI_API_KEY
        
        # Use distributed rate limiting if available, otherwise fall back to local
        if hasattr(self, '_distributed_wait'):
            await self._distributed_wait()
        else:
            await asyncio.sleep(self.rate_limit_delay) # Respect NCBI rate limits
        
        try:
            response = await self.client.get(f"{self.base_url}/{endpoint}", params=params)
            response.raise_for_status()
            return response.json()
        except httpx.ConnectError as e:
            logger.error(
                "Connection failed to NCBI E-utils",
                endpoint=endpoint,
                base_url=self.base_url,
                error=str(e)
            )
            raise ConnectionError("Unable to connect to NCBI E-utilities. Please check your internet connection.")
        except httpx.TimeoutException as e:
            logger.error(
                "Request timeout to NCBI E-utils",
                endpoint=endpoint,
                base_url=self.base_url,
                error=str(e)
            )
            raise TimeoutError("Request to NCBI E-utilities timed out. Please try again.")
        except httpx.HTTPStatusError as e:
            logger.error(
                "HTTP error from NCBI E-utils",
                endpoint=endpoint,
                url=str(e.request.url),
                status_code=e.response.status_code,
                error=str(e)
            )
            if e.response.status_code == 429:
                raise Exception("Rate limit exceeded. Please wait before making more requests.")
            elif e.response.status_code == 403:
                raise Exception("Access forbidden. Please check your API key or request parameters.")
            raise
        except Exception as e:
            logger.error(
                "Request failed to NCBI E-utils",
                endpoint=endpoint,
                error_type=type(e).__name__,
                error=str(e)
            )
            raise

    async def _make_web_request(self, url: str, max_retries: int = 3) -> httpx.Response:
        """Make a web request with retries and proper rate limiting for scraping."""
        for attempt in range(max_retries):
            try:
                # Longer delay for web scraping to be respectful
                if hasattr(self, '_distributed_wait'):
                    await self._distributed_wait()
                    await asyncio.sleep(self.rate_limit_delay * 2)  # Additional delay for scraping
                else:
                    await asyncio.sleep(self.rate_limit_delay * 3)
                
                response = await self.client.get(url)
                response.raise_for_status()
                return response
                
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 403 and attempt < max_retries - 1:
                    # Exponential backoff for 403 errors
                    wait_time = (2 ** attempt) * self.rate_limit_delay * 5
                    logger.warning(f"403 error on attempt {attempt + 1}, retrying in {wait_time:.2f}s")
                    await asyncio.sleep(wait_time)
                    continue
                logger.error(f"HTTP error for {url}: {e}")
                raise
            except Exception as e:
                if attempt < max_retries - 1:
                    wait_time = (2 ** attempt) * self.rate_limit_delay * 2
                    logger.warning(f"Request failed on attempt {attempt + 1}, retrying in {wait_time:.2f}s: {e}")
                    await asyncio.sleep(wait_time)
                    continue
                logger.error(f"Request failed after {max_retries} attempts: {e}")
                raise

    async def _make_xml_request(self, endpoint: str, params: dict) -> ET.Element:
        """Centralized request maker for XML responses."""
        if settings.NCBI_API_KEY:
            params["api_key"] = settings.NCBI_API_KEY
        
        # Use distributed rate limiting if available, otherwise fall back to local
        if hasattr(self, '_distributed_wait'):
            await self._distributed_wait()
        else:
            await asyncio.sleep(self.rate_limit_delay) # Respect NCBI rate limits
        
        try:
            response = await self.client.get(f"{self.base_url}/{endpoint}", params=params)
            response.raise_for_status()
            return ET.fromstring(response.text)
        except httpx.ConnectError as e:
            logger.error(
                "Connection failed to NCBI E-utils XML endpoint",
                endpoint=endpoint,
                base_url=self.base_url,
                error=str(e)
            )
            raise ConnectionError("Unable to connect to NCBI E-utilities. Please check your internet connection.")
        except httpx.TimeoutException as e:
            logger.error(
                "Request timeout to NCBI E-utils XML endpoint",
                endpoint=endpoint,
                base_url=self.base_url,
                error=str(e)
            )
            raise TimeoutError("Request to NCBI E-utilities timed out. Please try again.")
        except httpx.HTTPStatusError as e:
            logger.error(
                "HTTP error from NCBI E-utils XML endpoint",
                endpoint=endpoint,
                url=str(e.request.url),
                status_code=e.response.status_code,
                error=str(e)
            )
            if e.response.status_code == 429:
                raise Exception("Rate limit exceeded. Please wait before making more requests.")
            elif e.response.status_code == 403:
                raise Exception("Access forbidden. Please check your API key or request parameters.")
            raise
        except Exception as e:
            logger.error(
                "Request failed to NCBI E-utils XML endpoint",
                endpoint=endpoint,
                error_type=type(e).__name__,
                error=str(e)
            )
            raise

    async def search_genereview_pmid(self, gene_symbol: str) -> str | None:
        """Search for a GeneReview PubMed ID using a gene symbol."""
        params = {
            "db": "pubmed",
            "term": f"{gene_symbol}[All Fields] AND GeneReviews[book]",
            "retmode": "json"
        }
        data = await self._make_request("esearch.fcgi", params)
        id_list = data.get("esearchresult", {}).get("idlist", [])
        return id_list[0] if id_list else None

    async def get_book_url_from_pmid(self, pubmed_id: str) -> str | None:
        """Get the NCBI Bookshelf URL from a PubMed ID."""
        params = {
            "dbfrom": "pubmed",
            "id": pubmed_id,
            "cmd": "prlinks",
            "retmode": "json"
        }
        data = await self._make_request("elink.fcgi", params)
        linksets = data.get("linksets", [])
        if not linksets:
            return None
            
        linksetdbs = linksets[0].get("linksetdbs", [])
        for db in linksetdbs:
            if db.get("dbto") == "books":
                links = db.get("links", [])
                return f"https://www.ncbi.nlm.nih.gov/books/NBK{links[0]}/" if links else None
        return None

    async def scrape_genereview_book(self, book_url: str) -> dict[str, dict[str, str]]:
        """Scrape the main sections of a GeneReview book page."""
        scrape_logger = logger.bind(url=book_url, operation="basic_scrape")
        
        with PerformanceLogger(scrape_logger, "book_scraping") as perf:
            try:
                scrape_logger.debug("Starting basic book scraping")
                response = await self._make_web_request(book_url)
                perf.log_milestone("response_received", response_size=len(response.text))
                
                soup = BeautifulSoup(response.text, "lxml")
            
                results = {}
                # The main content is within a div with id='NBK1116' or similar
                content_div = soup.find("div", {"id": lambda x: x and x.startswith('NBK')})
                if not content_div:
                    scrape_logger.warning("No NBK content div found")
                    return {}

                # Extract title
                title_tag = content_div.find('h1')
                if title_tag:
                     results['title'] = {'title': 'Title', 'content': title_tag.text.strip()}

                # Scrape sections based on h2 tags with an id
                sections_found = 0
                for h2 in content_div.find_all('h2', id=True):
                    sections_found += 1
                section_title = h2.text.strip()
                # Find the content for this section, which is everything until the next h2
                content_html = ""
                for sibling in h2.find_next_siblings():
                    if sibling.name == 'h2':
                        break
                    content_html += str(sibling)
                
                    # Use a new soup object to parse the section content cleanly
                    section_soup = BeautifulSoup(content_html, 'lxml')
                    section_text = section_soup.get_text(separator=' ', strip=True)

                    # Map common titles to standardized keys
                    key = section_title.lower().replace(" ", "_")
                    results[key] = {'title': section_title, 'content': section_text}
                
                perf.add_context(sections_found=sections_found, total_sections=len(results))
                scrape_logger.info(
                    "Basic scraping completed successfully",
                    sections_found=sections_found,
                    total_sections=len(results)
                )
                
                return results
            except Exception as e:
                scrape_logger.error(
                    "Scraping failed",
                    error_type=type(e).__name__,
                    error_message=str(e),
                    exc_info=True
                )
                return {}

    async def search_genereviews(self, gene_symbol: str, retmax: int = 20) -> List[Dict[str, Any]]:
        """Enhanced search for GeneReviews returning multiple results with metadata."""
        params = {
            "db": "pubmed",
            "term": f"{gene_symbol}[All Fields] AND GeneReviews[book]",
            "retmode": "json",
            "retmax": retmax,
            "usehistory": "y"
        }
        data = await self._make_request("esearch.fcgi", params)
        result = data.get("esearchresult", {})
        
        return {
            "count": int(result.get("count", 0)),
            "retmax": int(result.get("retmax", 0)),
            "retstart": int(result.get("retstart", 0)),
            "ids": result.get("idlist", []),
            "webenv": result.get("webenv", ""),
            "querykey": result.get("querykey", "")
        }

    async def fetch_abstract(self, pubmed_id: str) -> Dict[str, Any]:
        """Fetch abstract and metadata from PubMed using efetch."""
        params = {
            "db": "pubmed",
            "id": pubmed_id,
            "retmode": "xml",
            "rettype": "abstract"
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

    def _parse_regular_article(self, article: ET.Element, pubmed_id: str) -> Dict[str, Any]:
        """Parse regular PubmedArticle XML structure."""
        article_data = {"pmid": pubmed_id}
        
        # Extract basic information
        medline_citation = article.find(".//MedlineCitation")
        if medline_citation is not None:
            pmid = medline_citation.find(".//PMID")
            if pmid is not None:
                article_data["pmid"] = pmid.text
                
        # Extract article details
        article_elem = article.find(".//Article")
        if article_elem is not None:
            # Title
            title = article_elem.find(".//ArticleTitle")
            if title is not None:
                article_data["title"] = title.text or ""
            
            # Abstract
            abstract_texts = []
            for abstract_text in article_elem.findall(".//Abstract/AbstractText"):
                if abstract_text.text:
                    label = abstract_text.get("Label", "")
                    text = abstract_text.text.strip()
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
                        name = last_name.text or ""
                        if first_name is not None and first_name.text:
                            name = f"{first_name.text} {name}"
                        authors.append(name)
            article_data["authors"] = authors
            
            # Journal
            journal = article_elem.find(".//Journal/Title")
            if journal is not None:
                article_data["journal"] = journal.text or ""
                
            # Publication date
            pub_date = article_elem.find(".//PubDate")
            if pub_date is not None:
                year = pub_date.find(".//Year")
                month = pub_date.find(".//Month") 
                day = pub_date.find(".//Day")
                
                date_parts = []
                if year is not None:
                    date_parts.append(year.text)
                if month is not None:
                    date_parts.append(month.text)
                if day is not None:
                    date_parts.append(day.text)
                
                article_data["publication_date"] = "-".join(date_parts) if date_parts else ""
        
        return article_data

    def _parse_book_article(self, book_article: ET.Element, pubmed_id: str) -> Dict[str, Any]:
        """Parse PubmedBookArticle XML structure (for GeneReviews)."""
        article_data = {"pmid": pubmed_id}
        
        book_document = book_article.find(".//BookDocument")
        if book_document is None:
            return article_data
            
        # Extract PMID
        pmid = book_document.find(".//PMID")
        if pmid is not None:
            article_data["pmid"] = pmid.text
            
        # Extract title - ArticleTitle for the specific chapter
        title = book_document.find(".//ArticleTitle")
        if title is not None:
            article_data["title"] = title.text or ""
        
        # Extract abstract - handle multiple AbstractText elements
        abstract_texts = []
        for abstract_text in book_document.findall(".//Abstract/AbstractText"):
            if abstract_text.text:
                label = abstract_text.get("Label", "")
                text = abstract_text.text.strip()
                if label and label.upper() != "UNLABELLED":
                    abstract_texts.append(f"{label}: {text}")
                else:
                    abstract_texts.append(text)
        
        article_data["abstract"] = " ".join(abstract_texts)
        
        # Extract authors - look for AuthorList with Type="authors"
        authors = []
        author_lists = book_document.findall(".//AuthorList")
        for author_list in author_lists:
            if author_list.get("Type") == "authors":
                for author in author_list.findall(".//Author"):
                    last_name = author.find(".//LastName")
                    first_name = author.find(".//ForeName")
                    if last_name is not None:
                        name = last_name.text or ""
                        if first_name is not None and first_name.text:
                            name = f"{first_name.text} {name}"
                        authors.append(name)
                break  # Use first authors list found
        article_data["authors"] = authors
        
        # Extract journal/book information
        book_title = book_document.find(".//Book/BookTitle")
        if book_title is not None:
            article_data["journal"] = book_title.text or "GeneReviews"
        else:
            article_data["journal"] = "GeneReviews"
            
        # Extract publication date - use ContributionDate or DateRevised
        contrib_date = book_document.find(".//ContributionDate")
        if contrib_date is not None:
            year = contrib_date.find(".//Year")
            month = contrib_date.find(".//Month")
            day = contrib_date.find(".//Day")
            
            date_parts = []
            if year is not None:
                date_parts.append(year.text)
            if month is not None:
                date_parts.append(month.text)
            if day is not None:
                date_parts.append(day.text)
                
            article_data["publication_date"] = "-".join(date_parts) if date_parts else ""
        else:
            # Fallback to book publication date
            book_pub_date = book_document.find(".//Book/PubDate")
            if book_pub_date is not None:
                year = book_pub_date.find(".//Year")
                if year is not None:
                    article_data["publication_date"] = year.text
        
        return article_data

    async def get_all_links(self, pubmed_id: str) -> Dict[str, List[str]]:
        """Get all available links from a PubMed ID using elink."""
        params = {
            "dbfrom": "pubmed",
            "id": pubmed_id,
            "cmd": "prlinks"
        }
        root = await self._make_xml_request("elink.fcgi", params)
        
        urls = []
        
        # Parse XML response for provider links
        for obj_url in root.findall(".//ObjUrl"):
            url_elem = obj_url.find(".//Url")
            if url_elem is not None and url_elem.text:
                urls.append(url_elem.text)
            
        return {"urls": urls}

    async def scrape_genereview_comprehensive(self, book_url: str) -> Dict[str, Any]:
        """Comprehensive scraping of a GeneReview book page with improved structure and fault tolerance."""
        try:
            response = await self._make_web_request(book_url)
            soup = BeautifulSoup(response.text, "lxml")
            
            # Extract NBK ID from URL
            nbk_match = re.search(r'NBK(\d+)', book_url)
            nbk_id = nbk_match.group(1) if nbk_match else None
            
            results = {
                "nbk_id": nbk_id,
                "url": book_url,
                "title": "",
                "sections": {},
                "metadata": {}
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

    def _find_main_content(self, soup: BeautifulSoup) -> Optional[BeautifulSoup]:
        """Find the main content container using multiple strategies."""
        # Strategy 1: Look for div with NBK ID that contains actual content
        nbk_divs = soup.find_all("div", {"id": lambda x: x and x.startswith('NBK')})
        for div in nbk_divs:
            # Check if this div has substantial content (h2/h3 headings)
            if div.find_all(['h2', 'h3']):
                return div
        
        # Strategy 2: Look for content areas with substantial headings
        content_selectors = [
            {"class": re.compile(r'.*content.*', re.I)},
            {"class": re.compile(r'.*main.*', re.I)},
            {"class": re.compile(r'.*article.*', re.I)},
            {"class": re.compile(r'.*chapter.*', re.I)},
            {"class": re.compile(r'.*body.*', re.I)},
            {"role": "main"},
        ]
        
        for selector in content_selectors:
            content_div = soup.find("div", selector)
            if content_div and content_div.find_all(['h2', 'h3']):
                return content_div
        
        # Strategy 3: Look for semantic HTML5 elements with content
        for tag in ["main", "article", "section"]:
            content_div = soup.find(tag)
            if content_div and content_div.find_all(['h2', 'h3']):
                return content_div
        
        # Strategy 4: Find any container with multiple h2/h3 headings
        all_containers = soup.find_all(['div', 'section', 'article'])
        for container in all_containers:
            headings = container.find_all(['h2', 'h3'])
            if len(headings) >= 3:  # Likely the main content if it has multiple sections
                return container
        
        # Strategy 5: Use the body as fallback
        return soup.find("body")

    def _extract_title(self, soup: BeautifulSoup, content_div: BeautifulSoup) -> str:
        """Extract document title using multiple strategies."""
        # Strategy 1: First h1 in content that's not 'Bookshelf'
        h1_tags = content_div.find_all('h1')
        for h1 in h1_tags:
            title = h1.get_text().strip()
            if title and title.lower() not in ['bookshelf', 'ncbi bookshelf']:
                return title
        
        # Strategy 2: Page title tag, cleaned up
        title_tag = soup.find('title')
        if title_tag:
            title = title_tag.get_text().strip()
            # Clean up common suffixes
            title = re.sub(r'\s*-\s*NCBI\s*Bookshelf.*$', '', title, flags=re.I)
            title = re.sub(r'\s*-\s*GeneReviews.*$', '', title, flags=re.I)
            title = re.sub(r'\s*-\s*PubMed.*$', '', title, flags=re.I)
            if title and title.lower() not in ['bookshelf', 'ncbi bookshelf']:
                return title
        
        # Strategy 3: Look for specific title classes or attributes
        title_selectors = [
            {"class": re.compile(r'.*title.*', re.I)},
            {"class": re.compile(r'.*heading.*', re.I)},
            {"data-title": True},
        ]
        
        for selector in title_selectors:
            elem = content_div.find(attrs=selector)
            if elem:
                title = elem.get_text().strip()
                if title and title.lower() not in ['bookshelf', 'ncbi bookshelf']:
                    return title
        
        # Strategy 4: Look for meta tags
        meta_title = soup.find("meta", {"property": "og:title"})
        if meta_title and meta_title.get("content"):
            return meta_title["content"].strip()
        
        meta_title = soup.find("meta", {"name": "dc.title"})
        if meta_title and meta_title.get("content"):
            return meta_title["content"].strip()
        
        return "Unknown Document"

    def _extract_metadata(self, soup: BeautifulSoup, content_div: BeautifulSoup) -> Dict[str, Any]:
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
            metadata["references"] = references
        
        return metadata

    def _extract_authors(self, content_div: BeautifulSoup) -> Optional[str]:
        """Extract author information."""
        patterns = [
            re.compile(r'Author[s]?\s*:', re.I),
            re.compile(r'By\s*:', re.I),
            re.compile(r'Written\s*by', re.I),
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
                        author_text = text[match.end():].strip()
                        if author_text:
                            return author_text
        
        return None

    def _extract_update_info(self, content_div: BeautifulSoup) -> Optional[str]:
        """Extract update information."""
        patterns = [
            re.compile(r'Last\s*(Updated?|Revision)', re.I),
            re.compile(r'Updated?\s*:', re.I),
            re.compile(r'Revision\s*History', re.I),
            re.compile(r'Initial\s*Posting', re.I),
        ]
        
        for pattern in patterns:
            elements = content_div.find_all(string=pattern)
            for elem in elements:
                parent = elem.parent
                if parent:
                    return parent.get_text().strip()
        
        return None

    def _extract_publication_info(self, content_div: BeautifulSoup) -> Optional[str]:
        """Extract publication and copyright information."""
        patterns = [
            re.compile(r'Copyright', re.I),
            re.compile(r'Published', re.I),
            re.compile(r'Citation', re.I),
        ]
        
        for pattern in patterns:
            elements = content_div.find_all(string=pattern)
            for elem in elements:
                parent = elem.parent
                if parent:
                    return parent.get_text().strip()
        
        return None

    def _extract_last_updated(self, content_div: BeautifulSoup) -> Optional[str]:
        """Extract last updated date."""
        # Look for date patterns
        date_pattern = re.compile(r'\b\d{4}[-/]\d{1,2}[-/]\d{1,2}\b|\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,2},?\s+\d{4}\b', re.I)
        
        update_elements = content_div.find_all(string=re.compile(r'updated?|revised?', re.I))
        for elem in update_elements:
            parent = elem.parent
            if parent:
                text = parent.get_text()
                match = date_pattern.search(text)
                if match:
                    return match.group().strip()
        
        return None

    def _extract_references(self, content_div: BeautifulSoup) -> List[str]:
        """Extract and parse references section as a list of strings."""
        references = []
        
        # Find references section
        ref_headings = content_div.find_all(['h1', 'h2', 'h3', 'h4', 'h5', 'h6'], 
                                          string=re.compile(r'references?|bibliography', re.I))
        
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
            if current_pos and current_pos.name in ['h1', 'h2', 'h3'] and current_pos != ref_heading:
                break
            
            # Collect content elements that likely contain references
            if current_pos and hasattr(current_pos, 'name') and current_pos.name in ['p', 'div', 'ul', 'ol', 'li']:
                ref_content_elements.append(current_pos)
        
        # Extract and process references
        for element in ref_content_elements:
            text = element.get_text().strip()
            if text and len(text) > 30:  # Filter out very short text
                # Split on patterns that typically separate references
                # Look for author patterns at start of lines/sentences
                potential_refs = re.split(r'(?=\b[A-Z][a-z]+\s+[A-Z]{1,2}(?:[a-z]*)?(?:,\s*[A-Z][a-z]+\s+[A-Z]{1,2})?.*?\.\s)', text)
                
                for ref in potential_refs:
                    ref = ref.strip()
                    # Clean up the reference text
                    if len(ref) > 50 and re.search(r'[A-Z][a-z]+.*?\d{4}', ref):  # Must contain author-like pattern and year
                        # Clean up common formatting issues
                        ref = re.sub(r'\s+', ' ', ref)  # Normalize whitespace
                        ref = re.sub(r'^\d+\.\s*', '', ref)  # Remove leading numbers
                        ref = ref.strip()
                        if ref and not ref.lower().startswith(('http', 'www', 'doi:', 'pmid:')):
                            references.append(ref)
        
        # If the above didn't work well, try a simpler approach
        if len(references) < 5:  # If we didn't get many references, try alternative method
            references = []
            
            # Get all text after references heading and split on common patterns
            full_text = ""
            current_pos = ref_heading
            while current_pos:
                current_pos = current_pos.find_next()
                if current_pos and current_pos.name in ['h1', 'h2', 'h3'] and current_pos != ref_heading:
                    break
                if current_pos and hasattr(current_pos, 'get_text'):
                    full_text += " " + current_pos.get_text()
            
            # Split on numbered references or author patterns
            ref_patterns = [
                r'\d+\.\s*([A-Z][^.]*?(?:\d{4}[^.]*?\.(?:\s*\[PubMed\]|\s*\[PMC[^\]]*\])?)?)',
                r'([A-Z][a-z]+\s+[A-Z]{1,2}[^.]*?\d{4}[^.]*?\.(?:\s*\[PubMed\]|\s*\[PMC[^\]]*\])?)',
            ]
            
            for pattern in ref_patterns:
                matches = re.findall(pattern, full_text, re.MULTILINE | re.DOTALL)
                for match in matches:
                    ref = match.strip()
                    if len(ref) > 50:
                        ref = re.sub(r'\s+', ' ', ref)
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

    def _parse_reference(self, ref_text: str) -> Dict[str, Any]:
        """Parse a single reference into structured data."""
        ref_data = {"text": ref_text}
        
        # Extract PMID
        pmid_match = re.search(r'PMID:?\s*(\d+)', ref_text, re.I)
        if pmid_match:
            ref_data["pmid"] = pmid_match.group(1)
        
        # Extract year (4 digits)
        year_match = re.search(r'\b(19|20)\d{2}\b', ref_text)
        if year_match:
            ref_data["year"] = year_match.group()
        
        # Extract authors (before first period or semicolon)
        author_match = re.match(r'^([^.;]+)', ref_text)
        if author_match:
            authors = author_match.group(1).strip()
            if not re.search(r'\d{4}', authors):  # Make sure it doesn't contain year
                ref_data["authors"] = authors
        
        # Extract title (often in quotes or after authors before journal)
        title_patterns = [
            re.compile(r'"([^"]+)"'),  # Quoted title
            re.compile(r'\.([^.]+)\.\s*[A-Z]'),  # Title between periods
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
            re.compile(r'\.\s*([A-Z][^.]+?)\.\s*\d{4}'),  # Journal before year
            re.compile(r'\b([A-Z][a-z]*\s+[A-Z][a-z]*(?:\s+[A-Z][a-z]*)*)\s*\.\s*\d{4}'),  # Multi-word journal
        ]
        
        for pattern in journal_patterns:
            journal_match = pattern.search(ref_text)
            if journal_match:
                journal = journal_match.group(1).strip()
                if len(journal) > 3 and not re.search(r'\d', journal):
                    ref_data["journal"] = journal
                break
        
        return ref_data

    def _extract_hierarchical_sections(self, content_div: BeautifulSoup) -> Dict[str, Dict[str, Any]]:
        """Extract sections with hierarchical structure based on heading tags."""
        sections = {}
        
        # Get all h2 and h3 headings in the content (focus on these as requested)
        headings = content_div.find_all(['h2', 'h3'])
        
        # Filter out headings that are likely navigation or headers (not content)
        content_headings = []
        for heading in headings:
            heading_text = heading.get_text().strip()
            # Skip very short headings or common navigation headings
            if (len(heading_text) > 2 and 
                heading_text.lower() not in ['menu', 'navigation', 'skip', 'search', 'bookshelf', 'ncbi bookshelf'] and
                not heading_text.startswith('Figure') and
                not heading_text.startswith('Table') and
                not re.match(r'^(Search|Browse|Help|Site)', heading_text, re.I)):
                content_headings.append(heading)
        
        # Extract sections based on headings
        for i, heading in enumerate(content_headings):
            section_title = heading.get_text().strip()
            if not section_title:
                continue
            
            # Find the next heading at the same level or higher to know where this section ends
            next_heading = None
            current_level = int(heading.name[1])  # h2 = 2, h3 = 3
            
            for j in range(i + 1, len(content_headings)):
                next_candidate = content_headings[j]
                next_level = int(next_candidate.name[1])
                if next_level <= current_level:  # Same level or higher (h2 ends at next h2 or h1, h3 ends at h3, h2, or h1)
                    next_heading = next_candidate
                    break
            
            # Collect all content between this heading and the next
            content_parts = []
            
            # Find all elements that come after this heading but before the next heading
            current_pos = heading
            
            # Get all following siblings and their descendants
            while current_pos:
                current_pos = current_pos.find_next()
                
                # Stop if we've reached the next heading
                if current_pos and next_heading and (current_pos == next_heading or next_heading in current_pos.find_all_previous(limit=1)):
                    break
                
                # Stop if we've reached another heading at same or higher level
                if current_pos and current_pos.name in ['h1', 'h2', 'h3'] and current_pos in content_headings:
                    candidate_level = int(current_pos.name[1])
                    if candidate_level <= current_level:
                        break
                
                # Extract text from content elements
                if current_pos and hasattr(current_pos, 'name') and current_pos.name in ['p', 'div', 'ul', 'ol', 'li', 'section', 'article']:
                    text = current_pos.get_text().strip()
                    
                    # Filter out unwanted content
                    if (text and len(text) > 15 and 
                        'federal government' not in text.lower() and
                        'the .gov means' not in text.lower() and
                        'https://' not in text.lower() and
                        'the site is secure' not in text.lower() and
                        not re.search(r'(sharing sensitive information|encrypted and transmitted securely)', text.lower())):
                        content_parts.append(text)
                
                # Safety check to prevent infinite loops
                if len(content_parts) > 100:
                    break
            
            # Create section if we found content
            if content_parts:
                section_key = self._normalize_section_key(section_title)
                section_content = ' '.join(content_parts).strip()
                
                # Clean up the content
                section_content = re.sub(r'\s+', ' ', section_content)  # Normalize whitespace
                section_content = re.sub(r'(\n\s*){3,}', '\n\n', section_content)  # Limit line breaks
                
                if len(section_content) > 50:  # Only include sections with substantial content
                    sections[section_key] = {
                        'title': section_title,
                        'content': section_content,
                        'level': current_level,
                        'subsections': {}  # Initialize subsections as required by model
                    }
        
        return sections

    def _normalize_section_key(self, title: str) -> str:
        """Normalize section title for use as dictionary key."""
        # Remove special characters and normalize spacing
        key = re.sub(r'[^\w\s-]', '', title.lower())
        key = re.sub(r'[-\s]+', '_', key.strip())
        # Remove common prefixes/suffixes
        key = re.sub(r'^(the_|a_|an_)', '', key)
        key = re.sub(r'(_section|_chapter)$', '', key)
        return key[:50]  # Limit length

    async def close(self):
        await self.client.aclose()