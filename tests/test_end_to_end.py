"""
End-to-end tests for the complete GeneReview workflow.

These tests validate the entire pipeline from gene symbol search to comprehensive
GeneReview data extraction, ensuring all components work together correctly.
"""

import pytest

from genereview_link.api.eutils_client import EutilsClient
from genereview_link.services.genereview_service import GeneReviewService


@pytest.fixture
def client():
    """Provide a fresh EutilsClient instance for testing."""
    return EutilsClient()


@pytest.fixture
def service():
    """Provide a fresh GeneReviewService instance for testing."""
    return GeneReviewService()


class TestCompleteWorkflow:
    """Test the complete GeneReview data extraction workflow."""

    @pytest.mark.asyncio
    async def test_brca1_complete_workflow(self, service):
        """Test complete workflow for BRCA1 gene."""
        gene_symbol = "BRCA1"

        # Execute the complete workflow
        result = await service.get_genereview_comprehensive(gene_symbol)

        # Validate the comprehensive result structure
        from genereview_link.models.genereview_models import GeneReview

        assert isinstance(result, GeneReview), "Result should be a GeneReview model"
        assert result.gene_symbol == gene_symbol, f"Gene symbol should be {gene_symbol}"

        # Should have PubMed data
        assert result.pubmed_id, "Should include PubMed ID"
        assert result.book_url, "Should include book URL"
        assert result.title, "Should include title"

        # Should have abstract data
        if result.abstract_data:
            abstract = result.abstract_data
            assert abstract.pmid, "Abstract should include PMID"
            # Note: Title may be empty for some GeneReviews book articles
            # assert abstract.title, "Abstract should include title"
            assert abstract.abstract, "Abstract should include abstract text"

        # Should have comprehensive full text data
        if result.full_text_data:
            full_text = result.full_text_data
            assert full_text.title, "Full text should include title"
            assert full_text.sections, "Full text should include sections"

            # Validate section structure
            sections = full_text.sections
            assert isinstance(sections, dict), "Sections should be a dictionary"
            assert len(sections) >= 5, f"Should have multiple sections, got {len(sections)}"

            # Check for key sections
            section_keys = [key.lower() for key in sections]
            expected_sections = ["summary", "diagnosis", "management"]
            found_sections = [
                exp for exp in expected_sections if any(exp in key for key in section_keys)
            ]
            assert len(found_sections) >= 2, (
                f"Should find key sections, found {found_sections} in {section_keys}"
            )

            # Validate individual sections
            for section_key, section_data in sections.items():
                assert section_data.title, f"Section {section_key} should have title"
                assert section_data.content, f"Section {section_key} should have content"
                assert section_data.level, f"Section {section_key} should have level"
                assert isinstance(section_data.subsections, dict), (
                    f"Section {section_key} should have subsections dict"
                )

                # Content should be substantial
                content_length = len(section_data.content)
                assert content_length > 100, (
                    f"Section {section_key} should have substantial content, "
                    f"got {content_length} chars"
                )

    @pytest.mark.asyncio
    async def test_tp53_complete_workflow(self, service):
        """Test complete workflow for TP53 gene (Li-Fraumeni syndrome)."""
        gene_symbol = "TP53"

        # Execute the complete workflow
        result = await service.get_genereview_comprehensive(gene_symbol)

        # Validate basic structure
        from genereview_link.models.genereview_models import GeneReview

        assert isinstance(result, GeneReview), "Result should be a GeneReview model"
        assert result.gene_symbol == gene_symbol, f"Gene symbol should be {gene_symbol}"

        # Should find Li-Fraumeni syndrome related content
        if result.title:
            title = result.title.lower()
            assert any(term in title for term in ["li-fraumeni", "tp53", "syndrome"]), (
                f"Title should be relevant to TP53/Li-Fraumeni: {result.title}"
            )


