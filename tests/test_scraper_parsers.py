"""
Unit tests for scraper parsing logic using fixture data.

These tests validate the parsing methods independently of network requests,
using saved HTML fixtures from actual GeneReviews pages.
"""

import warnings
from pathlib import Path

import pytest
from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning

from genereview_link.api.eutils_client import EutilsClient

# Suppress XML parsing warnings for fixtures that are HTML/XML hybrids
warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)


@pytest.fixture
def client():
    """Provide a fresh EutilsClient instance for testing."""
    return EutilsClient()


def load_fixture(name: str) -> str:
    """Load HTML fixture content from the fixtures directory."""
    fixture_path = Path(__file__).parent / "fixtures" / name
    if not fixture_path.exists():
        pytest.skip(f"Fixture {name} not found")
    return fixture_path.read_text(encoding="utf-8")


class TestMainContentExtraction:
    """Test the _find_main_content method with various fixtures."""

    def test_find_main_content_brca1(self, client):
        """Test main content extraction from BRCA1 fixture."""
        html_content = load_fixture("NBK1247_BRCA1.html")
        soup = BeautifulSoup(html_content, "lxml")

        content_div = client._find_main_content(soup)

        assert content_div is not None, "Should find main content div"
        assert content_div.name == "div", "Main content should be a div element"
        # Should contain multiple h2 headings for sections
        h2_headings = content_div.find_all("h2")
        assert len(h2_headings) >= 3, f"Should have multiple sections, found {len(h2_headings)}"

    def test_find_main_content_li_fraumeni(self, client):
        """Test main content extraction from Li-Fraumeni syndrome fixture."""
        html_content = load_fixture("NBK1311_Huntington.html")
        soup = BeautifulSoup(html_content, "lxml")

        content_div = client._find_main_content(soup)

        assert content_div is not None, "Should find main content div"
        # Verify it contains substantial content
        text_length = len(content_div.get_text().strip())
        assert text_length > 1000, f"Main content should be substantial, got {text_length} chars"


class TestTitleExtraction:
    """Test the _extract_title method with various fixtures."""

    def test_extract_title_brca1(self, client):
        """Test title extraction from BRCA1 fixture."""
        html_content = load_fixture("NBK1247_BRCA1.html")
        soup = BeautifulSoup(html_content, "lxml")
        content_div = client._find_main_content(soup)

        title = client._extract_title(soup, content_div)

        assert title, "Should extract a non-empty title"
        assert "BRCA" in title, f"Title should mention BRCA, got: {title}"
        assert len(title) > 10, f"Title should be substantial, got: {title}"
        # Should not contain generic terms
        assert "Bookshelf" not in title, f"Title should not contain 'Bookshelf': {title}"

    def test_extract_title_li_fraumeni(self, client):
        """Test title extraction from Li-Fraumeni syndrome fixture."""
        html_content = load_fixture("NBK1311_Huntington.html")
        soup = BeautifulSoup(html_content, "lxml")
        content_div = client._find_main_content(soup)

        title = client._extract_title(soup, content_div)

        assert title, "Should extract a non-empty title"
        assert any(term in title.lower() for term in ["li-fraumeni", "syndrome", "cancer"]), (
            f"Title should be relevant to Li-Fraumeni syndrome, got: {title}"
        )


class TestMetadataExtraction:
    """Test metadata extraction methods."""

    def test_extract_authors_brca1(self, client):
        """Test author extraction from BRCA1 fixture."""
        html_content = load_fixture("NBK1247_BRCA1.html")
        soup = BeautifulSoup(html_content, "lxml")
        content_div = client._find_main_content(soup)

        authors = client._extract_authors(content_div)

        if authors:  # Authors might not always be present
            assert isinstance(authors, str), "Authors should be a string"
            assert len(authors) > 5, f"Authors should be substantial if present: {authors}"

    def test_extract_update_info_brca1(self, client):
        """Test update info extraction from BRCA1 fixture."""
        html_content = load_fixture("NBK1247_BRCA1.html")
        soup = BeautifulSoup(html_content, "lxml")
        content_div = client._find_main_content(soup)

        update_info = client._extract_update_info(content_div)

        if update_info:  # Update info might not always be present
            assert isinstance(update_info, str), "Update info should be a string"
            assert any(term in update_info.lower() for term in ["update", "revised", "initial"]), (
                f"Update info should contain relevant terms: {update_info}"
            )


class TestHierarchicalSectionExtraction:
    """Test the hierarchical section extraction logic."""

    def test_extract_hierarchical_sections_brca1(self, client):
        """Test hierarchical section extraction from BRCA1 fixture."""
        html_content = load_fixture("NBK1247_BRCA1.html")
        soup = BeautifulSoup(html_content, "lxml")
        content_div = client._find_main_content(soup)

        sections = client._extract_hierarchical_sections(content_div)

        assert isinstance(sections, dict), "Sections should be a dictionary"
        assert len(sections) >= 3, f"Should extract multiple sections, got {len(sections)}"

        # Check that sections have expected structure
        for section_key, section_data in sections.items():
            assert isinstance(section_data, dict), f"Section {section_key} should be a dict"
            assert "title" in section_data, f"Section {section_key} should have title"
            assert "content" in section_data, f"Section {section_key} should have content"
            assert "level" in section_data, f"Section {section_key} should have level"
            assert "subsections" in section_data, f"Section {section_key} should have subsections"

            # Content should be substantial
            assert len(section_data["content"]) > 50, (
                f"Section {section_key} content should be substantial"
            )

    def test_extract_hierarchical_sections_li_fraumeni(self, client):
        """Test hierarchical section extraction from Li-Fraumeni syndrome fixture."""
        html_content = load_fixture("NBK1311_Huntington.html")
        soup = BeautifulSoup(html_content, "lxml")
        content_div = client._find_main_content(soup)

        sections = client._extract_hierarchical_sections(content_div)

        assert isinstance(sections, dict), "Sections should be a dictionary"
        assert len(sections) >= 2, f"Should extract multiple sections, got {len(sections)}"

        # Look for common GeneReview sections
        section_keys = [key.lower() for key in sections]
        expected_sections = [
            "summary",
            "diagnosis",
            "management",
            "genetic_counseling",
        ]

        found_expected = sum(
            1 for expected in expected_sections if any(expected in key for key in section_keys)
        )
        assert found_expected >= 2, (
            f"Should find at least 2 common sections, found {found_expected} in {section_keys}"
        )


