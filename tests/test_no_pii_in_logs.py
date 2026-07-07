"""Guard: caller-supplied free text must never leak into logs or metrics.

Security remediation (Theme A, D3). ``gene_symbol`` on ``GET /search/{gene_symbol}``
is caller-supplied free text (potentially GDPR Art. 9 patient-derived) and must
never surface in a structured log record, an exception detail we log, an upstream
E-utils error log, or a Prometheus metric label exported at ``/metrics``. We keep
the correlation id, tool/operation, status, endpoint, and timings — never the
query term itself.

Each test drives a code path with a high-entropy sentinel and asserts the sentinel
appears in no emitted log record (or in the ``/metrics`` exposition). Research use
only; not clinical decision support."""

from __future__ import annotations

from typing import Any

import pytest
import pytest_asyncio
import respx
import structlog
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, Response

from genereview_link.api.client_manager import get_managed_client
from genereview_link.config import ServerConfig
from genereview_link.server_manager import UnifiedServerManager

SENTINEL = "SENTINELPII7F3AGENE"


def _assert_no_sentinel(logs: list[dict[str, Any]]) -> None:
    """Fail if the sentinel free-text term appears in any captured log record."""
    # Non-empty capture guards against a vacuously passing assertion.
    assert logs, "expected at least one structured log record to be captured"
    for entry in logs:
        for value in entry.values():
            assert SENTINEL not in str(value), (
                f"raw free-text search term leaked into a log record: {entry!r}"
            )


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


class _FailingClient:
    """EutilsClient stand-in whose failure message echoes the caller input.

    This reproduces the realistic leak: an upstream error whose ``str(exc)``
    embeds the free-text query term. The route must not log that detail (nor a
    traceback rendering it), and the ``PerformanceLogger`` wrapper must not log
    ``str(exc_val)`` of the raised ``StructuredHTTPException`` (whose
    ``next_commands`` echo the gene symbol).
    """

    async def search_genereviews(self, gene_symbol: str, retmax: int = 20) -> dict[str, Any]:
        raise RuntimeError(f"upstream failure while processing {gene_symbol}")


def _build_app(client: Any) -> FastAPI:
    config = ServerConfig(transport="http", log_level="INFO", enable_docs=False)
    app: FastAPI = UnifiedServerManager().create_fastapi_app(config)

    async def _get_client() -> Any:
        yield client

    app.dependency_overrides[get_managed_client] = _get_client
    return app


@pytest_asyncio.fixture
async def http_client() -> Any:
    app = _build_app(_EmptyClient())
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
    _assert_no_sentinel(logs)


@pytest.mark.asyncio
async def test_search_failure_path_does_not_log_raw_gene_symbol() -> None:
    """The 500 failure path (route error log + PerformanceLogger) must be clean."""
    app = _build_app(_FailingClient())
    transport = ASGITransport(app=app)
    with structlog.testing.capture_logs() as logs:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(f"/search/{SENTINEL}")

    assert resp.status_code == 500
    _assert_no_sentinel(logs)


@pytest.mark.asyncio
@respx.mock
async def test_eutils_http_error_does_not_log_query_term() -> None:
    """An E-utils HTTP error must not log the request URL (carries the term)."""
    from genereview_link.api.eutils_client import EutilsClient

    respx.get(url__startswith="https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi").mock(
        return_value=Response(400, json={"error": "bad request"})
    )

    client = EutilsClient()
    client.rate_limit_delay = 0.0  # keep the test fast; no real network
    try:
        with (
            structlog.testing.capture_logs() as logs,
            pytest.raises(Exception),  # noqa: B017 - re-raised HTTPStatusError
        ):
            await client.search_genereviews(SENTINEL)
    finally:
        await client.close()

    _assert_no_sentinel(logs)


@pytest.mark.asyncio
async def test_metrics_endpoint_does_not_expose_raw_gene_symbol() -> None:
    """Prometheus /metrics must label paths with the route template, not the term."""
    app = _build_app(_EmptyClient())
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        search_resp = await client.get(f"/search/{SENTINEL}")
        metrics_resp = await client.get("/metrics")

    assert search_resp.status_code == 200
    assert metrics_resp.status_code == 200
    body = metrics_resp.text
    assert SENTINEL not in body, "raw free-text search term leaked into a metrics label"
    # Positive assertion: the dynamic segment is redacted to its template token,
    # which keeps label cardinality bounded as well.
    assert "/search/{gene_symbol}" in body
