"""Integration test: advisory lock ensures only one worker fires per interval."""

from __future__ import annotations

import asyncio

import asyncpg
import pytest

from genereview_link.ingest.scheduler import RELEASE_WATCHER_LOCK_ID


@pytest.mark.asyncio
@pytest.mark.integration
async def test_advisory_lock_only_one_acquires(pool: asyncpg.Pool) -> None:
    """Two concurrent coroutines race for the advisory lock; only one wins."""
    results: list[bool] = []

    async def try_lock() -> None:
        async with pool.acquire() as conn:
            got = await conn.fetchval("select pg_try_advisory_lock($1)", RELEASE_WATCHER_LOCK_ID)
            results.append(bool(got))
            if got:
                # hold it briefly then release
                await asyncio.sleep(0.05)
                await conn.fetchval("select pg_advisory_unlock($1)", RELEASE_WATCHER_LOCK_ID)

    # Make sure lock is clean before test
    async with pool.acquire() as conn:
        await conn.fetchval("select pg_advisory_unlock_all()")

    await asyncio.gather(try_lock(), try_lock())

    # Exactly one coroutine should have acquired the lock
    assert results.count(True) == 1
    assert results.count(False) == 1


@pytest.mark.asyncio
@pytest.mark.integration
async def test_advisory_lock_released_after_use(pool: asyncpg.Pool) -> None:
    """After releasing the lock a second caller can acquire it."""
    # Ensure clean state
    async with pool.acquire() as conn:
        await conn.fetchval("select pg_advisory_unlock_all()")

    async with pool.acquire() as conn:
        got = await conn.fetchval("select pg_try_advisory_lock($1)", RELEASE_WATCHER_LOCK_ID)
        assert got is True
        await conn.fetchval("select pg_advisory_unlock($1)", RELEASE_WATCHER_LOCK_ID)

    # A second acquire on a fresh connection should now succeed
    async with pool.acquire() as conn:
        got2 = await conn.fetchval("select pg_try_advisory_lock($1)", RELEASE_WATCHER_LOCK_ID)
        assert got2 is True
        await conn.fetchval("select pg_advisory_unlock($1)", RELEASE_WATCHER_LOCK_ID)
