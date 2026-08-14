"""TASK-019 unit tests: the period resolver (CMP-018), against a small
hand-authored fixture period list -- per the design's explicit testing
note, so "the dataset's actual latest available period" is deterministic
and known in the test, not dependent on the real bundled snapshot."""

from __future__ import annotations

from datetime import date

import pytest

from core.models import Period
from core.period import resolve_period

_QUARTER_ENDS = [(3, 31), (6, 30), (9, 30), (12, 31)]
_QUARTER_ABBR = {3: "Mar", 6: "Jun", 9: "Sep", 12: "Dec"}


def _build_fixture_periods(start_year: int, end_year: int, end_month: int) -> list[Period]:
    """Every quarter-end from `start_year`-Mar through `end_year`-`end_month`
    inclusive, mirroring the real dataset's contiguous quarterly series."""
    periods: list[Period] = []
    for year in range(start_year, end_year + 1):
        for month, day in _QUARTER_ENDS:
            if year == end_year and month > end_month:
                break
            periods.append(Period(label=f"Year ending {_QUARTER_ABBR[month]} {year}", end_date=date(year, month, day)))
    return periods


class _FixturePeriodRepository:
    def __init__(self, periods: list[Period]) -> None:
        self._periods = periods

    def get_period_reference(self) -> list[Period]:
        return self._periods


# Fixture dataset: "Year ending Mar 2010" .. "Year ending Sep 2025" -- same
# span shape as the real bundled data, small and deterministic.
_FIXTURE = _FixturePeriodRepository(_build_fixture_periods(2010, 2025, 9))
_LATEST_LABEL = "Year ending Sep 2025"


def test_month_and_year_resolves_exactly() -> None:
    result = resolve_period(_FIXTURE, "September 2025")
    assert result.status == "resolved"
    assert result.period is not None
    assert result.period.label == _LATEST_LABEL


def test_exact_ons_label_also_resolves() -> None:
    """TASK-009: a tool downstream of resolve_period must accept the exact
    label the model already saw from a prior resolution (e.g. echoing back
    "Year ending Sep 2015" from an earlier `period_range` result)."""
    result = resolve_period(_FIXTURE, "Year ending Sep 2015")
    assert result.status == "resolved"
    assert result.period is not None and result.period.label == "Year ending Sep 2015"


def test_month_abbreviation_and_numeral_also_resolve() -> None:
    for month_text in ("Sep", "sep", "9", "09"):
        result = resolve_period(_FIXTURE, f"{month_text} 2025")
        assert result.status == "resolved", month_text
        assert result.period is not None and result.period.label == _LATEST_LABEL


def test_bare_year_resolves_with_an_explicit_assumption() -> None:
    result = resolve_period(_FIXTURE, "2015")
    assert result.status == "resolved_with_assumption"
    assert result.period is not None
    assert result.period.label == "Year ending Sep 2015"
    assert result.assumption_note
    assert "September 2015" in result.assumption_note


def test_since_a_bare_year_resolves_a_range_to_the_datasets_actual_latest() -> None:
    result = resolve_period(_FIXTURE, "since 2015")
    assert result.status == "range_resolved"
    assert result.period_range is not None
    start, end = result.period_range
    assert start.label == "Year ending Sep 2015"
    assert end.label == _LATEST_LABEL
    # The bare-year assumption made while resolving the start of the range
    # must still surface -- never applied silently.
    assert result.assumption_note is not None and "September 2015" in result.assumption_note


def test_since_a_month_and_year_resolves_a_range_with_no_assumption() -> None:
    result = resolve_period(_FIXTURE, "since March 2015")
    assert result.status == "range_resolved"
    assert result.period_range is not None
    assert result.period_range[0].label == "Year ending Mar 2015"
    assert result.assumption_note is None


def test_last_five_years_ends_at_latest_and_starts_exactly_five_years_before() -> None:
    result = resolve_period(_FIXTURE, "last five years")
    assert result.status == "range_resolved"
    assert result.period_range is not None
    start, end = result.period_range
    assert end.label == _LATEST_LABEL
    assert start.label == "Year ending Sep 2020"


def test_last_decade_ends_at_latest_and_starts_exactly_ten_years_before() -> None:
    result = resolve_period(_FIXTURE, "last decade")
    assert result.status == "range_resolved"
    assert result.period_range is not None
    start, end = result.period_range
    assert end.label == _LATEST_LABEL
    assert start.label == "Year ending Sep 2015"


def test_last_n_years_accepts_a_numeral_too() -> None:
    result = resolve_period(_FIXTURE, "last 3 years")
    assert result.status == "range_resolved"
    assert result.period_range is not None
    assert result.period_range[0].label == "Year ending Sep 2022"


def test_out_of_range_expression_offers_nearest_available_suggestions() -> None:
    result = resolve_period(_FIXTURE, "September 2030")
    assert result.status == "out_of_range"
    assert result.period is None
    assert len(result.suggestions) >= 1
    assert result.suggestions[0].label == _LATEST_LABEL  # nearest to a date beyond the dataset's end


def test_off_convention_month_is_out_of_range_not_a_guess() -> None:
    """"January" is a real calendar month but never a quarter-end in this
    dataset's convention -- must not be silently rounded to March."""
    result = resolve_period(_FIXTURE, "January 2020")
    assert result.status == "out_of_range"
    assert result.period is None


def test_malformed_expression_is_not_found_never_raises() -> None:
    result = resolve_period(_FIXTURE, "asdlkfj not a period")
    assert result.status == "not_found"
    assert result.period is None
    assert result.period_range is None


def test_since_an_unparseable_expression_is_not_found() -> None:
    result = resolve_period(_FIXTURE, "since forever")
    assert result.status == "not_found"


@pytest.mark.parametrize("garbage", ["", "   ", "!!!", "since", "last years"])
def test_resolver_never_raises_for_any_garbage_input(garbage: str) -> None:
    result = resolve_period(_FIXTURE, garbage)
    assert result.status in ("not_found", "out_of_range")
