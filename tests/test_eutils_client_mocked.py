"""Unit tests for EutilsClient using respx-mocked HTTP responses.

These tests exercise the JSON/XML eutils helpers and the parser branches that
would otherwise only be hit by live NCBI traffic.
"""

from __future__ import annotations

import pytest
import respx
from httpx import Response

from genereview_link.api.eutils_client import EutilsClient
from genereview_link.config import settings


@pytest.fixture
def client() -> EutilsClient:
    """Fresh client per test - we never share state across tests."""
    return EutilsClient()


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
        assert result == {"urls": ["https://example.com/a", "https://example.com/b"]}

    @pytest.mark.asyncio
    @respx.mock
    async def test_no_urls(self, client: EutilsClient) -> None:
        xml = "<?xml version='1.0'?><eLinkResult><LinkSet/></eLinkResult>"
        respx.get(_eutils_url("elink.fcgi")).mock(
            return_value=Response(200, content=xml.encode("utf-8"))
        )
        result = await client.get_all_links("20301425")
        assert result == {"urls": []}


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


class TestFetchAbstractBook:
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
