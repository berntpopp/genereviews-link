"""Missing or wrong model revisions are stale embeddings, not completed work."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

import pytest

from genereview_link.ingest.orchestrator import iter_passages_missing_embedding


class _Connection:
    def __init__(self) -> None:
        self.queries: list[tuple[str, tuple[object, ...]]] = []

    async def execute(self, _query: str) -> None:
        return None

    async def fetch(self, query: str, *args: object) -> list[dict[str, str]]:
        self.queries.append((query, args))
        if len(self.queries) == 1:
            return [
                {
                    "nbk_id": "NBK1",
                    "passage_id": "p1",
                    "text": "text",
                    "passage_type": "narrative",
                }
            ]
        return []


class _Pool:
    def __init__(self) -> None:
        self.connection = _Connection()

    @asynccontextmanager
    async def acquire(self) -> Any:
        yield self.connection


@pytest.mark.asyncio
async def test_missing_or_wrong_revision_is_selected_for_safe_upsert() -> None:
    pool = _Pool()
    batches = [
        batch
        async for batch in iter_passages_missing_embedding(
            pool,  # type: ignore[arg-type]
            model_name="BAAI/bge-small-en-v1.5",
            model_revision="reviewed-revision",
            schema="genereview",
            batch_size=10,
        )
    ]

    assert batches == [[("NBK1", "p1", "text", "narrative")]]
    query, args = pool.connection.queries[0]
    assert "model_revision is distinct from $2" in query.lower()
    assert args[0:2] == ("BAAI/bge-small-en-v1.5", "reviewed-revision")
