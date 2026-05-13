"""MCPErrorPayload + StructuredHTTPException round-trip."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from genereview_link.api.errors import (
    FieldError,
    MCPErrorPayload,
    StructuredHTTPException,
)
from genereview_link.api.orchestration_errors import (
    gene_not_found_error,
    pmid_resolver_failed_error,
)


def test_payload_model_dump_round_trip():
    p = MCPErrorPayload(
        code="x",
        message="m",
        recovery_hint="try y",
        field_errors=[FieldError(field="f", reason="r", valid_values=["a", "b"])],
        next_commands=[{"tool": "search_passages", "arguments": {"q": "BRCA1"}}],
    )
    dumped = p.model_dump(mode="json")
    assert dumped["code"] == "x"
    assert dumped["field_errors"][0]["valid_values"] == ["a", "b"]
    assert dumped["next_commands"][0]["tool"] == "search_passages"


@pytest.mark.asyncio
async def test_structured_http_exception_body_is_payload():
    app = FastAPI()

    @app.get("/raises")
    def raises():
        raise StructuredHTTPException(
            status_code=404,
            code="not_found",
            message="nope",
            recovery_hint="try harder",
        )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get("/raises")
    assert resp.status_code == 404
    body = resp.json()
    assert body["detail"]["code"] == "not_found"
    assert body["detail"]["recovery_hint"] == "try harder"


def test_gene_not_found_error_has_search_passages_fallback() -> None:
    err = gene_not_found_error("BRCA1")
    detail = err.detail
    assert detail["code"] == "gene_not_found"
    assert detail["recovery_hint"]
    assert detail["next_commands"][0]["tool"] == "search_passages"
    assert detail["next_commands"][0]["arguments"]["gene"] == "BRCA1"


def test_pmid_resolver_failed_error_echoes_pmid() -> None:
    err = pmid_resolver_failed_error("20301425", gene_symbol="BRCA1")
    assert err.detail["code"] == "pmid_resolver_failed"
    assert "20301425" in err.detail["message"]
