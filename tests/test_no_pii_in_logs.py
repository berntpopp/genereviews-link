"""Guard: the search route must not log the raw free-text search term.

Security remediation (Theme A, D3). ``gene_symbol`` on ``GET /search/{gene_symbol}``
is caller-supplied free text and must never be bound into the structured logger.
We keep the correlation id, tool/operation, status, and timings — never the query
term itself. This test drives the route with a high-entropy sentinel and asserts
the sentinel appears in no emitted log record. Research use only; not clinical
decision support."""

from __future__ import annotations

from typing import Any

import pytest
import pytest_asyncio
import structlog
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from genereview_link.api.client_manager import get_managed_client
from genereview_link.config import ServerConfig
from genereview_link.server_manager import UnifiedServerManager

SENTINEL = "SENTINELPII7F3AGENE"


class _EmptyClient:
    """Minimal EutilsClient stand-in that returns an empty live-search result."""

    async def search_genereviews(self, gene_symbol: str, retmax: int = 20) -> dict[str, Any]:
        return {
            "count": 0,
            "retmax": retmax,
            "retstart": 0,
            "ids": [],
            "webenv": "",
            "querykey": "",
        }


@pytest_asyncio.fixture
async def http_client() -> Any:
    config = ServerConfig(transport="http", log_level="INFO", enable_docs=False)
    app: FastAPI = UnifiedServerManager().create_fastapi_app(config)

    async def _get_client() -> Any:
        yield _EmptyClient()

    app.dependency_overrides[get_managed_client] = _get_client
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest.mark.asyncio
async def test_search_route_does_not_log_raw_gene_symbol(http_client: AsyncClient) -> None:
    with structlog.testing.capture_logs() as logs:
        resp = await http_client.get(f"/search/{SENTINEL}")

    assert resp.status_code == 200
    # The route emits at least the "Starting GeneReview search" INFO record, so
    # capture must be non-empty — otherwise the guard would pass vacuously.
    assert logs, "expected the search route to emit at least one structured log record"
    for entry in logs:
        for value in entry.values():
            assert SENTINEL not in str(value), (
                f"raw free-text search term leaked into a log record: {entry!r}"
            )
