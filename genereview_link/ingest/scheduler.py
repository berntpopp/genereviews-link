"""APScheduler hourly release watcher, single-fired across gunicorn workers."""

from __future__ import annotations

import logging

import asyncpg

from genereview_link.config import settings
from genereview_link.ingest.github_release import resolve_latest

logger = logging.getLogger(__name__)

RELEASE_WATCHER_LOCK_ID = 0x47525F524C5F31  # "GR_RL_1"


async def check_for_new_release(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        got = await conn.fetchval("select pg_try_advisory_lock($1)", RELEASE_WATCHER_LOCK_ID)
        if not got:
            return
        try:
            latest_url = await resolve_latest(settings.GITHUB_REPO)
            active = await conn.fetchval(
                "select version from public.genereview_corpus_version where is_active"
            )
            logger.info(
                "release watcher fired",
                extra={"latest_url": latest_url, "active": active},
            )
            # Pull and swap only if AUTO_PULL_RELEASES is true
            if settings.AUTO_PULL_RELEASES:
                pass  # implementation extends Task 6.3 bootstrap into a hot-swap path
        finally:
            await conn.fetchval("select pg_advisory_unlock($1)", RELEASE_WATCHER_LOCK_ID)
