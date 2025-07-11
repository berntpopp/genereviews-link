import asyncio
import logging
import re
from typing import Any, List, Dict
from xml.etree import ElementTree as ET

import httpx
from bs4 import BeautifulSoup

from genereview_link.config import settings

logger = logging.getLogger(__name__)

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
        
        await asyncio.sleep(self.rate_limit_delay) # Respect NCBI rate limits
        
        try:
            response = await self.client.get(f"{self.base_url}/{endpoint}", params=params)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP error for {e.request.url}: {e}")
            raise
        except Exception as e:
            logger.error(f"Request failed: {e}")
            raise

    async def _make_web_request(self, url: str, max_retries: int = 3) -> httpx.Response:
        """Make a web request with retries and proper rate limiting for scraping."""
        for attempt in range(max_retries):
            try:
                # Longer delay for web scraping to be respectful
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
        
        await asyncio.sleep(self.rate_limit_delay) # Respect NCBI rate limits
        
        try:
            response = await self.client.get(f"{self.base_url}/{endpoint}", params=params)
            response.raise_for_status()
            return ET.fromstring(response.text)
        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP error for {e.request.url}: {e}")
            raise
        except Exception as e:
            logger.error(f"Request failed: {e}")
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
        try:
            response = await self._make_web_request(book_url)
            soup = BeautifulSoup(response.text, "lxml")
            
            results = {}
            # The main content is within a div with id='NBK1116' or similar
            content_div = soup.find("div", {"id": lambda x: x and x.startswith('NBK')})
            if not content_div:
                return {}

            # Extract title
            title_tag = content_div.find('h1')
            if title_tag:
                 results['title'] = {'title': 'Title', 'content': title_tag.text.strip()}

            # Scrape sections based on h2 tags with an id
            for h2 in content_div.find_all('h2', id=True):
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
            
            return results
        except Exception as e:
            logger.error(f"Failed to scrape {book_url}: {e}")
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
        """Comprehensive scraping of a GeneReview book page."""
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
            
            # Find main content container
            content_div = soup.find("div", {"id": lambda x: x and x.startswith('NBK')})
            if not content_div:
                # Try alternative selectors
                content_div = soup.find("div", class_="chapter") or soup.find("main") or soup
            
            # Extract title
            title_tag = content_div.find('h1')
            if title_tag:
                results['title'] = title_tag.text.strip()
            
            # Extract metadata (authors, publication info, etc.)
            metadata = {}
            
            # Look for author information
            authors_section = soup.find("div", class_="authors") or soup.find("p", string=re.compile(r"Authors?:"))
            if authors_section:
                metadata["authors"] = authors_section.get_text(strip=True)
            
            # Look for update information
            update_info = soup.find(string=re.compile(r"Initial Posting|Last Update"))
            if update_info:
                metadata["update_info"] = update_info.strip()
                
            results["metadata"] = metadata
            
            # Extract sections - improved section detection
            sections = {}
            
            # Method 1: Find h2 with IDs (most reliable)
            for h2 in content_div.find_all(['h2', 'h3'], id=True):
                section_title = h2.text.strip()
                if not section_title:
                    continue
                    
                # Collect content until next heading
                content_parts = []
                for sibling in h2.find_next_siblings():
                    if sibling.name in ['h1', 'h2', 'h3'] and sibling.get('id'):
                        break
                    if sibling.name:
                        content_parts.append(sibling.get_text(separator=' ', strip=True))
                
                section_content = ' '.join(content_parts).strip()
                if section_content:
                    key = section_title.lower().replace(" ", "_").replace(".", "").replace(",", "")
                    sections[key] = {
                        'title': section_title,
                        'content': section_content
                    }
            
            # Method 2: If no sections found, try alternative approach
            if not sections:
                for heading in content_div.find_all(['h2', 'h3', 'h4']):
                    section_title = heading.text.strip()
                    if not section_title or len(section_title) < 3:
                        continue
                        
                    # Get next paragraph or div
                    next_elem = heading.find_next_sibling(['p', 'div'])
                    if next_elem:
                        content = next_elem.get_text(separator=' ', strip=True)
                        if content and len(content) > 20:  # Minimum content length
                            key = section_title.lower().replace(" ", "_").replace(".", "").replace(",", "")
                            sections[key] = {
                                'title': section_title,
                                'content': content
                            }
            
            results["sections"] = sections
            
            return results
        except Exception as e:
            logger.error(f"Failed to scrape {book_url}: {e}")
            return {"error": str(e), "url": book_url}

    async def close(self):
        await self.client.aclose()