class TestContentQuality:
    """Test the quality and completeness of extracted content."""

    def test_content_completeness_brca1(self, client):
        """Test that extraction captures substantial content from BRCA1."""
        html_content = load_fixture("NBK1247_BRCA1.html")
        soup = BeautifulSoup(html_content, "lxml")
        content_div = client._find_main_content(soup)

        # Extract all components
        title = client._extract_title(soup, content_div)
        sections = client._extract_hierarchical_sections(content_div)
        metadata = client._extract_metadata(soup, content_div)

        # Calculate total content length
        total_content_length = len(title) if title else 0
        for section_data in sections.values():
            total_content_length += len(section_data.get("content", ""))

        # Should capture substantial content (at least 5KB of text)
        assert total_content_length > 5000, (
            f"Total content should be substantial, got {total_content_length} chars"
        )

        # Should have meaningful metadata
        assert isinstance(metadata, dict), "Metadata should be a dictionary"

    def test_no_empty_sections(self, client):
        """Test that no sections are completely empty."""
        html_content = load_fixture("NBK1247_BRCA1.html")
        soup = BeautifulSoup(html_content, "lxml")
        content_div = client._find_main_content(soup)

        sections = client._extract_hierarchical_sections(content_div)

        for section_key, section_data in sections.items():
            content = section_data.get("content", "").strip()
            assert len(content) > 10, (
                f"Section '{section_key}' should not be empty or trivial: '{content[:100]}...'"
            )

    def test_section_titles_meaningful(self, client):
        """Test that section titles are meaningful and not generic."""
        html_content = load_fixture("NBK1247_BRCA1.html")
        soup = BeautifulSoup(html_content, "lxml")
        content_div = client._find_main_content(soup)

        sections = client._extract_hierarchical_sections(content_div)

        generic_titles = {"menu", "navigation", "skip", "search", "bookshelf"}

        for _section_key, section_data in sections.items():
            title = section_data.get("title", "").lower()
            assert not any(generic in title for generic in generic_titles), (
                f"Section title should not be generic: '{section_data.get('title')}'"
            )
            assert len(title) > 2, (
                f"Section title should be meaningful: '{section_data.get('title')}'"
            )


class TestErrorHandling:
    """Test error handling in parsing methods."""

    def test_malformed_html_handling(self, client):
        """Test that parser handles malformed HTML gracefully."""
        malformed_html = "<html><body><h1>Test</h1><p>Unclosed paragraph</body></html>"
        soup = BeautifulSoup(malformed_html, "lxml")

        # Should not raise exceptions
        content_div = client._find_main_content(soup)
        title = client._extract_title(soup, content_div)
        sections = client._extract_hierarchical_sections(content_div)

        # Should return reasonable defaults
        assert isinstance(sections, dict)
        assert isinstance(title, str)

    def test_empty_content_handling(self, client):
        """Test handling of empty or minimal content."""
        minimal_html = "<html><body><div id='NBK123'></div></body></html>"
        soup = BeautifulSoup(minimal_html, "lxml")

        content_div = client._find_main_content(soup)
        sections = client._extract_hierarchical_sections(content_div)

        assert isinstance(sections, dict)
        # Empty content should result in empty sections dict
        assert len(sections) == 0


class TestRegressionValidation:
    """Test for common regression issues in parsing."""

    def test_duplicate_content_prevention(self, client):
        """Test that content isn't duplicated across sections."""
        html_content = load_fixture("NBK1247_BRCA1.html")
        soup = BeautifulSoup(html_content, "lxml")
        content_div = client._find_main_content(soup)

        sections = client._extract_hierarchical_sections(content_div)

        # Collect all content snippets
        all_content = []
        for section_data in sections.values():
            content = section_data.get("content", "")
            if len(content) > 100:  # Only check substantial content
                all_content.append(content[:200])  # First 200 chars for comparison

        # Check for significant overlap (allowing some overlap for headers/footers)
        for i, content1 in enumerate(all_content):
            for j, content2 in enumerate(all_content[i + 1 :], i + 1):
                # Calculate simple overlap ratio
                overlap = len(set(content1.split()) & set(content2.split()))
                total_words = len(set(content1.split()) | set(content2.split()))
                overlap_ratio = overlap / total_words if total_words > 0 else 0

                assert overlap_ratio < 0.9, (
                    f"Sections {i} and {j} have too much overlap ({overlap_ratio:.2f})"
                )
