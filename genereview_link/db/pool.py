"""Async pool factory for Postgres connections."""

from __future__ import annotations

import contextlib

import asyncpg
import pgvector.asyncpg

from genereview_link import config


async def _init_conn(conn: asyncpg.Connection) -> None:
    """Register pgvector codec on each new connection.

    Tolerant of the pre-migration state where the ``vector`` extension does
    not yet exist (e.g. the very first ``db migrate`` invocation on a fresh
    database). Subsequent connections — after the data migrations have
    installed the extension — will register the codec successfully.
    """
    with contextlib.suppress(ValueError):
        await pgvector.asyncpg.register_vector(conn)


async def create_pool() -> asyncpg.Pool:
    """Create an asyncpg pool from settings.

    Reads ``config.settings`` lazily (at call time, not import time) so tests
    that reassign ``genereview_link.config.settings`` see updated values.

    Raises:
        RuntimeError: if DATABASE_URL is empty.
    """
    s = config.settings
    if not s.DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not configured")
    return await asyncpg.create_pool(
        dsn=s.DATABASE_URL,
        min_size=s.DATABASE_POOL_MIN_SIZE,
        max_size=s.DATABASE_POOL_MAX_SIZE,
        init=_init_conn,
    )
