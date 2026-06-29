"""Unit test: /health must return {status, version, transport} per MCP Transport Standard v1."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from genereview_link.config import ServerConfig
from genereview_link.server_manager import UnifiedServerManager


@pytest.fixture
def sync_client() -> TestClient:
    """Synchronous test client (no DB needed — just health endpoint)."""
    config = ServerConfig(transport="http", log_level="WARNING")
    mgr = UnifiedServerManager()
    app = mgr.create_fastapi_app(config)
    return TestClient(app, raise_server_exceptions=True)


def test_health_returns_status_version_transport(sync_client: TestClient) -> None:
    """GET /health must include status, version, and transport."""
    response = sync_client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy", f"expected status=healthy, got {data}"
    assert "version" in data, f"missing 'version' key in /health response: {data}"
    assert data["transport"] == "streamable-http-stateless", (
        f"expected transport=streamable-http-stateless, got {data.get('transport')}"
    )
