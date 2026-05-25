"""Shared fixtures for integration tests requiring a real Postgres."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from urllib.parse import urlparse

import asyncpg
import pytest
import pytest_asyncio

from genereview_link.db.pool import _init_conn


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Auto-mark every test in tests/integration/ with @pytest.mark.integration."""
    for item in items:
        if "tests/integration/" in str(item.fspath) or "tests\\integration\\" in str(item.fspath):
            item.add_marker(pytest.mark.integration)


def _assert_test_database(url: str) -> None:
    """Refuse to run destructive integration fixtures against a non-test DB.

    The ``pool`` fixture drops the ``genereview`` schema before and after each
    test. If ``GENEREVIEW_TEST_DATABASE_URL`` is ever pointed at a populated
    dev/prod database, that wipe destroys real data. Require the database name
    to contain ``test`` (or an explicit override env) so this cannot happen by
    accident.
    """
    if os.environ.get("GENEREVIEW_TEST_ALLOW_NON_TEST_DB") == "1":
        return
    dbname = urlparse(url).path.lstrip("/")
    if "test" not in dbname.lower():
        pytest.fail(
            f"Refusing to run destructive integration fixtures against database "
            f"{dbname!r} — its name does not contain 'test'. Point "
            f"GENEREVIEW_TEST_DATABASE_URL at a dedicated test database, or set "
            f"GENEREVIEW_TEST_ALLOW_NON_TEST_DB=1 to override (not recommended).",
            pytrace=False,
        )


def _database_url() -> str:
    url = os.environ.get("GENEREVIEW_TEST_DATABASE_URL")
    if not url:
        pytest.skip("GENEREVIEW_TEST_DATABASE_URL not set; integration test skipped")
    _assert_test_database(url)
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
    pool = await asyncpg.create_pool(
        url,
        min_size=1,
        max_size=4,
        server_settings={"search_path": "genereview, public"},
        init=_init_conn,
    )
    await _wipe(pool)
    yield pool
    await _wipe(pool)
    await pool.close()
