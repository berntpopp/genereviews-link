"""Tests for the migration runner."""

from __future__ import annotations

import asyncpg
import pytest

from genereview_link.db.migrate import (
    apply_control_migrations,
    apply_data_migrations,
    list_applied,
)


@pytest.mark.asyncio
async def test_apply_control_migrations_is_idempotent(pool: asyncpg.Pool) -> None:
    first = await apply_control_migrations(pool)
    second = await apply_control_migrations(pool)
    assert len(first) >= 1
    assert second == []  # no new migrations applied on second run
    applied = await list_applied(pool, namespace="control")
    assert "0001_base" in applied


@pytest.mark.asyncio
async def test_apply_data_migrations_targets_schema(pool: asyncpg.Pool) -> None:
    await apply_control_migrations(pool)
    applied = await apply_data_migrations(pool, schema="genereview")
    assert len(applied) >= 1
    async with pool.acquire() as conn:
        exists = await conn.fetchval(
            "select exists ("
            "  select 1 from information_schema.tables "
            "  where table_schema = 'genereview' and table_name = 'genereview_chapters'"
            ")"
        )
        assert exists is True
