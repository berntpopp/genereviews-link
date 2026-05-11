"""Smoke test: repository can be instantiated against a pool."""

from __future__ import annotations

import asyncpg
import pytest

from genereview_link.db.migrate import apply_control_migrations, apply_data_migrations
from genereview_link.retrieval.repository import GeneReviewRepository


@pytest.mark.asyncio
async def test_active_corpus_version_when_none(pool: asyncpg.Pool) -> None:
    await apply_control_migrations(pool)
    await apply_data_migrations(pool, schema="genereview")
    repo = GeneReviewRepository(pool)
    cv = await repo.active_corpus_version()
    assert cv is None


@pytest.mark.asyncio
async def test_active_embedding_table_returns_default(pool: asyncpg.Pool) -> None:
    await apply_control_migrations(pool)
    repo = GeneReviewRepository(pool)
    table = await repo.active_embedding_table()
    assert table == "genereview_embeddings_bge384"
