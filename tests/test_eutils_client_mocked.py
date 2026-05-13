"""Unit tests for EutilsClient using respx-mocked HTTP responses.

These tests exercise the JSON/XML eutils helpers and the parser branches that
would otherwise only be hit by live NCBI traffic.
"""

from __future__ import annotations

import xml.etree.ElementTree as StdET
from collections.abc import Callable
from pathlib import Path

import pytest
import respx
from defusedxml import ElementTree as ET  # noqa: N817 - drop-in replacement for stdlib ET
from httpx import Response

from genereview_link.api.eutils_client import EutilsClient
from genereview_link.config import settings


@pytest.fixture
def client() -> EutilsClient:
    """Fresh client per test - we never share state across tests."""
    return EutilsClient()


@pytest.fixture
def load_xml() -> Callable[[str], StdET.Element]:
    def _load_xml(name: str) -> StdET.Element:
        fixture_path = Path(__file__).parent / "fixtures" / name
        return ET.fromstring(fixture_path.read_text(encoding="utf-8"))

    return _load_xml


def _eutils_url(endpoint: str) -> str:
    return f"{settings.EUTILS_BASE_URL}/{endpoint}"


class TestSearchGenereviewPmid:
    @pytest.mark.asyncio
    @respx.mock
    async def test_returns_first_id(self, client: EutilsClient) -> None:
        respx.get(_eutils_url("esearch.fcgi")).mock(
            return_value=Response(
                200,
                json={"esearchresult": {"idlist": ["20301425", "999999"]}},
            )
        )
        pmid = await client.search_genereview_pmid("BRCA1")
        assert pmid == "20301425"

    @pytest.mark.asyncio
    @respx.mock
    async def test_returns_none_when_empty(self, client: EutilsClient) -> None:
        respx.get(_eutils_url("esearch.fcgi")).mock(
            return_value=Response(200, json={"esearchresult": {"idlist": []}})
        )
        pmid = await client.search_genereview_pmid("NOTREAL")
        assert pmid is None


class TestSearchGenereviews:
    @pytest.mark.asyncio
    @respx.mock
    async def test_returns_full_structure(self, client: EutilsClient) -> None:
        respx.get(_eutils_url("esearch.fcgi")).mock(
            return_value=Response(
                200,
                json={
                    "esearchresult": {
                        "count": "2",
                        "retmax": "2",
                        "retstart": "0",
                        "idlist": ["a", "b"],
                        "webenv": "env",
                        "querykey": "1",
                    }
                },
            )
        )
        result = await client.search_genereviews("BRCA1", retmax=2)
        assert result["count"] == 2
        assert result["ids"] == ["a", "b"]
        assert result["webenv"] == "env"
        assert result["querykey"] == "1"


