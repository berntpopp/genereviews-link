"""HNSW index must not exist before `embed` finishes."""

from __future__ import annotations

import asyncpg
import pytest

from genereview_link.db.migrate import apply_control_migrations, apply_data_migrations
from genereview_link.ingest.orchestrator import build_hnsw_index


@pytest.mark.asyncio
async def test_hnsw_absent_after_migrations(pool: asyncpg.Pool) -> None:
    await apply_control_migrations(pool)
    await apply_data_migrations(pool, schema="genereview")
    async with pool.acquire() as conn:
        exists = await conn.fetchval(
            """
            select exists (
              select 1 from pg_indexes
               where schemaname = 'genereview'
                 and indexname = 'genereview_embeddings_bge384_hnsw_cosine'
            )
            """
        )
    assert exists is False


@pytest.mark.asyncio
async def test_build_hnsw_index_creates_it(pool: asyncpg.Pool) -> None:
    await apply_control_migrations(pool)
    await apply_data_migrations(pool, schema="genereview")
    await build_hnsw_index(pool, schema="genereview")
    async with pool.acquire() as conn:
        exists = await conn.fetchval(
            """
            select exists (
              select 1 from pg_indexes
               where schemaname = 'genereview'
                 and indexname = 'genereview_embeddings_bge384_hnsw_cosine'
            )
            """
        )
    assert exists is True
