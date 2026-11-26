"""Response-Envelope v1.2: every tool result validates against the published JSON Schema.

The schema is vendored from https://github.com/berntpopp/genefoundry-envelope at tag v1.2.0.
Every tool runs through the real MCP facade over the recorded repository rows and NCBI
payloads of the untrusted-content fencing tests.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import jsonschema
import pytest
from fastapi import FastAPI
from fastmcp import Client

from genereview_link.api.client_manager import get_managed_client
from genereview_link.api.routes import (
    abstract,
    chapters,
    fulltext,
    genereview,
    links,
    passages,
    search,
    search_batch,
    tables,
)
from genereview_link.api.routes import license as license_routes
from genereview_link.config import ServerConfig
from genereview_link.mcp import provenance
from genereview_link.retrieval.embeddings import FakeEmbeddingProvider
from genereview_link.server_manager import UnifiedServerManager
from genereview_link.services.genereview_service import GeneReviewService
from genereview_link.services.service_manager import get_managed_service
from tests.unit.mcp.test_untrusted_content_fencing import (
    _hostile_passage_row,
    _hostile_repo,
    _HostileClient,
)

SCHEMA_PATH = Path(__file__).with_name("envelope-v1.2.schema.json")
SCHEMA_SHA256 = "c3c47e20d7dd189aac2568fc963b5514d7a5ac2c3e5f21d126f42c96d1ad1ae3"
SCHEMA = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

CORPUS_VERSION = "2026-10-01"
CORPUS_INGESTED = "2026-10-02T03:04:05Z"
GENE = {"gene_symbol": "BRCA1"}
# (tool, arguments, expected data_version): corpus tools name the corpus release, tools
# that fetch from NCBI at call time are "live".
DATA_CALLS: list[tuple[str, dict[str, Any], str]] = [
    ("search_passages", {"q": "BRCA1", "mode": "full"}, CORPUS_VERSION),
    ("search_passages", {"q": "BRCA1", "mode": "ids_only"}, CORPUS_VERSION),
    ("search_passages_batch", {"specs": [{"q": "BRCA1", "mode": "full"}]}, CORPUS_VERSION),
    ("get_passage", {"passage_id": "NBK1116:0042"}, CORPUS_VERSION),
    ("get_passages_batch", {"ids": ["NBK1116:0042"]}, CORPUS_VERSION),
    ("get_chapter_section", {"nbk_id": "NBK1116", "section": "management"}, CORPUS_VERSION),
    ("get_chapter_metadata", {"nbk_id": "NBK1116"}, CORPUS_VERSION),
    ("get_table", {"nbk_id": "NBK1116", "table_id": "t1"}, CORPUS_VERSION),
    ("get_license", {}, CORPUS_VERSION),
    ("search_genereviews", {**GENE, "fresh": True}, "live"),
    ("get_abstract", {"pmid": "20301425"}, "live"),
    ("get_links", {"pmid": "20301425"}, "live"),
    ("get_fulltext", {"nbk_id": "NBK1116"}, "live"),
    ("get_genereview_summary", {**GENE, "fresh": True}, "live"),
    ("get_genereview_summary", GENE, "live"),
]
ERROR_CALLS: list[tuple[str, dict[str, Any]]] = [
    ("get_abstract", {"pmid": "not-a-pmid"}),
    ("get_license", {"wrong_argument": 1}),
    ("no_such_tool", {}),
]


@pytest.fixture
async def mcp(monkeypatch: pytest.MonkeyPatch) -> Any:
    app = FastAPI()
    for module in (
        passages,
        search_batch,
        chapters,
        tables,
        abstract,
        fulltext,
        links,
        genereview,
        search,
        license_routes,
    ):
        app.include_router(module.router)

    class _Acquire:  # get_passages_batch reads rows over one pooled connection
        async def __aenter__(self) -> Any:
            return MagicMock(execute=AsyncMock())

        async def __aexit__(self, *exc: Any) -> None:
            return None

    app.state.repository = _hostile_repo(
        _acquire=MagicMock(return_value=_Acquire()),
        _fetch_passage_row=AsyncMock(return_value=_hostile_passage_row()),
    )
    app.state.embedder = FakeEmbeddingProvider(dim=384)
    app.state.corpus_version = CORPUS_VERSION
    monkeypatch.setattr(provenance, "_active_corpus", (CORPUS_VERSION, CORPUS_INGESTED))
    service = GeneReviewService(client=_HostileClient())  # type: ignore[arg-type]

    async def _client() -> Any:
        yield _HostileClient()

    async def _service() -> Any:
        yield service

    app.dependency_overrides[get_managed_client] = _client
    app.dependency_overrides[get_managed_service] = _service
    return await UnifiedServerManager().create_mcp_server(app, ServerConfig())


async def _call(mcp: Any, tool: str, arguments: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    async with Client(mcp) as client:
        result = await client.call_tool(tool, arguments, raise_on_error=False)
    return result.structured_content, result.is_error


def test_vendored_schema_is_the_v1_2_0_release() -> None:
    assert hashlib.sha256(SCHEMA_PATH.read_bytes()).hexdigest() == SCHEMA_SHA256


async def test_data_calls_cover_every_registered_tool(mcp: Any) -> None:
    assert {name for name, _, _ in DATA_CALLS} == {tool.name for tool in await mcp.list_tools()}


@pytest.mark.parametrize(("tool", "arguments", "data_version"), DATA_CALLS)
async def test_success_validates_and_carries_provenance(
    mcp: Any, tool: str, arguments: dict[str, Any], data_version: str
) -> None:
    before = provenance.utc_z(datetime.now(UTC))
    envelope, is_error = await _call(mcp, tool, arguments)
    assert envelope["success"] is True and is_error is False
    jsonschema.validate(envelope, SCHEMA)
    meta = envelope["_meta"]
    assert meta["source"] == "genereviews"
    assert meta["data_version"] == data_version
    if data_version == "live":
        assert before <= meta["retrieved_at"] <= provenance.utc_z(datetime.now(UTC))
    else:
        assert meta["retrieved_at"] == CORPUS_INGESTED


@pytest.mark.parametrize(("tool", "arguments"), ERROR_CALLS)
async def test_error_validates(mcp: Any, tool: str, arguments: dict[str, Any]) -> None:
    envelope, is_error = await _call(mcp, tool, arguments)
    assert envelope["success"] is False and is_error is True
    jsonschema.validate(envelope, SCHEMA)


async def test_cache_hit_keeps_the_original_fetch_time(
    mcp: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _Clock(datetime):
        ticks = iter(range(1, 9))

        @classmethod
        def now(cls, tz: Any = None) -> datetime:
            return datetime(2026, 1, next(cls.ticks), tzinfo=UTC)

    monkeypatch.setattr("genereview_link.services.genereview_service.datetime", _Clock)
    first, _ = await _call(mcp, "get_genereview_summary", GENE)
    hit, _ = await _call(mcp, "get_genereview_summary", GENE)
    assert first["_meta"]["retrieved_at"] == hit["_meta"]["retrieved_at"] == "2026-01-01T00:00:00Z"
