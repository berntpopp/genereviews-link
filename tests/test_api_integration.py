"""
API integration tests for the GeneReview Link Server.

These tests validate the REST API endpoints and ensure they work correctly
with the enhanced scraping system.
"""

import pytest
from fastapi.testclient import TestClient

from server import app  # Import the FastAPI app


@pytest.fixture
def client():
    """Provide a test client for the FastAPI app."""
    return TestClient(app)


class TestAPIEndpoints:
    """Test all API endpoints with various inputs."""

    def test_health_check(self, client):
        """Test basic health check endpoint."""
        response = client.get("/")
        assert response.status_code == 200

    def test_search_endpoint(self, client):
        """Test the search endpoint."""
        response = client.get("/search/BRCA1")
        assert response.status_code == 200

        data = response.json()
        assert isinstance(data, dict)
        assert "count" in data
        assert "ids" in data
        assert isinstance(data["ids"], list)

    def test_abstract_endpoint(self, client):
        """Test the abstract endpoint with a known PubMed ID."""
        # Use a known BRCA1 GeneReview PubMed ID
        pubmed_id = "20301425"  # BRCA1 GeneReview

        response = client.get(f"/abstract/{pubmed_id}")
        assert response.status_code == 200

        data = response.json()
        assert isinstance(data, dict)
        assert "pmid" in data
        assert "title" in data
        assert data["pmid"] == pubmed_id

    def test_links_endpoint(self, client):
        """Test the links endpoint."""
        pubmed_id = "20301425"  # BRCA1 GeneReview

        response = client.get(f"/links/{pubmed_id}")
        assert response.status_code == 200

        data = response.json()
        assert isinstance(data, dict)
        assert "urls" in data
        assert isinstance(data["urls"], list)

    def test_fulltext_endpoint(self, client):
        """Test the fulltext endpoint with enhanced scraping."""
        nbk_id = "NBK1247"  # BRCA1 GeneReview

        response = client.get(f"/fulltext/{nbk_id}")
        assert response.status_code == 200

        data = response.json()
        assert isinstance(data, dict)
        assert "nbk_id" in data
        assert "url" in data
        assert "title" in data
        assert "sections" in data

        # Validate enhanced scraping results
        sections = data["sections"]
        assert isinstance(sections, dict)
        assert len(sections) >= 5, f"Should have multiple sections, got {len(sections)}"

        # Check section structure
        for section_key, section_data in sections.items():
            assert "title" in section_data
            assert "content" in section_data
            assert "level" in section_data
            assert "subsections" in section_data

            # Content should be substantial
            content_length = len(section_data["content"])
            assert (
                content_length > 100
            ), f"Section {section_key} should have substantial content: {content_length}"

    def test_genereview_comprehensive_endpoint(self, client):
        """Test the comprehensive GeneReview endpoint."""
        gene_symbol = "BRCA1"

        response = client.get(f"/genereview/{gene_symbol}")
        assert response.status_code == 200

        data = response.json()
        assert isinstance(data, dict)
        assert "gene_symbol" in data
        assert data["gene_symbol"] == gene_symbol
        assert "pubmed_id" in data
        assert "book_url" in data
        assert "title" in data

        # Should have comprehensive data
        if "full_text_data" in data and data["full_text_data"]:
            full_text = data["full_text_data"]
            assert "sections" in full_text
            assert len(full_text["sections"]) >= 5, "Should have multiple sections"


class TestAPIErrorHandling:
    """Test API error handling and edge cases."""

    def test_invalid_gene_search(self, client):
        """Test search with invalid gene symbol."""
        response = client.get("/search/INVALIDGENE123")
        assert response.status_code == 200  # Should not error, but return empty results

        data = response.json()
        assert data["count"] == 0
        assert len(data["ids"]) == 0

    def test_invalid_pubmed_id(self, client):
        """Test abstract endpoint with invalid PubMed ID."""
        response = client.get("/abstract/99999999")
        # Should return 404 or handle gracefully
        assert response.status_code in [200, 404]

        if response.status_code == 200:
            data = response.json()
            # Should return empty or error structure
            assert isinstance(data, dict)

    def test_invalid_nbk_id(self, client):
        """Test fulltext endpoint with invalid NBK ID."""
        response = client.get("/fulltext/NBK99999")
        # Should handle gracefully
        assert response.status_code in [200, 404]

        if response.status_code == 200:
            data = response.json()
            assert isinstance(data, dict)
            # May contain error field
            if "error" in data:
                assert isinstance(data["error"], str)

    def test_empty_gene_symbol(self, client):
        """Test endpoints with empty gene symbol."""
        response = client.get("/search/")
        assert response.status_code == 404  # Should be not found

        response = client.get("/genereview/")
        assert response.status_code == 404  # Should be not found


