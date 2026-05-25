from __future__ import annotations

import asyncpg


async def test_search_path_survives_pool_release_and_reacquire(
    pool: asyncpg.Pool,
) -> None:
    async with pool.acquire() as conn:
        search_path_a = await conn.fetchval("show search_path")

    async with pool.acquire() as conn:
        search_path_b = await conn.fetchval("show search_path")

    assert search_path_a == "genereview, public"
    assert search_path_b == "genereview, public"
