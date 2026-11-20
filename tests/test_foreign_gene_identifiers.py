"""Gene parameters reject identifiers from other namespaces (genefoundry-router #343).

A well-formed Ensembl/HGNC/RefSeq/NCBI identifier is not a gene symbol: it must be an
``invalid_input`` naming the converter, never a silent empty, a fuzzy symbol suggestion,
or a next step that repeats the same input.
"""

from __future__ import annotations

import re
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastmcp import Client

from genereview_link.api.client_manager import get_managed_client
from genereview_link.api.errors import StructuredHTTPException
from genereview_link.api.gene_identifier import (
    GENE_PATH_PATTERN,
    reject_foreign_gene_identifier,
)
from genereview_link.api.routes.passages import get_embedding_provider, get_repository
from genereview_link.config import ServerConfig
from genereview_link.retrieval.embeddings import FakeEmbeddingProvider
from genereview_link.server_manager import UnifiedServerManager
from genereview_link.services.gene_index import GeneIndex

HGNC = {"tool": "hgnc_resolve_symbol"}
FOREIGN = [
    ("ENSG00000133703", {**HGNC, "arguments": {"query": "ENSG00000133703"}}),
    ("ENSG00000133703.14", {**HGNC, "arguments": {"query": "ENSG00000133703.14"}}),
    ("ENST00000311936", None),  # transcript id: hgnc_resolve_symbol cannot convert it
    ("HGNC:6407", {**HGNC, "arguments": {"query": "HGNC:6407"}}),
    ("NM_004985.5", {**HGNC, "arguments": {"query": "NM_004985.5"}}),
    (
        "NCBIGene:3845",
        {
            "tool": "hgnc_resolve_gene_by_xref",
            "arguments": {"source": "entrez_id", "value": "3845"},
        },
    ),
    (
        "3845",
        {
            "tool": "hgnc_resolve_gene_by_xref",
            "arguments": {"source": "entrez_id", "value": "3845"},
        },
    ),
]


HGNC_6407 = {**HGNC, "arguments": {"query": "HGNC:6407"}}


class _EmptyClient:
    async def search_genereviews(self, *a: Any, **kw: Any) -> dict[str, Any]:
        return {"count": 0, "retmax": 20, "retstart": 0, "ids": [], "webenv": "", "querykey": ""}


async def _build_mcp() -> Any:
    repo = MagicMock()
    repo.search_passages = AsyncMock(return_value=[])
    repo._dense_candidates_filtered = AsyncMock(return_value=[])
    repo.get_chapters_by_gene = AsyncMock(return_value=[])
    repo.get_defining_chapter_by_gene = AsyncMock(return_value=None)

    config = ServerConfig(transport="http", log_level="WARNING", enable_docs=False)
    manager = UnifiedServerManager()
    app = manager.create_fastapi_app(config)

    async def _client() -> Any:
        yield _EmptyClient()

    app.dependency_overrides[get_managed_client] = _client
    app.dependency_overrides[get_repository] = lambda: repo
    app.dependency_overrides[get_embedding_provider] = lambda: FakeEmbeddingProvider(dim=384)
    app.state.repository = repo
    # HGF/HGD are the symbols the audit saw fuzzy-suggested for "HGNC:6407".
    app.state.gene_index = GeneIndex(symbols=frozenset({"KRAS", "HGF", "HGD"}))
    return await manager.create_mcp_server(app, config)


def _assert_foreign(payload: dict[str, Any], field: str, converter: dict[str, Any] | None) -> None:
    assert payload["error_code"] == "invalid_input"
    assert payload["retryable"] is False
    assert payload["field"] == field
    assert payload["allowed_values"] == ["<HGNC gene symbol>, e.g. KRAS"]
    if converter is None:
        assert "transcript" in payload["message"]
        assert "hgnc_resolve" not in payload["hint"]
    else:
        assert converter["tool"] in payload["hint"]
    assert "ENS" not in payload["message"] and "6407" not in payload["message"]


@pytest.mark.parametrize("value", ["KRAS", "C9orf72", "P2RY12", "HLA-DRB1", "ENSA", "NMT1"])
def test_real_symbols_pass_the_shape_check(value: str) -> None:
    reject_foreign_gene_identifier("gene", value)


@pytest.mark.asyncio
@pytest.mark.parametrize(("value", "converter"), FOREIGN)
@pytest.mark.parametrize(
    ("tool", "field", "extra"),
    [
        ("search_genereviews", "gene_symbol", {}),
        ("get_genereview_summary", "gene_symbol", {}),
        ("search_passages", "gene", {"q": "Noonan syndrome diagnosis"}),
    ],
)
async def test_foreign_gene_identifier_is_invalid_input_naming_the_converter(
    tool: str, field: str, extra: dict[str, Any], value: str, converter: dict[str, Any] | None
) -> None:
    mcp = await _build_mcp()
    async with Client(mcp) as client:
        result = await client.call_tool(tool, {field: value, **extra}, raise_on_error=False)

    assert result.is_error is True
    payload = result.structured_content
    assert payload["success"] is False
    _assert_foreign(payload, field, converter)
    assert payload["_meta"]["next_commands"] == ([converter] if converter else [])


