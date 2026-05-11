"""Smoke test for asyncpg pool factory."""

from __future__ import annotations

import pytest

from genereview_link.db.pool import create_pool


@pytest.mark.asyncio
async def test_pool_can_be_acquired_and_queries(monkeypatch, database_url: str) -> None:
    monkeypatch.setenv("DATABASE_URL", database_url)
    # Reload settings so the new env var takes effect
    from genereview_link import config as cfg

    cfg.settings = cfg.Settings()
    pool = await create_pool()
    try:
        async with pool.acquire() as conn:
            value = await conn.fetchval("select 1")
            assert value == 1
    finally:
        await pool.close()
