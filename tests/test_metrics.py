"""Verify Prometheus /metrics endpoint exposes basic counters."""

from fastapi.testclient import TestClient


def test_metrics_endpoint_returns_prometheus_format() -> None:
    """/metrics returns text/plain with Prometheus exposition format."""
    from server import app

    with TestClient(app) as client:
        # Trigger at least one request to populate counters
        client.get("/health")
        response = client.get("/metrics")
        assert response.status_code == 200
        assert "text/plain" in response.headers["content-type"]
        body = response.text
        assert "genereview_requests_total" in body
        assert "genereview_request_duration_seconds" in body
