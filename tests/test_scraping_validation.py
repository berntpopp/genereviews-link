"""
Validation tests for scraping accuracy and data integrity.

These tests ensure that the enhanced scraping system produces accurate,
consistent, and complete results across different GeneReview documents.
"""

import re
import warnings
from pathlib import Path

import pytest
from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning

from genereview_link.api.eutils_client import EutilsClient

# Suppress XML parsing warnings for test fixtures
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


class TestDataIntegrity:
    """Test data integrity and accuracy of extracted content."""

    def test_brca1_content_accuracy(self, client):
        """Test accuracy of BRCA1 content extraction."""
        html_content = load_fixture("NBK1247_BRCA1.html")
        soup = BeautifulSoup(html_content, "lxml")

        content_div = client._find_main_content(soup)
        sections = client._extract_hierarchical_sections(content_div)

        # Verify key BRCA1-specific content is captured
        all_content = " ".join(section.get("content", "") for section in sections.values()).lower()

        # Should contain BRCA-specific medical terms
        brca_terms = [
            "hereditary breast",
            "ovarian cancer",
            "brca1",
            "brca2",
            "mutation",
            "pathogenic",
            "surveillance",
            "prophylactic",
        ]

        found_terms = [term for term in brca_terms if term in all_content]
        assert len(found_terms) >= 6, f"Should find BRCA-specific terms, found: {found_terms}"

        # Should not contain irrelevant content
        irrelevant_terms = ["javascript", "advertisement", "cookie policy"]
        for term in irrelevant_terms:
            assert term not in all_content, f"Should not contain irrelevant content: {term}"

    def test_li_fraumeni_content_accuracy(self, client):
        """Test accuracy of Li-Fraumeni syndrome content extraction."""
        html_content = load_fixture("NBK1311_Huntington.html")
        soup = BeautifulSoup(html_content, "lxml")

        content_div = client._find_main_content(soup)
        sections = client._extract_hierarchical_sections(content_div)

        # Verify Li-Fraumeni specific content
        all_content = " ".join(section.get("content", "") for section in sections.values()).lower()

        # Should contain Li-Fraumeni specific terms
        lfs_terms = [
            "li-fraumeni",
            "tp53",
            "cancer predisposition",
            "syndrome",
            "adrenocortical",
            "sarcoma",
            "early-onset",
        ]

        found_terms = [term for term in lfs_terms if term in all_content]
        assert len(found_terms) >= 4, (
            f"Should find Li-Fraumeni specific terms, found: {found_terms}"
        )


class TestStructuralConsistency:
    """Test consistency of extracted data structures."""

    def test_section_hierarchy_consistency(self, client):
        """Test that section hierarchy is consistent across documents."""
        fixtures = ["NBK1247_BRCA1.html", "NBK1311_Huntington.html"]

        for fixture in fixtures:
            html_content = load_fixture(fixture)
            soup = BeautifulSoup(html_content, "lxml")

            content_div = client._find_main_content(soup)
            sections = client._extract_hierarchical_sections(content_div)

            # All sections should have consistent structure
            for section_key, section_data in sections.items():
                assert isinstance(section_data, dict), (
                    f"Section {section_key} should be a dictionary"
                )

                # Required fields
                required_fields = ["title", "content", "level", "subsections"]
                for field in required_fields:
                    assert field in section_data, f"Section {section_key} missing field: {field}"

                # Field types
                assert isinstance(section_data["title"], str), (
                    f"Section {section_key} title should be string"
                )
                assert isinstance(section_data["content"], str), (
                    f"Section {section_key} content should be string"
                )
                assert isinstance(section_data["level"], int), (
                    f"Section {section_key} level should be integer"
                )
                assert isinstance(section_data["subsections"], dict), (
                    f"Section {section_key} subsections should be dictionary"
                )

                # Level should be reasonable (2-6 for HTML headings)
                assert 1 <= section_data["level"] <= 6, (
                    f"Section {section_key} level should be 1-6: {section_data['level']}"
                )

                # Validate subsections recursively
                for sub_key, sub_data in section_data["subsections"].items():
                    assert isinstance(sub_data, dict), (
                        f"Subsection {sub_key} should be a dictionary"
                    )
                    assert "title" in sub_data, f"Subsection {sub_key} missing title"
                    assert "content" in sub_data, f"Subsection {sub_key} missing content"

    def test_content_formatting_consistency(self, client):
        """Test that content formatting is consistent."""
        fixtures = ["NBK1247_BRCA1.html", "NBK1311_Huntington.html"]

        for fixture in fixtures:
            html_content = load_fixture(fixture)
            soup = BeautifulSoup(html_content, "lxml")

            content_div = client._find_main_content(soup)
            sections = client._extract_hierarchical_sections(content_div)

            for section_key, section_data in sections.items():
                content = section_data["content"]

                # Should not have excessive whitespace
                assert not re.search(r"\s{3,}", content), (
                    f"Section {section_key} has excessive whitespace"
                )

                # Should not start or end with whitespace
                assert content == content.strip(), (
                    f"Section {section_key} has leading/trailing whitespace"
                )

                # Should not have HTML tags
                assert "<" not in content or not re.search(r"<[^>]+>", content), (
                    f"Section {section_key} contains HTML tags"
                )

                # Should not have control characters
                assert not re.search(r"[\x00-\x08\x0B-\x0C\x0E-\x1F\x7F]", content), (
                    f"Section {section_key} contains control characters"
                )


