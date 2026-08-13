"""TASK-003/TASK-004 unit tests: pure formulas in core/metrics.py against
hand-computed values (ASM-010/ASM-003)."""

from __future__ import annotations

from datetime import date

import pytest

from core.metrics import (
    SUPPRESSION_MESSAGE,
    cagr_pct,
    growth_gbp,
    growth_pct,
    premium_gbp,
    premium_label,
    premium_pct,
    years_between,
)


def test_growth_gbp_is_end_minus_start() -> None:
    assert growth_gbp(177995, 495000) == 495000 - 177995


def test_growth_pct_matches_hand_computed_value() -> None:
    result = growth_pct(177995, 495000)
    expected = (495000 - 177995) / 177995 * 100
    assert result == pytest.approx(expected)


def test_years_between_uses_exact_elapsed_time_not_rounded_integer() -> None:
    years = years_between(date(2015, 9, 30), date(2025, 9, 30))
    # ~10 years, but not exactly 10.0 due to leap-year day-count -- proves
    # this isn't a rounded integer subtraction.
    assert 9.99 < years < 10.01


def test_cagr_pct_matches_hand_computed_value() -> None:
    years = years_between(date(2015, 9, 30), date(2025, 9, 30))
    result = cagr_pct(177995, 495000, years)
    expected = ((495000 / 177995) ** (1 / years) - 1) * 100
    assert result == pytest.approx(expected)


def test_premium_pct_matches_manchester_spot_check() -> None:
    # Manchester, "Year ending Sep 2025": new-build 495000, existing 400000.
    assert premium_pct(495000, 400000) == pytest.approx(23.75)


def test_premium_gbp_matches_manchester_spot_check() -> None:
    assert premium_gbp(495000, 400000) == 95000


def test_premium_label_negative_is_discount() -> None:
    assert premium_label(-5.0) == "discount"


def test_premium_label_positive_is_premium() -> None:
    assert premium_label(5.0) == "premium"


def test_premium_label_none_stays_none() -> None:
    assert premium_label(None) is None


def test_suppression_message_is_the_stakeholder_specified_wording() -> None:
    assert SUPPRESSION_MESSAGE == "ONS does not report a value for this area and period."
