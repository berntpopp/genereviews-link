"""Integration tests for filter scope under upgraded rerank=rrf.

These tests assert that user-supplied filters apply to BOTH lexical and
dense branches, not just lexical. Closes the round-2 Codex finding.
"""
from __future__ import annotations

import os

import pytest
import pytest_asyncio
from httpx import AsyncClient

BASE_URL = os.environ.get("MCP_BASE_URL", "http://127.0.0.1:8765")


@pytest_asyncio.fixture
async def client() -> AsyncClient:
    async with AsyncClient(base_url=BASE_URL, timeout=30.0) as c:
        yield c


@pytest.mark.integration
async def test_rrf_with_nbk_id_filter_returns_only_that_chapter(client: AsyncClient) -> None:
    r = await client.get(
        "/passages/search",
        params={
            "q": "variant",
            "rerank": "rrf",
            "nbk_id": "NBK1247",
            "mode": "ids_only",
            "limit": 50,
        },
    )
    r.raise_for_status()
    ids = [row["passage_id"] for row in r.json()["results"]]
    assert ids, "should return at least one row"
    leaks = [p for p in ids if not p.startswith("NBK1247:")]
    assert not leaks, f"nbk_id filter leaked: {leaks}"


@pytest.mark.integration
async def test_rrf_with_gene_filter_restricts_to_gene(client: AsyncClient) -> None:
    r = await client.get(
        "/passages/search",
        params={
            "q": "variant",
            "rerank": "rrf",
            "gene": "HFE",
            "mode": "brief",
            "limit": 50,
        },
    )
    r.raise_for_status()
    rows = r.json()["results"]
    assert rows, "should return at least one row"
    for row in rows:
        # gene_symbols may be a list[str] or comma-separated string depending on
        # response shape; accept either as long as 'HFE' appears.
        gs = row.get("gene_symbols", row.get("chapter_gene_symbols", ""))
        if isinstance(gs, list):
            assert "HFE" in gs, (
                f"gene filter leaked: row {row['passage_id']} has gene_symbols {gs}"
            )
        else:
            assert "HFE" in str(gs), (
                f"gene filter leaked: row {row['passage_id']} has gene_symbols {gs}"
            )


@pytest.mark.integration
async def test_rrf_with_section_filter_restricts_to_section(client: AsyncClient) -> None:
    r = await client.get(
        "/passages/search",
        params={
            "q": "BRCA1",
            "rerank": "rrf",
            "sections": ["management"],
            "mode": "brief",
            "limit": 50,
        },
    )
    r.raise_for_status()
    for row in r.json()["results"]:
        section = row.get("chapter_section") or row.get("section")
        assert section == "management", (
            f"section filter leaked: {row['passage_id']} is in {section}"
        )


@pytest.mark.integration
async def test_rrf_with_heading_path_contains_filters_correctly(client: AsyncClient) -> None:
    r = await client.get(
        "/passages/search",
        params={
            "q": "BRCA1",
            "rerank": "rrf",
            "heading_path_contains": "Prevention",
            "mode": "brief",
            "limit": 20,
        },
    )
    r.raise_for_status()
    for row in r.json()["results"]:
        hp = (row.get("heading_path") or "").lower()
        assert "prevention" in hp, (
            f"heading_path_contains leaked: {hp}"
        )