class TestContentCompleteness:
    """Test completeness of extracted content."""

    def test_section_coverage(self, client):
        """Test that all major sections are extracted."""
        html_content = load_fixture("NBK1247_BRCA1.html")
        soup = BeautifulSoup(html_content, "lxml")

        # Count sections in original HTML
        content_div = client._find_main_content(soup)
        original_h2_count = len(content_div.find_all("h2"))

        # Extract sections
        sections = client._extract_hierarchical_sections(content_div)
        extracted_count = len(sections)

        # Should extract most sections (allow for some navigation/footer sections)
        coverage_ratio = extracted_count / original_h2_count if original_h2_count > 0 else 0
        assert coverage_ratio >= 0.7, (
            f"Should extract most sections: {extracted_count}/{original_h2_count} = {coverage_ratio:.2f}"
        )

    def test_content_volume_validation(self, client):
        """Test that extracted content represents substantial portion of original."""
        html_content = load_fixture("NBK1247_BRCA1.html")
        soup = BeautifulSoup(html_content, "lxml")

        # Get total text from main content area
        content_div = client._find_main_content(soup)
        original_text = content_div.get_text()
        original_length = len(original_text)

        # Get extracted content
        sections = client._extract_hierarchical_sections(content_div)
        extracted_text = " ".join(section.get("content", "") for section in sections.values())
        extracted_length = len(extracted_text)

        # Should extract significant portion of content
        extraction_ratio = extracted_length / original_length if original_length > 0 else 0
        assert extraction_ratio >= 0.4, (
            f"Should extract substantial content: {extracted_length}/{original_length} = {extraction_ratio:.2f}"
        )

    def test_important_sections_present(self, client):
        """Test that important GeneReview sections are present."""
        fixtures_and_expected = [
            ("NBK1247_BRCA1.html", ["summary", "diagnosis", "management"]),
            (
                "NBK1311_Huntington.html",
                ["summary", "diagnosis", "management"],
            ),
        ]

        for fixture, expected_sections in fixtures_and_expected:
            html_content = load_fixture(fixture)
            soup = BeautifulSoup(html_content, "lxml")

            content_div = client._find_main_content(soup)
            sections = client._extract_hierarchical_sections(content_div)

            section_keys = [key.lower() for key in sections]

            found_expected = 0
            for expected in expected_sections:
                if any(expected in key for key in section_keys):
                    found_expected += 1

            assert found_expected >= 2, (
                f"Should find key sections in {fixture}: expected {expected_sections}, found {section_keys}"
            )


