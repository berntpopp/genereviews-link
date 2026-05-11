"""Verify that asgi-correlation-id propagates request IDs to response headers."""

from fastapi.testclient import TestClient


def test_correlation_id_in_response_header() -> None:
    """A request without X-Request-ID gets one assigned in the response."""
    from server import app

    with TestClient(app) as client:
        response = client.get("/health")
        assert response.status_code == 200
        assert "X-Request-ID" in response.headers
        # Should be a UUID-shaped string
        assert len(response.headers["X-Request-ID"]) >= 16


def test_correlation_id_echoed_from_request() -> None:
    """A request with X-Request-ID gets the same value back in the response."""
    from server import app

    incoming = "550e8400-e29b-41d4-a716-446655440000"
    with TestClient(app) as client:
        response = client.get("/health", headers={"X-Request-ID": incoming})
        assert response.status_code == 200
        assert response.headers["X-Request-ID"] == incoming