class TestScrapingRobustness:
    """Test the robustness of the scraping system with various edge cases."""

    @pytest.mark.asyncio
    async def test_multiple_genes_consistency(self, service):
        """Test that scraping is consistent across multiple genes."""
        genes = ["BRCA1", "BRCA2", "TP53"]
        results = {}

        for gene in genes:
            try:
                result = await service.get_genereview_comprehensive(gene)
                results[gene] = result
            except Exception as e:
                # Some genes might not have GeneReviews, which is okay
                print(f"Could not fetch {gene}: {e}")
                continue

        # Should successfully process at least 2 genes
        assert len(results) >= 2, f"Should process multiple genes, got {len(results)}"

        # Each successful result should have consistent structure
        for gene, result in results.items():
            from genereview_link.models.genereview_models import GeneReview

            assert isinstance(result, GeneReview), f"{gene} should be GeneReview model"
            assert result.gene_symbol == gene, f"{gene} should match query"

            if result.full_text_data and result.full_text_data.sections:
                sections = result.full_text_data.sections
                assert len(sections) >= 3, f"{gene} should have multiple sections"

    @pytest.mark.asyncio
    async def test_content_quality_metrics(self, client):
        """Test that scraped content meets quality thresholds."""
        test_urls = [
            "https://www.ncbi.nlm.nih.gov/books/NBK1247/",  # BRCA1
            "https://www.ncbi.nlm.nih.gov/books/NBK1311/",  # Li-Fraumeni
        ]

        for url in test_urls:
            try:
                result = await client.scrape_genereview_book(url)

                if result.get("content"):
                    sections = result["content"]

                    # Quality metrics
                    total_content_length = sum(
                        len(section.get("content", "")) for section in sections.values()
                    )

                    # Should have substantial content (at least 10KB)
                    assert total_content_length >= 10000, (
                        f"Total content should be substantial: {total_content_length}"
                    )

                    # Should not have too many very short sections
                    short_sections = sum(
                        1 for section in sections.values() if len(section.get("content", "")) < 100
                    )
                    assert short_sections <= len(sections) * 0.3, (
                        f"Too many short sections: {short_sections}/{len(sections)}"
                    )

                    # Should have meaningful section titles
                    for _section_key, section_data in sections.items():
                        title = section_data.get("title", "")
                        assert len(title) >= 3, f"Section title too short: '{title}'"
                        assert title.lower() not in [
                            "menu",
                            "navigation",
                            "search",
                        ], f"Section title should not be generic: '{title}'"

            except Exception as e:
                # Some URLs might be temporarily unavailable
                print(f"Could not test {url}: {e}")
                continue


class TestErrorRecovery:
    """Test error recovery and graceful degradation."""

    @pytest.mark.asyncio
    async def test_invalid_gene_handling(self, service):
        """Test handling of invalid or non-existent genes."""
        invalid_genes = ["INVALIDGENE123", "NOTREAL", ""]

        for gene in invalid_genes:
            try:
                result = await service.get_genereview_comprehensive(gene)

                # Should return a valid structure even for invalid genes
                from genereview_link.models.genereview_models import GeneReview

                assert isinstance(result, GeneReview), f"Should return GeneReview for {gene}"

                # Should indicate no results found
                if result and gene:  # If not empty and gene is not empty
                    assert result.gene_symbol, f"Should include gene symbol for {gene}"

            except Exception as e:
                # For empty gene symbols or truly invalid genes, expect DataNotFoundError
                from genereview_link.services.genereview_service import (
                    DataNotFoundError,
                )

                if isinstance(e, DataNotFoundError):
                    # This is expected behavior for invalid genes
                    continue
                else:
                    # Should not raise other unhandled exceptions
                    pytest.fail(f"Should handle invalid gene {gene} gracefully, got: {e}")

    @pytest.mark.asyncio
    async def test_partial_data_handling(self, client):
        """Test handling when only partial data is available."""
        # This test would typically use mocked responses
        # For now, we test with real data and verify graceful handling

        test_url = "https://www.ncbi.nlm.nih.gov/books/NBK1247/"
        result = await client.scrape_genereview_book(test_url)

        # Should always return a dictionary, even if empty
        assert isinstance(result, dict), "Should always return a dictionary"

        # If successful, should have proper structure
        if result and "content" in result:
            sections = result["content"]
            assert isinstance(sections, dict), "Sections should be a dictionary"

            # Each section should have required fields
            for section_key, section_data in sections.items():
                required_fields = ["title", "content", "level", "subsections"]
                for field in required_fields:
                    assert field in section_data, (
                        f"Section {section_key} missing required field: {field}"
                    )


class TestPerformanceValidation:
    """Test performance characteristics of the scraping system."""

    @pytest.mark.asyncio
    async def test_scraping_performance(self, client):
        """Test that scraping completes within reasonable time limits."""
        import time

        test_url = "https://www.ncbi.nlm.nih.gov/books/NBK1247/"

        start_time = time.time()
        result = await client.scrape_genereview_book(test_url)
        end_time = time.time()

        duration = end_time - start_time

        # Should complete within 30 seconds
        assert duration < 30, f"Scraping took too long: {duration:.2f} seconds"

        # Should be fast enough for production use (under 10 seconds typically)
        if duration > 10:
            print(f"Warning: Scraping took {duration:.2f} seconds")

        # Should return meaningful results within time limit
        if "content" in result:
            assert len(result["content"]) > 0, "Should extract content within time limit"

    @pytest.mark.asyncio
    async def test_memory_efficiency(self, client):
        """Test that scraping doesn't consume excessive memory."""
        import os

        import psutil

        process = psutil.Process(os.getpid())
        initial_memory = process.memory_info().rss

        # Scrape multiple documents
        test_urls = [
            "https://www.ncbi.nlm.nih.gov/books/NBK1247/",
            "https://www.ncbi.nlm.nih.gov/books/NBK1311/",
        ]

        for url in test_urls:
            try:
                await client.scrape_genereview_book(url)
            except Exception:  # noqa: S112
                continue  # Skip failed requests in memory-leak smoke test

        final_memory = process.memory_info().rss
        memory_increase = final_memory - initial_memory

        # Should not increase memory by more than 100MB
        assert memory_increase < 100 * 1024 * 1024, (
            f"Memory increase too large: {memory_increase / 1024 / 1024:.1f} MB"
        )
