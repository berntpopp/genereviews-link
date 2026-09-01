"""Unit tests for the `/health` corpus freshness reporting introduced for #145."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from genereview_link.corpus.freshness import corpus_health


def test_absent_state_reports_no_facts_and_is_not_stale() -> None:
    """An app assembled outside the normal lifespan (unit tests, embedded use) must not
    be misreported as a stale corpus just because the lifecycle never ran -- mirrors
    `embedding_health`'s "assume healthy on missing state" default."""
    result = corpus_health(SimpleNamespace(), max_age_days=90)
    assert result == {
        "version": None,
        "data_as_of": None,
        "age_days": None,
        "max_age_days": 90,
        "stale": False,
    }


def test_fresh_corpus_is_not_stale() -> None:
    state = SimpleNamespace(
        corpus_version="2026-08-15-r1",
        corpus_data_as_of=datetime(2026, 8, 15, tzinfo=UTC).isoformat(),
    )
    result = corpus_health(state, max_age_days=90, now=datetime(2026, 9, 2, tzinfo=UTC))
    assert result["version"] == "2026-08-15-r1"
    assert result["data_as_of"] == "2026-08-15T00:00:00+00:00"
    assert result["age_days"] == 18
    assert result["max_age_days"] == 90
    assert result["stale"] is False


def test_frozen_corpus_reports_stale_past_max_age() -> None:
    """The #145 scenario itself: frozen at 2026-05-12, discovered 2026-09-02 (~113 days)."""
    state = SimpleNamespace(
        corpus_version="2026-05-12-r1",
        corpus_data_as_of=datetime(2026, 5, 12, tzinfo=UTC).isoformat(),
    )
    result = corpus_health(state, max_age_days=90, now=datetime(2026, 9, 2, tzinfo=UTC))
    assert result["age_days"] > 90
    assert result["stale"] is True


def test_age_exactly_at_max_age_is_not_yet_stale() -> None:
    """The threshold is exclusive: exactly max_age_days old is the last healthy day."""
    state = SimpleNamespace(
        corpus_version="2026-06-04-r1",
        corpus_data_as_of=datetime(2026, 6, 4, tzinfo=UTC).isoformat(),
    )
    result = corpus_health(state, max_age_days=90, now=datetime(2026, 9, 2, tzinfo=UTC))
    assert result["age_days"] == 90
    assert result["stale"] is False


def test_version_without_data_as_of_is_not_fabricated_as_stale() -> None:
    """A version with no timestamp (defensive: should not happen per the DB invariant in
    corpus/pipeline.py) is reported honestly rather than guessed at either way."""
    state = SimpleNamespace(corpus_version="legacy", corpus_data_as_of=None)
    result = corpus_health(state, max_age_days=90)
    assert result["version"] == "legacy"
    assert result["age_days"] is None
    assert result["stale"] is False
