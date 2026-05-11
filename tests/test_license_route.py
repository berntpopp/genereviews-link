"""Tests for the dedicated /license endpoint."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from genereview_link.api.routes import license as license_routes


@pytest.fixture
def app() -> FastAPI:
    app = FastAPI()
    app.include_router(license_routes.router)
    return app


@pytest.mark.asyncio
async def test_license_endpoint_returns_attribution(app: FastAPI) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/license")
    assert resp.status_code == 200
    body = resp.json()
    assert "copyright" in body
    assert "University of Washington" in body["copyright"]
    assert body["terms_url"].startswith("https://www.ncbi.nlm.nih.gov/")
    assert "data_source" in body
    assert "GeneReviews" in body["data_source"]