class TestAPIPerformance:
    """Test API performance characteristics."""

    def test_response_times(self, client):
        """Test that API responses are within acceptable time limits."""
        import time

        endpoints = [
            "/search/BRCA1",
            "/abstract/20301425",
            "/links/20301425",
        ]

        for endpoint in endpoints:
            start_time = time.time()
            response = client.get(endpoint)
            end_time = time.time()

            duration = end_time - start_time

            # Most endpoints should be fast (under 5 seconds)
            assert duration < 5.0, f"Endpoint {endpoint} too slow: {duration:.2f}s"
            assert response.status_code == 200, f"Endpoint {endpoint} failed"

    def test_fulltext_performance(self, client):
        """Test fulltext endpoint performance."""
        import time

        start_time = time.time()
        response = client.get("/fulltext/NBK1247")
        end_time = time.time()

        duration = end_time - start_time

        # Fulltext scraping may take longer but should be reasonable
        assert duration < 15.0, f"Fulltext endpoint too slow: {duration:.2f}s"
        assert response.status_code == 200

        # Should return substantial data for the time taken
        data = response.json()
        if "sections" in data:
            section_count = len(data["sections"])
            assert (
                section_count >= 5
            ), f"Should return substantial data: {section_count} sections"


class TestAPIDataConsistency:
    """Test data consistency across different API endpoints."""

    def test_cross_endpoint_consistency(self, client):
        """Test that data is consistent across related endpoints."""
        gene_symbol = "BRCA1"

        # Get data from comprehensive endpoint
        comprehensive_response = client.get(f"/genereview/{gene_symbol}")
        assert comprehensive_response.status_code == 200
        comprehensive_data = comprehensive_response.json()

        # Extract PubMed ID and NBK ID
        pubmed_id = comprehensive_data.get("pubmed_id")
        book_url = comprehensive_data.get("book_url")

        if pubmed_id:
            # Test abstract endpoint
            abstract_response = client.get(f"/abstract/{pubmed_id}")
            if abstract_response.status_code == 200:
                abstract_data = abstract_response.json()

                # Titles should be related (may not be identical due to formatting)
                comp_title = comprehensive_data.get("title", "").lower()
                abs_title = abstract_data.get("title", "").lower()

                # Should share common keywords
                comp_words = set(comp_title.split())
                abs_words = set(abs_title.split())
                common_words = comp_words & abs_words

                assert (
                    len(common_words) >= 2
                ), f"Titles should share keywords: '{comp_title}' vs '{abs_title}'"

        # Extract NBK ID from book URL
        if book_url and "NBK" in book_url:
            import re

            nbk_match = re.search(r"NBK\d+", book_url)
            if nbk_match:
                nbk_id = nbk_match.group()

                # Test fulltext endpoint
                fulltext_response = client.get(f"/fulltext/{nbk_id}")
                if fulltext_response.status_code == 200:
                    fulltext_data = fulltext_response.json()

                    # Titles should match or be very similar
                    comp_title = comprehensive_data.get("title", "").lower()
                    full_title = fulltext_data.get("title", "").lower()

                    # Should be identical or very similar
                    if comp_title and full_title:
                        # Allow for minor formatting differences
                        comp_clean = re.sub(r"[^\w\s]", "", comp_title)
                        full_clean = re.sub(r"[^\w\s]", "", full_title)

                        assert (
                            comp_clean == full_clean
                            or comp_title in full_title
                            or full_title in comp_title
                        ), f"Titles should match: '{comp_title}' vs '{full_title}'"


class TestAPICaching:
    """Test API caching behavior."""

    def test_repeated_requests_consistency(self, client):
        """Test that repeated requests return consistent results."""
        endpoint = "/search/BRCA1"

        # Make multiple requests
        responses = []
        for _ in range(3):
            response = client.get(endpoint)
            assert response.status_code == 200
            responses.append(response.json())

        # Results should be identical (due to caching)
        first_response = responses[0]
        for response in responses[1:]:
            assert response == first_response, "Cached responses should be identical"

    def test_different_genes_different_results(self, client):
        """Test that different genes return different results."""
        genes = ["BRCA1", "BRCA2", "TP53"]
        results = {}

        for gene in genes:
            response = client.get(f"/search/{gene}")
            if response.status_code == 200:
                results[gene] = response.json()

        # Should have different results for different genes
        gene_pairs = [(g1, g2) for g1 in results for g2 in results if g1 < g2]

        for gene1, gene2 in gene_pairs:
            result1 = results[gene1]
            result2 = results[gene2]

            # Results should be different (at least in IDs)
            assert (
                result1["ids"] != result2["ids"]
            ), f"Different genes should have different results: {gene1} vs {gene2}"


class TestAPIDocumentation:
    """Test API documentation and OpenAPI schema."""

    def test_docs_endpoint(self, client):
        """Test that API documentation is accessible."""
        response = client.get("/docs")
        assert response.status_code == 200
        assert "text/html" in response.headers.get("content-type", "")

    def test_openapi_schema(self, client):
        """Test that OpenAPI schema is valid."""
        response = client.get("/openapi.json")
        assert response.status_code == 200

        schema = response.json()
        assert "openapi" in schema
        assert "info" in schema
        assert "paths" in schema

        # Should have all expected endpoints
        expected_paths = [
            "/search/{gene_symbol}",
            "/abstract/{pubmed_id}",
            "/links/{pubmed_id}",
            "/fulltext/{nbk_id}",
            "/genereview/{gene_symbol}",
        ]

        for path in expected_paths:
            assert path in schema["paths"], f"Missing endpoint in schema: {path}"