class TestGetBookUrlFromPmid:
    @pytest.mark.asyncio
    async def test_get_book_url_from_pmid_accepts_pubmed_books_linkset(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = EutilsClient()

        async def fake_request(endpoint: str, params: dict[str, object]) -> dict[str, object]:
            assert endpoint == "elink.fcgi"
            assert params["dbfrom"] == "pubmed"
            assert params["id"] == "20301425"
            assert params["retmode"] == "json"
            assert "cmd" not in params
            return {
                "linksets": [
                    {
                        "linksetdbs": [
                            {"dbto": "pubmed_books", "links": ["1247"]},
                        ]
                    }
                ]
            }

        monkeypatch.setattr(client, "_make_request", fake_request)
        assert await client.get_book_url_from_pmid("20301425") == (
            "https://www.ncbi.nlm.nih.gov/books/NBK1247/"
        )

    @pytest.mark.asyncio
    @respx.mock
    async def test_returns_book_url_when_present(self, client: EutilsClient) -> None:
        respx.get(_eutils_url("elink.fcgi")).mock(
            return_value=Response(
                200,
                json={
                    "linksets": [
                        {
                            "linksetdbs": [
                                {"dbto": "pmc", "links": ["1"]},
                                {"dbto": "books", "links": ["1247"]},
                            ]
                        }
                    ]
                },
            )
        )
        url = await client.get_book_url_from_pmid("20301425")
        assert url == "https://www.ncbi.nlm.nih.gov/books/NBK1247/"

    @pytest.mark.asyncio
    @respx.mock
    async def test_returns_none_when_no_linksets(self, client: EutilsClient) -> None:
        respx.get(_eutils_url("elink.fcgi")).mock(return_value=Response(200, json={"linksets": []}))
        url = await client.get_book_url_from_pmid("20301425")
        assert url is None

    @pytest.mark.asyncio
    @respx.mock
    async def test_returns_none_when_no_books_dbto(self, client: EutilsClient) -> None:
        respx.get(_eutils_url("elink.fcgi")).mock(
            return_value=Response(
                200,
                json={"linksets": [{"linksetdbs": [{"dbto": "pmc", "links": ["1"]}]}]},
            )
        )
        url = await client.get_book_url_from_pmid("20301425")
        assert url is None


class TestGetAllLinks:
    def test_parse_prlinks_entries(self, load_xml: Callable[[str], StdET.Element]) -> None:
        root = load_xml("elink/PMID20301425_prlinks.xml")
        client = EutilsClient()

        entries = client._parse_link_entries(root)

        assert entries == [
            {
                "url": "https://www.ncbi.nlm.nih.gov/books/NBK1247/",
                "link_type": "prlinks",
                "provider": "NCBI Bookshelf",
            }
        ]

    def test_parse_llinks_entries(self, load_xml: Callable[[str], StdET.Element]) -> None:
        root = load_xml("elink/PMID20301425_llinks.xml")
        client = EutilsClient()

        entries = client._parse_link_entries(root)

        assert entries == [
            {
                "url": "https://example.org/fulltext",
                "link_type": "llinks",
                "provider": "llinks",
            }
        ]

    def test_parse_neighbor_entries(self, load_xml: Callable[[str], StdET.Element]) -> None:
        root = load_xml("elink/PMID20301425_neighbor.xml")
        client = EutilsClient()

        entries = client._parse_link_entries(root)

        assert {
            "url": "https://www.ncbi.nlm.nih.gov/books/NBK1247/",
            "link_type": "books",
            "provider": "NCBI Bookshelf",
        } in entries
        assert {
            "url": "https://www.ncbi.nlm.nih.gov/pmc/articles/PMC123456/",
            "link_type": "pmc",
            "provider": "PubMed Central",
        } in entries

    @pytest.mark.asyncio
    @respx.mock
    async def test_parses_object_urls(self, client: EutilsClient) -> None:
        xml = (
            "<?xml version='1.0'?>"
            "<eLinkResult><LinkSet><IdUrlList><IdUrlSet>"
            "<ObjUrl><Url>https://example.com/a</Url></ObjUrl>"
            "<ObjUrl><Url>https://example.com/b</Url></ObjUrl>"
            "</IdUrlSet></IdUrlList></LinkSet></eLinkResult>"
        )
        respx.get(_eutils_url("elink.fcgi")).mock(
            return_value=Response(200, content=xml.encode("utf-8"))
        )
        result = await client.get_all_links("20301425")
        assert result["urls"] == ["https://example.com/a", "https://example.com/b"]
        assert result["by_type"]["llinks"] == ["https://example.com/a", "https://example.com/b"]

    @pytest.mark.asyncio
    @respx.mock
    async def test_no_urls(self, client: EutilsClient) -> None:
        xml = "<?xml version='1.0'?><eLinkResult><LinkSet/></eLinkResult>"
        respx.get(_eutils_url("elink.fcgi")).mock(
            return_value=Response(200, content=xml.encode("utf-8"))
        )
        result = await client.get_all_links("20301425")
        assert result == {"urls": [], "link_entries": [], "by_type": {}}

    @pytest.mark.asyncio
    @respx.mock
    async def test_uses_llinks_primary_for_book_urls(self, client: EutilsClient) -> None:
        llinks_xml = (
            "<?xml version='1.0'?>"
            "<eLinkResult><LinkSet><IdUrlList><IdUrlSet>"
            "<ObjUrl>"
            "<Url>https://www.ncbi.nlm.nih.gov/books/NBK1247/</Url>"
            "<Category>llinks</Category>"
            "</ObjUrl>"
            "</IdUrlSet></IdUrlList></LinkSet></eLinkResult>"
        )
        route = respx.get(_eutils_url("elink.fcgi")).mock(
            return_value=Response(200, content=llinks_xml.encode("utf-8"))
        )

        result = await client.get_all_links("20301425")

        assert route.call_count == 1
        assert route.calls[0].request.url.params["cmd"] == "llinks"
        assert result["urls"] == ["https://www.ncbi.nlm.nih.gov/books/NBK1247/"]
        assert result["by_type"]["llinks"] == ["https://www.ncbi.nlm.nih.gov/books/NBK1247/"]


class TestFetchAbstractRegular:
    @pytest.mark.asyncio
    @respx.mock
    async def test_parses_regular_article(self, client: EutilsClient) -> None:
        xml = """<?xml version="1.0"?>
        <PubmedArticleSet>
          <PubmedArticle>
            <MedlineCitation>
              <PMID>12345</PMID>
              <Article>
                <ArticleTitle>Sample Title</ArticleTitle>
                <Abstract>
                  <AbstractText Label="BACKGROUND">Bg text</AbstractText>
                  <AbstractText>Main text</AbstractText>
                </Abstract>
                <AuthorList>
                  <Author>
                    <LastName>Doe</LastName>
                    <ForeName>Jane</ForeName>
                  </Author>
                  <Author>
                    <LastName>Smith</LastName>
                  </Author>
                </AuthorList>
                <Journal>
                  <Title>Journal of Tests</Title>
                </Journal>
                <PubDate>
                  <Year>2024</Year>
                  <Month>06</Month>
                  <Day>15</Day>
                </PubDate>
              </Article>
            </MedlineCitation>
          </PubmedArticle>
        </PubmedArticleSet>
        """
        respx.get(_eutils_url("efetch.fcgi")).mock(
            return_value=Response(200, content=xml.encode("utf-8"))
        )
        result = await client.fetch_abstract("12345")
        assert result["pmid"] == "12345"
        assert result["title"] == "Sample Title"
        assert "Bg text" in result["abstract"]
        assert "Main text" in result["abstract"]
        assert "Jane Doe" in result["authors"]
        assert "Smith" in result["authors"]
        assert result["journal"] == "Journal of Tests"
        assert result["publication_date"] == "2024-06-15"

    @pytest.mark.asyncio
    @respx.mock
    async def test_preserves_inline_xml_text(self, client: EutilsClient) -> None:
        xml = """<?xml version="1.0"?>
        <PubmedArticleSet>
          <PubmedArticle>
            <MedlineCitation>
              <PMID>12345</PMID>
              <Article>
                <ArticleTitle><i>GRIN2A</i>-Related Disorders</ArticleTitle>
                <Abstract>
                  <AbstractText Label="CLINICAL CHARACTERISTICS"><i>GRIN2A</i>-related disorders encompass a broad phenotypic spectrum.</AbstractText>
                  <AbstractText Label="DIAGNOSIS/TESTING">The diagnosis of a <i>GRIN2A</i>-related disorder is established by molecular genetic testing.</AbstractText>
                </Abstract>
              </Article>
            </MedlineCitation>
          </PubmedArticle>
        </PubmedArticleSet>
        """
        respx.get(_eutils_url("efetch.fcgi")).mock(
            return_value=Response(200, content=xml.encode("utf-8"))
        )
        result = await client.fetch_abstract("12345")
        assert result["title"] == "GRIN2A-Related Disorders"
        assert (
            "CLINICAL CHARACTERISTICS: GRIN2A-related disorders encompass "
            "a broad phenotypic spectrum."
        ) in result["abstract"]
        assert (
            "DIAGNOSIS/TESTING: The diagnosis of a GRIN2A-related disorder "
            "is established by molecular genetic testing."
        ) in result["abstract"]


class TestFetchAbstractBook:
    def test_parse_book_article_preserves_title_and_labeled_abstract(self, load_xml) -> None:
        root = load_xml("efetch/NBK1247_book_article.xml")
        article = root.find(".//PubmedBookArticle")
        client = EutilsClient()

        parsed = client._parse_book_article(article, "20301425")

        assert parsed["title"]
        assert "DIAGNOSIS/TESTING:" in parsed["abstract"]
        assert "GENETIC COUNSELING:" in parsed["abstract"]
        assert "autosomal dominant manner" in parsed["abstract"]
        assert not parsed["abstract"].endswith("of")

    @pytest.mark.asyncio
    @respx.mock
    async def test_parses_book_article(self, client: EutilsClient) -> None:
        xml = """<?xml version="1.0"?>
        <PubmedArticleSet>
          <PubmedBookArticle>
            <BookDocument>
              <PMID>99999</PMID>
              <ArticleTitle>BRCA1 GeneReview</ArticleTitle>
              <Abstract>
                <AbstractText Label="UNLABELLED">Plain content</AbstractText>
                <AbstractText Label="CLINICAL">Clinical content</AbstractText>
              </Abstract>
              <AuthorList Type="authors">
                <Author>
                  <LastName>Genome</LastName>
                  <ForeName>Anne</ForeName>
                </Author>
              </AuthorList>
              <Book>
                <BookTitle>GeneReviews</BookTitle>
                <PubDate>
                  <Year>2023</Year>
                </PubDate>
              </Book>
              <ContributionDate>
                <Year>2024</Year>
                <Month>01</Month>
              </ContributionDate>
            </BookDocument>
          </PubmedBookArticle>
        </PubmedArticleSet>
        """
        respx.get(_eutils_url("efetch.fcgi")).mock(
            return_value=Response(200, content=xml.encode("utf-8"))
        )
        result = await client.fetch_abstract("99999")
        assert result["pmid"] == "99999"
        assert result["title"] == "BRCA1 GeneReview"
        assert "Plain content" in result["abstract"]
        assert "CLINICAL: Clinical content" in result["abstract"]
        assert "Anne Genome" in result["authors"]
        assert result["journal"] == "GeneReviews"
        assert result["publication_date"] == "2024-01"

    @pytest.mark.asyncio
    @respx.mock
    async def test_preserves_inline_xml_text(self, client: EutilsClient) -> None:
        xml = """<?xml version="1.0"?>
        <PubmedArticleSet>
          <PubmedBookArticle>
            <BookDocument>
              <PMID>27683935</PMID>
              <ArticleTitle><i>GRIN2A</i>-Related Disorders</ArticleTitle>
              <Abstract>
                <AbstractText Label="CLINICAL CHARACTERISTICS"><i>GRIN2A</i>-related disorders encompass a broad phenotypic spectrum.</AbstractText>
                <AbstractText Label="DIAGNOSIS/TESTING">The diagnosis of a <i>GRIN2A</i>-related disorder is established by molecular genetic testing.</AbstractText>
                <AbstractText Label="MANAGEMENT">Targeted therapy and supportive care are recommended.</AbstractText>
              </Abstract>
              <Book>
                <BookTitle><i>GeneReviews</i></BookTitle>
              </Book>
            </BookDocument>
          </PubmedBookArticle>
        </PubmedArticleSet>
        """
        respx.get(_eutils_url("efetch.fcgi")).mock(
            return_value=Response(200, content=xml.encode("utf-8"))
        )
        result = await client.fetch_abstract("27683935")
        assert result["title"] == "GRIN2A-Related Disorders"
        assert (
            "CLINICAL CHARACTERISTICS: GRIN2A-related disorders encompass "
            "a broad phenotypic spectrum."
        ) in result["abstract"]
        assert (
            "DIAGNOSIS/TESTING: The diagnosis of a GRIN2A-related disorder "
            "is established by molecular genetic testing."
        ) in result["abstract"]
        assert (
            "MANAGEMENT: Targeted therapy and supportive care are recommended."
            in result["abstract"]
        )
        assert result["journal"] == "GeneReviews"

    @pytest.mark.asyncio
    @respx.mock
    async def test_book_article_without_contribution_date_falls_back(
        self, client: EutilsClient
    ) -> None:
        xml = """<?xml version="1.0"?>
        <PubmedArticleSet>
          <PubmedBookArticle>
            <BookDocument>
              <PMID>1</PMID>
              <ArticleTitle>X</ArticleTitle>
              <Book>
                <PubDate>
                  <Year>2020</Year>
                </PubDate>
              </Book>
            </BookDocument>
          </PubmedBookArticle>
        </PubmedArticleSet>
        """
        respx.get(_eutils_url("efetch.fcgi")).mock(
            return_value=Response(200, content=xml.encode("utf-8"))
        )
        result = await client.fetch_abstract("1")
        assert result["pmid"] == "1"
        assert result["publication_date"] == "2020"
        # Default journal when no BookTitle.
        assert result["journal"] == "GeneReviews"

    @pytest.mark.asyncio
    @respx.mock
    async def test_neither_article_type_returns_empty(self, client: EutilsClient) -> None:
        xml = "<?xml version='1.0'?><PubmedArticleSet/>"
        respx.get(_eutils_url("efetch.fcgi")).mock(
            return_value=Response(200, content=xml.encode("utf-8"))
        )
        result = await client.fetch_abstract("12345")
        assert result == {}


class TestCloseLifecycle:
    @pytest.mark.asyncio
    async def test_close_does_not_raise(self) -> None:
        client = EutilsClient()
        await client.close()
