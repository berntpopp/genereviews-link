"""Unit tests for EutilsClient._extract_metadata references field type.

Regression for #34: references must be returned as list[str], not a
newline-joined string.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from bs4 import BeautifulSoup, Tag

from genereview_link.api.eutils_client import EutilsClient

FIXTURE_PATH = Path(__file__).parent.parent / "fixtures" / "NBK1247_BRCA1.html"

MINIMAL_REFERENCES_HTML = """\
<html>
<body>
<div class="main-content lit-style">
  <h2>Summary</h2>
  <p>Some summary content.</p>
  <h2>References</h2>
  <ul>
    <li><div class="bk_ref">Alhopuro P, Phichith D, Tuupanen S, Sammalkorpi H,
    Nybondas M, Gylfe AE, Robinson JP, Yang D, Chen LQ, Orntoft TF, Mecklin JP,
    Jarvinen H, Eng C, Moeslein G, Shibata D, Houlston RS, Tomlinson I,
    Launonen V, Ristimaki A, Aaltonen LA. Unregulated smooth-muscle myosin in
    human intestinal neoplasia. Proc Natl Acad Sci U S A. 2008;105:5513-8.
    [PubMed]</div></li>
    <li><div class="bk_ref">Smith AB, Jones CD, Brown EF. Another important study
    with real authors and a publication year 2019. J Genet Med. 2019;12:100-110.
    [PubMed]</div></li>
    <li><div class="bk_ref">Williams GH, Taylor MK, Anderson LR, Carter PQ.
    Genomic analysis of hereditary cancer syndromes in the modern era 2021.
    Nat Genet. 2021;53:500-515. [PubMed]</div></li>
    <li><div class="bk_ref">Johnson RS, Davis KL, Miller TF, Wilson AB, Thompson CD.
    Comprehensive review of BRCA variant interpretation frameworks 2020.
    Am J Hum Genet. 2020;107:800-815. [PubMed]</div></li>
    <li><div class="bk_ref">Garcia MN, Martinez OP, Rivera SR, Gonzalez EB.
    Population-based estimates of hereditary breast cancer prevalence 2022.
    Cancer Epidemiol. 2022;78:102-120. [PubMed]</div></li>
  </ul>
</div>
</body>
</html>
"""


@pytest.fixture
def eutils_client() -> EutilsClient:
    return EutilsClient()


def _parse_html(html: str) -> tuple[BeautifulSoup, Tag]:
    """Return (soup, content_div) for the given HTML string."""
    soup = BeautifulSoup(html, "html.parser")
    content_div = soup.find("div", {"class": "main-content lit-style"})
    assert isinstance(content_div, Tag), "Test HTML must contain main-content div"
    return soup, content_div


class TestExtractMetadataReferencesType:
    def test_references_is_list_with_minimal_html(self, eutils_client: EutilsClient) -> None:
        """_extract_metadata must return references as list[str], not str."""
        soup, content_div = _parse_html(MINIMAL_REFERENCES_HTML)
        metadata = eutils_client._extract_metadata(soup, content_div)
        assert "references" in metadata, "metadata must contain references key"
        refs = metadata["references"]
        assert isinstance(refs, list), f"references must be list[str], got {type(refs).__name__!r}"
        assert len(refs) > 0, "references list must be non-empty"
        for item in refs:
            assert isinstance(item, str), (
                f"each reference entry must be str, got {type(item).__name__!r}"
            )

    def test_references_not_joined_string(self, eutils_client: EutilsClient) -> None:
        """references must not be a newline-joined string (pre-fix regression)."""
        soup, content_div = _parse_html(MINIMAL_REFERENCES_HTML)
        metadata = eutils_client._extract_metadata(soup, content_div)
        if "references" in metadata:
            assert not isinstance(metadata["references"], str), (
                "references must not be a plain string; the bug was joining them with newlines"
            )

    def test_references_type_when_present_in_brca1_fixture(
        self, eutils_client: EutilsClient
    ) -> None:
        """When _extract_metadata returns references for the BRCA1 fixture, they
        must be list[str] not a string.  The BRCA1 fixture HTML uses a DOM
        layout the heuristic does not currently recognize, so references may be
        absent -- that is a pre-existing limitation, not a bug to fix here.
        What matters is that IF references is set, its type is list."""
        html = FIXTURE_PATH.read_text(encoding="utf-8")
        soup = BeautifulSoup(html, "html.parser")
        content_div = eutils_client._find_main_content(soup)
        assert isinstance(content_div, Tag), "BRCA1 fixture must yield a content_div"
        metadata = eutils_client._extract_metadata(soup, content_div)
        if "references" in metadata:
            refs = metadata["references"]
            assert isinstance(refs, list), (
                f"references from fixture must be list[str], got {type(refs).__name__!r}"
            )
            for item in refs:
                assert isinstance(item, str)
