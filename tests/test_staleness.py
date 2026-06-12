"""Unit tests for genereview_link.models.staleness helpers.

Tests cover:
- years_since boundary values and None propagation
- staleness_band bucket thresholds (boundary at 2.0, 4.0, 7.0)
- likely_stale_for_therapeutics flag logic
"""

from __future__ import annotations

from datetime import date
from typing import NamedTuple

from genereview_link.models.staleness import (
    likely_stale_for_therapeutics,
    staleness_band,
    years_since,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _Section(NamedTuple):
    """Minimal section-like object for testing."""

    section: str
    passage_count: int


# ---------------------------------------------------------------------------
# years_since
# ---------------------------------------------------------------------------


def test_years_since_none_date_returns_none() -> None:
    assert years_since(None, date(2026, 6, 12)) is None


def test_years_since_zero_days() -> None:
    today = date(2026, 6, 12)
    assert years_since(today, today) == 0.0


def test_years_since_one_year_rounded() -> None:
    # 365 days / 365.25 ~ 1.0 (rounds to 1.0)
    last_updated = date(2025, 6, 12)
    today = date(2026, 6, 12)
    result = years_since(last_updated, today)
    assert result is not None
    assert abs(result - 1.0) < 0.05  # within 0.05 years


def test_years_since_returns_float_rounded_to_one_decimal() -> None:
    last_updated = date(2024, 1, 1)
    today = date(2026, 6, 12)
    result = years_since(last_updated, today)
    assert result is not None
    # Check it is rounded to 1 decimal
    assert result == round(result, 1)


# ---------------------------------------------------------------------------
# staleness_band boundaries
# ---------------------------------------------------------------------------


def test_staleness_band_none_returns_none() -> None:
    assert staleness_band(None) is None


def test_staleness_band_1_9_years_is_current() -> None:
    assert staleness_band(1.9) == "current"


def test_staleness_band_0_0_years_is_current() -> None:
    assert staleness_band(0.0) == "current"


def test_staleness_band_2_0_years_is_aging() -> None:
    assert staleness_band(2.0) == "aging"


def test_staleness_band_3_9_years_is_aging() -> None:
    assert staleness_band(3.9) == "aging"


def test_staleness_band_4_0_years_is_stale() -> None:
    assert staleness_band(4.0) == "stale"


def test_staleness_band_6_9_years_is_stale() -> None:
    assert staleness_band(6.9) == "stale"


def test_staleness_band_7_0_years_is_very_stale() -> None:
    assert staleness_band(7.0) == "very_stale"


def test_staleness_band_10_years_is_very_stale() -> None:
    assert staleness_band(10.0) == "very_stale"


# ---------------------------------------------------------------------------
# likely_stale_for_therapeutics
# ---------------------------------------------------------------------------


def test_likely_stale_for_therapeutics_none_band_is_false() -> None:
    sections = [_Section("management", 5)]
    assert likely_stale_for_therapeutics(None, sections) is False


def test_likely_stale_for_therapeutics_current_band_is_false() -> None:
    sections = [_Section("management", 5)]
    assert likely_stale_for_therapeutics("current", sections) is False


def test_likely_stale_for_therapeutics_aging_band_is_false() -> None:
    sections = [_Section("management", 5)]
    assert likely_stale_for_therapeutics("aging", sections) is False


def test_likely_stale_for_therapeutics_stale_with_management_is_true() -> None:
    sections = [_Section("summary", 3), _Section("management", 5)]
    assert likely_stale_for_therapeutics("stale", sections) is True


def test_likely_stale_for_therapeutics_very_stale_with_management_is_true() -> None:
    sections = [_Section("management", 1)]
    assert likely_stale_for_therapeutics("very_stale", sections) is True


def test_likely_stale_for_therapeutics_stale_no_management_section_is_false() -> None:
    sections = [_Section("summary", 3), _Section("diagnosis", 4)]
    assert likely_stale_for_therapeutics("stale", sections) is False


def test_likely_stale_for_therapeutics_stale_management_zero_passages_is_false() -> None:
    """Management section present but empty (passage_count=0) must NOT trigger True."""
    sections = [_Section("management", 0), _Section("summary", 2)]
    assert likely_stale_for_therapeutics("stale", sections) is False


def test_likely_stale_for_therapeutics_empty_sections_is_false() -> None:
    assert likely_stale_for_therapeutics("very_stale", []) is False
