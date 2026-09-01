"""Unit test: /health must return {status, version, transport} per MCP Transport Standard v1."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from genereview_link.config import ServerConfig, settings
from genereview_link.server_manager import UnifiedServerManager


def _app() -> FastAPI:
    config = ServerConfig(transport="http", log_level="WARNING")
    return UnifiedServerManager().create_fastapi_app(config)


@pytest.fixture
def sync_client() -> TestClient:
    """Synchronous test client (no DB needed — just health endpoint)."""
    return TestClient(_app(), raise_server_exceptions=True)


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


def test_health_reports_no_corpus_facts_when_lifespan_never_ran(
    sync_client: TestClient,
) -> None:
    """No lifespan run (this fixture) must not be misreported as a stale corpus (#145)."""
    data = sync_client.get("/health").json()
    assert data["corpus"] == {
        "version": None,
        "data_as_of": None,
        "age_days": None,
        "max_age_days": settings.CORPUS_MAX_AGE_DAYS,
        "stale": False,
    }
    assert data["status"] == "healthy"


def test_health_exposes_data_as_of_for_a_fresh_restored_corpus() -> None:
    """A corpus restored from a fixture manifest well within CORPUS_MAX_AGE_DAYS stays
    healthy and reports its identity and data_as_of, exactly as server_lifecycle wires it
    from `genereview_corpus_version.ingest_finished_at` (see corpus/freshness.py)."""
    fixture_ingest_finished_at = datetime.now(UTC) - timedelta(days=5)
    app = _app()
    app.state.corpus_version = "2026-08-28-r1"
    app.state.corpus_data_as_of = fixture_ingest_finished_at.isoformat()

    data = TestClient(app, raise_server_exceptions=True).get("/health").json()

    assert data["status"] == "healthy"
    assert data["corpus"]["version"] == "2026-08-28-r1"
    assert data["corpus"]["data_as_of"] == fixture_ingest_finished_at.isoformat()
    assert data["corpus"]["age_days"] == 5
    assert data["corpus"]["stale"] is False


def test_health_goes_degraded_for_a_corpus_frozen_past_max_age() -> None:
    """The #145 scenario: a corpus that stopped refreshing must eventually surface as
    `degraded` from data_as_of alone, with no scheduler or comparison required."""
    frozen_since = datetime.now(UTC) - timedelta(days=settings.CORPUS_MAX_AGE_DAYS + 1)
    app = _app()
    app.state.corpus_version = "2026-05-12-r1"
    app.state.corpus_data_as_of = frozen_since.isoformat()

    data = TestClient(app, raise_server_exceptions=True).get("/health").json()

    assert data["corpus"]["stale"] is True
    assert data["corpus"]["age_days"] == settings.CORPUS_MAX_AGE_DAYS + 1
    assert data["status"] == "degraded"
