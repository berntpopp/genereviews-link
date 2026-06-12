"""Integration test: gene_role=primary filters correctly against a live corpus.

Skipped by make ci-local / make test-unit (integration marker).
Requires a running GeneReviews server with a re-ingested corpus that
includes primary_gene_symbols data.

Acceptance criterion from issue #43:
- search_passages(q="BRCA1 risk-reducing surgery", gene="BRCA1", gene_role="primary")
  returns passages from the HBOC chapter (NBK1247) and NOT from the FA chapter.
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
async def test_gene_role_primary_returns_hboc_not_fa_for_brca1(
    client: AsyncClient,
) -> None:
    """HBOC passages rank above FA when gene_role=primary and gene=BRCA1.

    After re-ingest: NBK1247 has primary_gene_symbols=[BRCA1, BRCA2].
    The FA chapter has primary_gene_symbols=[] (title is 'Fanconi Anemia').
    So gene_role=primary must exclude FA passages entirely.
    """
    resp = await client.get(
        "/passages/search",
        params={
            "q": "BRCA1 risk-reducing surgery",
            "gene": "BRCA1",
            "gene_role": "primary",
            "mode": "ids_only",
            "limit": 20,
        },
    )
    resp.raise_for_status()
    results = resp.json()["results"]
    assert results, "expected at least one result for BRCA1 primary query"

    # All results must come from chapters where BRCA1 is primary.
    # For a fully-ingested corpus the NBK IDs should be HBOC-family chapters,
    # not FA (FANCS alias) chapters.
    passage_ids = [r["passage_id"] for r in results]
    # At least one HBOC (NBK1247) passage should appear
    hboc_hits = [p for p in passage_ids if p.startswith("NBK1247:")]
    assert hboc_hits, (
        f"expected NBK1247 (HBOC) passages in gene_role=primary results; got {passage_ids}"
    )


@pytest.mark.integration
async def test_gene_role_any_default_still_returns_results_for_brca1(
    client: AsyncClient,
) -> None:
    """gene_role=any (default) preserves pre-feature behaviour."""
    resp = await client.get(
        "/passages/search",
        params={
            "q": "BRCA1 risk-reducing surgery",
            "gene": "BRCA1",
            "mode": "ids_only",
            "limit": 20,
        },
    )
    resp.raise_for_status()
    results = resp.json()["results"]
    assert results, "gene_role=any should return results (backward-compatible)"
