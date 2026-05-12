"""Hard regression gate for ranking_baseline.json exact-symbol-anchor rows.

Per the C-alpha gate specified in
docs/superpowers/specs/2026-05-12-ranking-architecture-redesign-design.md,
any model that changes the locked top-1 on a regression_kind=exact-symbol-anchor
entry disqualifies the model. This test runs as part of the standard test suite
and fails CI if upgraded rerank=rrf regresses an anchor.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import AsyncClient

BASELINE = json.loads(Path("tests/fixtures/ranking_baseline.json").read_text())


@pytest_asyncio.fixture
async def client() -> AsyncIterator[AsyncClient]:
    """Async HTTP client pointed at the live MCP server (port 8765)."""
    async with AsyncClient(base_url="http://127.0.0.1:8765") as c:
        yield c


@pytest.mark.parametrize(
    "entry",
    [e for e in BASELINE if e.get("regression_kind") == "exact-symbol-anchor"],
    ids=lambda e: e["query"][:40] if isinstance(e, dict) else str(e),
)
async def test_must_not_regress_exact_symbol_anchor(
    client: AsyncClient, entry: dict
) -> None:
    r = await client.get(
        "/passages/search",
        params={
            "q": entry["query"],
            "rerank": "rrf",
            "mode": "ids_only",
            "limit": 5,
        },
    )
    r.raise_for_status()
    ids = [row["passage_id"] for row in r.json()["results"]]
    assert ids, f"no results for {entry['query']}"
    assert ids[0] == entry["expected_top1_passage_id"], (
        f"REGRESSION on exact-symbol-anchor query {entry['query']!r}: "
        f"expected top-1 {entry['expected_top1_passage_id']}, got {ids[0]}"
    )
