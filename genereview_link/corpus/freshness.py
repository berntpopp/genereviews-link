"""Corpus data-as-of reporting for `/health` (#145).

`ingest/scheduler.py` used to end in `if settings.AUTO_PULL_RELEASES: pass`, commented
"implementation extends Task 6.3". That branch never landed: `public.genereview_refresh_log`
stayed empty, the active corpus sat frozen at 2026-05-12 for ~3.7 months, and `/health`
reported `healthy` throughout because nothing checked corpus age. The advisory-lock-guarded
release watcher (`ingest/scheduler.py::check_for_new_release`) now records staleness against
the *newest upstream release* -- but that comparison only fires once an hour, only when
`RELEASE_WATCHER_ENABLED=true`, and only into a log table nobody is paged on. This module
reports staleness against wall-clock time for the corpus actually loaded, directly in
`/health`, so a frozen corpus is a visible fact on every liveness probe rather than a row an
operator has to go looking for.

`data_as_of` is `public.genereview_corpus_version.ingest_finished_at` for the active corpus.
That table is restored **verbatim** as `TABLE DATA` from the release bundle (see
`genereview_link/db/restore.py::CORPUS_TABLES`), so the value is the exact moment the
bundle's content was finalised upstream -- not when this deployment happened to restore it.
The bundle's `manifest.json` carries a `created_at` field too (set moments after
`ingest_finished_at`, once the finished corpus is packaged), but the manifest itself never
reaches the serving container: the no-egress restore sidecar consumes and discards it (see
`genereview_link/cli.py::corpus_restore`). `ingest_finished_at` is the one build-time fact
that is guaranteed to survive into every restored deployment, direct or legacy alike.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

__all__ = ["corpus_health"]


def corpus_health(
    state: Any,
    *,
    max_age_days: int,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Summarise the restored corpus's identity and freshness for `/health`.

    Mirrors `retrieval.provider_policy.embedding_health`'s defaults: an app assembled
    outside the normal lifespan (unit tests, embedded use) reports no corpus facts rather
    than fabricating a stale/healthy verdict from state that was simply never initialised.

    Args:
        state: FastAPI `app.state` (or anything with matching attributes). Reads
            `corpus_version` and `corpus_data_as_of`, both set once at startup by
            `server_lifecycle._initialize_state`.
        max_age_days: threshold past which an active corpus counts as stale.
        now: injectable current time for tests; defaults to `datetime.now(UTC)`. Must be
            timezone-aware when provided.

    Returns:
        A dict with `version`, `data_as_of` (ISO 8601 string or `None`), `age_days`,
        `max_age_days`, and `stale` (bool).
    """
    version = getattr(state, "corpus_version", None)
    data_as_of = getattr(state, "corpus_data_as_of", None)
    age_days: int | None = None
    stale = False
    if data_as_of:
        parsed = datetime.fromisoformat(data_as_of)
        current = now if now is not None else datetime.now(UTC)
        age_days = (current - parsed).days
        stale = age_days > max_age_days
    return {
        "version": version,
        "data_as_of": data_as_of,
        "age_days": age_days,
        "max_age_days": max_age_days,
        "stale": stale,
    }
