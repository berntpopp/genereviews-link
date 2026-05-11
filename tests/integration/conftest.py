"""Shared fixtures for integration tests requiring a real Postgres."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

import asyncpg
import pytest
import pytest_asyncio
from pgvector.asyncpg import register_vector


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Auto-mark every test in tests/integration/ with @pytest.mark.integration."""
    for item in items:
        if "tests/integration/" in str(item.fspath) or "tests\\integration\\" in str(item.fspath):
            item.add_marker(pytest.mark.integration)


def _database_url() -> str:
    url = os.environ.get("GENEREVIEW_TEST_DATABASE_URL")
    if not url:
        pytest.skip("GENEREVIEW_TEST_DATABASE_URL not set; integration test skipped")
    return url


@pytest.fixture
def database_url() -> str:
    return _database_url()


async def _wipe(pool: asyncpg.Pool) -> None:
    """Drop all genereview-related state so each test starts clean."""
    async with pool.acquire() as conn:
        await conn.execute("drop schema if exists genereview cascade")
        await conn.execute("drop schema if exists genereview_staging cascade")
        rows = await conn.fetch(
            "select schema_name from information_schema.schemata "
            "where schema_name like 'genereview_old_%'"
        )
        for row in rows:
            await conn.execute(f"drop schema if exists {row['schema_name']} cascade")
        await conn.execute("drop table if exists public.schema_migrations cascade")
        await conn.execute("drop table if exists public.genereview_corpus_version cascade")
        await conn.execute("drop table if exists public.genereview_refresh_log cascade")
        await conn.execute("drop table if exists public.genereview_active_embedding cascade")


@pytest_asyncio.fixture
async def pool() -> AsyncIterator[asyncpg.Pool]:
    """Yield a pool against the test Postgres; wipe genereview state before and after."""
    url = _database_url()
    pool = await asyncpg.create_pool(url, min_size=1, max_size=4, init=register_vector)
    await _wipe(pool)
    yield pool
    await _wipe(pool)
    await pool.close()