class TestEdgeCases:
    """Test handling of edge cases and unusual content."""

    def test_nested_content_handling(self, client):
        """Test handling of deeply nested content structures."""
        html_content = load_fixture("NBK1247_BRCA1.html")
        soup = BeautifulSoup(html_content, "lxml")

        content_div = client._find_main_content(soup)
        sections = client._extract_hierarchical_sections(content_div)

        # Should handle nested subsections
        has_subsections = any(
            len(section.get("subsections", {})) > 0 for section in sections.values()
        )

        if has_subsections:
            # Validate subsection structure
            for _section_key, section_data in sections.items():
                subsections = section_data.get("subsections", {})
                for sub_key, sub_data in subsections.items():
                    assert "title" in sub_data, f"Subsection {sub_key} missing title"
                    assert "content" in sub_data, f"Subsection {sub_key} missing content"
                    assert len(sub_data["content"]) > 0, f"Subsection {sub_key} should have content"

    def test_special_characters_handling(self, client):
        """Test handling of special characters and unicode."""
        html_content = load_fixture("NBK1247_BRCA1.html")
        soup = BeautifulSoup(html_content, "lxml")

        content_div = client._find_main_content(soup)
        sections = client._extract_hierarchical_sections(content_div)

        # Check all content for proper character handling
        for section_key, section_data in sections.items():
            content = section_data["content"]

            # Should handle unicode properly
            try:
                content.encode("utf-8")
            except UnicodeEncodeError:
                pytest.fail(f"Section {section_key} has invalid unicode")

            # Should not have HTML entities (should be decoded)
            common_entities = ["&amp;", "&lt;", "&gt;", "&quot;", "&#"]
            for entity in common_entities:
                assert entity not in content, (
                    f"Section {section_key} contains unescaped HTML entity: {entity}"
                )

    def test_empty_section_filtering(self, client):
        """Test that empty or near-empty sections are filtered out."""
        html_content = load_fixture("NBK1247_BRCA1.html")
        soup = BeautifulSoup(html_content, "lxml")

        content_div = client._find_main_content(soup)
        sections = client._extract_hierarchical_sections(content_div)

        # All extracted sections should have substantial content
        for section_key, section_data in sections.items():
            content = section_data["content"].strip()

            # Should not be empty
            assert len(content) > 0, f"Section {section_key} should not be empty"

            # Should have more than just punctuation or whitespace
            meaningful_chars = re.sub(r"[\s\.,;:!?\-\(\)\[\]]+", "", content)
            assert len(meaningful_chars) > 10, (
                f"Section {section_key} should have meaningful content: '{content[:100]}...'"
            )


class TestRegressionPrevention:
    """Test to prevent regression of known issues."""

    def test_navigation_menu_exclusion(self, client):
        """Test that navigation menus are not included in content."""
        html_content = load_fixture("NBK1247_BRCA1.html")
        soup = BeautifulSoup(html_content, "lxml")

        content_div = client._find_main_content(soup)
        sections = client._extract_hierarchical_sections(content_div)

        # Check that navigation elements are excluded
        navigation_keywords = [
            "skip to main",
            "bookshelf id",
            "ncbi menu",
            "search database",
        ]

        all_content = " ".join(section.get("content", "") for section in sections.values()).lower()

        for keyword in navigation_keywords:
            assert keyword not in all_content, f"Navigation content should be excluded: '{keyword}'"

    def test_footer_content_exclusion(self, client):
        """Test that footer content is not included."""
        html_content = load_fixture("NBK1247_BRCA1.html")
        soup = BeautifulSoup(html_content, "lxml")

        content_div = client._find_main_content(soup)
        sections = client._extract_hierarchical_sections(content_div)

        # Check that footer elements are excluded
        footer_keywords = [
            "copyright",
            "contact ncbi",
            "privacy policy",
            "disclaimer",
        ]

        all_content = " ".join(section.get("content", "") for section in sections.values()).lower()

        footer_found = [keyword for keyword in footer_keywords if keyword in all_content]

        # Allow some copyright notices in medical content, but not extensive footer text
        assert len(footer_found) <= 1, f"Footer content should be minimal: found {footer_found}"

    def test_duplicate_content_prevention(self, client):
        """Test that content is not duplicated across sections."""
        html_content = load_fixture("NBK1247_BRCA1.html")
        soup = BeautifulSoup(html_content, "lxml")

        content_div = client._find_main_content(soup)
        sections = client._extract_hierarchical_sections(content_div)

        # Collect content for comparison
        section_contents = []
        for section_key, section_data in sections.items():
            content = section_data["content"]
            if len(content) > 200:  # Only check substantial content
                section_contents.append((section_key, content))

        # Check for significant overlap between sections
        for i, (key1, content1) in enumerate(section_contents):
            for _j, (key2, content2) in enumerate(section_contents[i + 1 :], i + 1):
                # Simple overlap detection using word sets
                words1 = set(content1.lower().split())
                words2 = set(content2.lower().split())

                overlap = len(words1 & words2)
                total_unique = len(words1 | words2)
                overlap_ratio = overlap / total_unique if total_unique > 0 else 0

                # Allow some overlap for common medical terms, but not excessive duplication
                assert overlap_ratio < 0.8, (
                    f"Sections {key1} and {key2} have too much overlap: {overlap_ratio:.2f}"
                )