@pytest.mark.asyncio
async def test_batch_reports_a_foreign_gene_per_item_and_keeps_the_rest() -> None:
    mcp = await _build_mcp()
    async with Client(mcp) as client:
        result = await client.call_tool(
            "search_passages_batch",
            {
                "specs": [
                    {"q": "Noonan syndrome diagnosis", "gene": "KRAS"},
                    {"q": "Noonan syndrome diagnosis", "gene": "HGNC:6407"},
                ]
            },
            raise_on_error=False,
        )

    assert result.is_error is False
    ok, bad = result.structured_content["results"]
    assert ok["error"] is None
    assert bad["hits"] == []
    _assert_foreign(bad["error"], "gene", HGNC_6407)
    assert bad["error"]["next_commands"] == [HGNC_6407]


@pytest.mark.asyncio
async def test_batch_with_only_failing_specs_still_answers_per_item() -> None:
    mcp = await _build_mcp()
    async with Client(mcp) as client:
        one = await client.call_tool(
            "search_passages_batch",
            {"specs": [{"q": "x", "gene": "ENSG00000133703"}]},
            raise_on_error=False,
        )
        both = await client.call_tool(
            "search_passages_batch",
            {"specs": [{"q": "x", "gene": "HGNC:6407"}, {"q": "x", "gene": "ZZZ9"}]},
            raise_on_error=False,
        )

    assert one.is_error is False
    _assert_foreign(one.structured_content["results"][0]["error"], "gene", FOREIGN[0][1])
    assert both.is_error is False
    first, second = both.structured_content["results"]
    _assert_foreign(first["error"], "gene", HGNC_6407)
    assert second["error"]["error_code"] == "not_found"


@pytest.mark.asyncio
async def test_batch_keeps_good_spec_when_sibling_gene_is_not_indexed() -> None:
    mcp = await _build_mcp()
    async with Client(mcp) as client:
        result = await client.call_tool(
            "search_passages_batch",
            {"specs": [{"q": "x", "gene": "KRAS"}, {"q": "x", "gene": "ZZZ9"}]},
            raise_on_error=False,
        )

    assert result.is_error is False
    ok, bad = result.structured_content["results"]
    assert ok["error"] is None
    assert bad["error"]["error_code"] == "not_found"


@pytest.mark.parametrize(
    "value",
    [
        "HGNC6407",
        "HGNC:abc",
        "hgnc_6407",
        "ENSP00000256078",
        "ENSMUSG00000030265",
        "HGNC:\u0666\u0664\u0660\u0667",
    ],
)
def test_near_miss_identifier_forms_are_rejected_without_a_converter(value: str) -> None:
    with pytest.raises(StructuredHTTPException) as exc:
        reject_foreign_gene_identifier("gene", value)
    detail: Any = exc.value.detail
    assert detail["code"] == "unsupported_gene_identifier"
    assert detail["next_commands"] == []
    assert "hgnc_resolve" not in detail["recovery_hint"]


def test_non_ascii_digits_never_reach_a_converter_call() -> None:
    reject_foreign_gene_identifier(
        "gene", "\u0663\u0668\u0664\u0665"
    )  # not an identifier shape: no converter built
    assert re.fullmatch(GENE_PATH_PATTERN, "HGNC:\u0666\u0664\u0660\u0667") is None


def test_bare_number_says_how_it_was_read() -> None:
    with pytest.raises(StructuredHTTPException) as exc:
        reject_foreign_gene_identifier("gene", "6407")
    detail: Any = exc.value.detail
    assert "a bare number, read here as an NCBI Gene id" in detail["message"]
    assert "If it is an HGNC id, send it as HGNC:<n>." in detail["recovery_hint"]


@pytest.mark.asyncio
async def test_gene_shape_check_runs_before_the_nbk_lookup() -> None:
    mcp = await _build_mcp()
    async with Client(mcp) as client:
        result = await client.call_tool(
            "search_passages",
            {"q": "x", "gene": "HGNC:6407", "nbk_id": "NBK999999999"},
            raise_on_error=False,
        )

    assert result.structured_content["error_code"] == "invalid_input"
    assert result.structured_content["field"] == "gene"


@pytest.mark.asyncio
async def test_real_symbol_without_data_keeps_the_honest_empty() -> None:
    mcp = await _build_mcp()
    async with Client(mcp) as client:
        passages = await client.call_tool(
            "search_passages", {"q": "Noonan syndrome diagnosis", "gene": "KRAS"}
        )
        pmids = await client.call_tool("search_genereviews", {"gene_symbol": "KRAS"})

    assert passages.structured_content["success"] is True
    assert passages.structured_content["results"] == []
    assert pmids.structured_content["success"] is True
    assert pmids.structured_content["results"] == []


def test_search_passages_description_names_its_batch_sibling() -> None:
    app = UnifiedServerManager().create_fastapi_app(
        ServerConfig(transport="http", log_level="WARNING", enable_docs=False)
    )
    desc = app.openapi()["paths"]["/passages/search"]["get"]["description"]
    rest = desc.split(". ", 1)[1]
    assert rest.startswith(
        "For 2 or more queries, call search_passages_batch once instead of calling this "
        "tool per item."
    )
