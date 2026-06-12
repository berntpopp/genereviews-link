"""Staleness helpers for chapter-level freshness signals.

Pure, stateless functions used at response time by the
``get_chapter_metadata`` route.  No database or I/O.

All helpers are module-level and fully unit-testable without a running server.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date
from typing import Literal, Protocol

StalenessBand = Literal["current", "aging", "stale", "very_stale"]

_DAYS_PER_YEAR: float = 365.25


class _SectionLike(Protocol):
    """Structural protocol for objects with ``section`` and ``passage_count``."""

    @property
    def section(self) -> str: ...

    @property
    def passage_count(self) -> int: ...


def years_since(last_updated: date | None, today: date) -> float | None:
    """Return the number of years between *last_updated* and *today*.

    Returns ``None`` when *last_updated* is ``None``.
    Result is rounded to one decimal place.
    """
    if last_updated is None:
        return None
    days = (today - last_updated).days
    return round(days / _DAYS_PER_YEAR, 1)


def staleness_band(years: float | None) -> StalenessBand | None:
    """Bucket *years* into a staleness category.

    Thresholds:

    | Band        | Range (years since last update) |
    |-------------|----------------------------------|
    | current     | < 2.0                            |
    | aging       | 2.0 - <4.0                       |
    | stale       | 4.0 - <7.0                       |
    | very_stale  | >= 7.0                           |

    Returns ``None`` when *years* is ``None``.
    """
    if years is None:
        return None
    if years < 2.0:
        return "current"
    if years < 4.0:
        return "aging"
    if years < 7.0:
        return "stale"
    return "very_stale"


def likely_stale_for_therapeutics(
    band: StalenessBand | None,
    sections: Iterable[_SectionLike],
) -> bool:
    """Heuristic: is this chapter likely stale for therapeutic recommendations?

    Returns ``True`` when *band* is ``"stale"`` or ``"very_stale"`` AND the
    chapter has a ``management`` section with at least one passage.

    This is a heuristic signal, NOT a substitute for primary-literature
    follow-up.  Always verify against current literature.
    """
    if band not in ("stale", "very_stale"):
        return False
    for section in sections:
        if section.section == "management" and section.passage_count > 0:
            return True
    return False
