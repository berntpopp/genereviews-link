"""Response-Envelope v1.2 provenance: ``data_version`` and ``retrieved_at`` of a result.

A result is either served from the restored corpus (the release it was built from, and
the time that corpus was ingested from NCBI) or fetched live from NCBI (``live``, and
the time of that fetch, which a cached response keeps).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

# (version, ingest time) of the active corpus, set once at startup by the server lifespan.
_active_corpus: tuple[str, str | None] | None = None


def utc_z(moment: datetime) -> str:
    """An RFC 3339 UTC timestamp with ``Z``."""
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def set_active_corpus(version: str | None, ingest_finished_at: datetime | None) -> None:
    """Record the active corpus and when it was ingested from NCBI (None: no corpus)."""
    global _active_corpus
    ingested = utc_z(ingest_finished_at) if ingest_finished_at else None
    _active_corpus = (version, ingested) if version else None


def provenance(meta: dict[str, Any]) -> dict[str, str]:
    """``data_version`` and ``retrieved_at`` from a route's own ``_meta`` block."""
    corpus_version = meta.get("corpus_version")
    # A live NCBI fetch: stamped by the service (kept through its cache), or by the
    # route as ``live:<time>``.
    fetched_at = meta.get("retrieved_at")
    if fetched_at is None and isinstance(corpus_version, str):
        fetched_at = corpus_version.removeprefix("live:") if corpus_version[:5] == "live:" else None
    if fetched_at is not None:
        return {"data_version": "live", "retrieved_at": utc_z(datetime.fromisoformat(fetched_at))}
    # Corpus data. A result with no version of its own (the licence notice) describes
    # the active corpus; with no corpus at all it is dated by this response.
    active_version, ingested_at = _active_corpus or ("live", None)
    return {
        "data_version": corpus_version or active_version,
        "retrieved_at": ingested_at or utc_z(datetime.now(UTC)),
    }
