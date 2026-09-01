"""Hourly corpus-staleness watcher, single-fired across gunicorn workers.

This watcher OBSERVES and RECORDS. It deliberately does not pull.

It used to end in `if settings.AUTO_PULL_RELEASES: pass`, with the comment "implementation
extends Task 6.3 bootstrap into a hot-swap path". That branch never landed, so a setting
named AUTO_PULL_RELEASES silently did nothing, `public.genereview_refresh_log` was never
written by anything, and the corpus sat at 2026-05-12 with no signal that it had. A
scheduler that quietly does nothing is the same class of defect as a checksum that
verifies nothing: the observable state says "handled".

Pulling is not the missing feature. Corpus data reaches PostgreSQL only through the
reviewed data release and the no-egress init sidecar (#97); a serving process that fetched
and swapped a corpus would hold exactly the egress and database rights that design exists
to deny it. So AUTO_PULL_RELEASES is now refused outright rather than ignored, and the
watcher's real job -- telling you the corpus has fallen behind -- is actually done.
"""

from __future__ import annotations

import time
from typing import Any

import asyncpg

from genereview_link.config import settings
from genereview_link.ingest.github_release import resolve_latest
from genereview_link.logging_config import get_logger

logger = get_logger("release.watcher")

RELEASE_WATCHER_LOCK_ID = 0x47525F524C5F31  # "GR_RL_1"

#: Recorded in `genereview_refresh_log.decision`.
DECISION_CURRENT = "current"
DECISION_STALE = "stale"
DECISION_NO_CORPUS = "no-active-corpus"
DECISION_UNAVAILABLE = "upstream-unavailable"

__all__ = [
    "DECISION_CURRENT",
    "DECISION_NO_CORPUS",
    "DECISION_STALE",
    "DECISION_UNAVAILABLE",
    "RELEASE_WATCHER_LOCK_ID",
    "check_for_new_release",
    "release_tag_of",
]


def release_tag_of(asset_url: str) -> str | None:
    """Return the release tag embedded in a GitHub release-asset URL, if present."""
    marker = "/releases/download/"
    if marker not in asset_url:
        return None
    tail = asset_url.split(marker, 1)[1]
    tag = tail.split("/", 1)[0]
    return tag or None


async def _record(conn: Any, decision: str, *, duration_ms: int, detail: dict[str, object]) -> None:
    """Append one observation to the refresh log.

    The log is the whole point of the watcher: without a row, a stale corpus is invisible
    to anyone not reading container logs at the right moment.
    """
    import json

    await conn.execute(
        "insert into public.genereview_refresh_log (decision, duration_ms, detail) "
        "values ($1, $2, $3::jsonb)",
        decision,
        duration_ms,
        json.dumps(detail, sort_keys=True),
    )


async def check_for_new_release(pool: asyncpg.Pool) -> None:
    """Compare the newest published corpus release with the active one, and record it."""
    started = time.monotonic()
    async with pool.acquire() as conn:
        got = await conn.fetchval("select pg_try_advisory_lock($1)", RELEASE_WATCHER_LOCK_ID)
        if not got:
            return
        try:
            active = await conn.fetchval(
                "select version from public.genereview_corpus_version where is_active"
            )
            pinned = settings.CORPUS_RELEASE_TAG or None
            try:
                latest_url = await resolve_latest(settings.GITHUB_REPO)
            except Exception as exc:
                # Upstream being unreachable is an observation, not a crash: record it so
                # a watcher that has stopped seeing releases is distinguishable from one
                # that is seeing no NEW releases.
                duration_ms = int((time.monotonic() - started) * 1000)
                await _record(
                    conn,
                    DECISION_UNAVAILABLE,
                    duration_ms=duration_ms,
                    detail={"active": active, "error_type": type(exc).__name__},
                )
                logger.warning("release watcher could not reach upstream", error=str(exc))
                return

            latest_tag = release_tag_of(latest_url)
            if not active:
                decision = DECISION_NO_CORPUS
            elif pinned and latest_tag and latest_tag != pinned:
                decision = DECISION_STALE
            else:
                decision = DECISION_CURRENT

            duration_ms = int((time.monotonic() - started) * 1000)
            detail: dict[str, object] = {
                "active": active,
                "pinned_release_tag": pinned,
                "latest_release_tag": latest_tag,
                "latest_asset_url": latest_url,
            }
            await _record(conn, decision, duration_ms=duration_ms, detail=detail)

            if decision == DECISION_STALE:
                logger.warning(
                    "a newer corpus release is published; the deployment is pinned to an "
                    "older one. Update container-release.json, stage the asset, and "
                    "redeploy -- the serving process deliberately cannot pull it.",
                    active=active,
                    pinned_release_tag=pinned,
                    latest_release_tag=latest_tag,
                )
            else:
                logger.info("release watcher observation recorded", decision=decision)
        finally:
            await conn.fetchval("select pg_advisory_unlock($1)", RELEASE_WATCHER_LOCK_ID)
