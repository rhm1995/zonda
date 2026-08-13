"""STORY-001 component tests: `ui/explore_trends.py`'s pure rendering-data
helpers, against `core/tools.py`-shaped fixture values -- no live
Streamlit runtime required."""

from __future__ import annotations

from core.models import GrowthMetricsResult, PremiumResult, PremiumSeriesResult, TrendResult
from ui.explore_trends import (
    _format_gbp,
    _format_pct,
    _latest_non_suppressed_premium_point,
    _premium_chart_series,
    _price_chart_series,
)

MANCHESTER_TREND = TrendResult(
    la_code="E08000003",
    la_name="Manchester",
    dataset="new_build",
    period_start_label="Year ending Sep 2015",
    period_end_label="Year ending Sep 2025",
    points=[
        {
            "dataset": "new_build",
            "region_country_code": "E12000002",
            "region_country_name": "North West",
            "la_code": "E08000003",
            "la_name": "Manchester",
            "period_label": "Year ending Sep 2015",
            "period_end_date": "2015-09-30",
            "price_gbp": 177995,
            "suppressed": False,
        },
        {
            "dataset": "new_build",
            "region_country_code": "E12000002",
            "region_country_name": "North West",
            "la_code": "E08000003",
            "la_name": "Manchester",
            "period_label": "Year ending Dec 2015",
            "period_end_date": "2015-12-31",
            "price_gbp": None,
            "suppressed": True,
        },
        {
            "dataset": "new_build",
            "region_country_code": "E12000002",
            "region_country_name": "North West",
            "la_code": "E08000003",
            "la_name": "Manchester",
            "period_label": "Year ending Sep 2025",
            "period_end_date": "2025-09-30",
            "price_gbp": 495000,
            "suppressed": False,
        },
    ],
    suppressed_periods=["Year ending Dec 2015"],
)


def test_price_chart_series_reports_a_none_gap_for_suppressed_point() -> None:
    labels, values = _price_chart_series(MANCHESTER_TREND)
    assert labels == ["Year ending Sep 2015", "Year ending Dec 2015", "Year ending Sep 2025"]
    assert values == [177995, None, 495000]  # None, never 0, for the suppressed point


def test_format_gbp_and_pct_render_none_as_em_dash_not_zero() -> None:
    assert _format_gbp(None) == "—"
    assert _format_pct(None) == "—"
    assert _format_gbp(495000) == "£495,000"
    assert _format_pct(23.75) == "23.75%"


PREMIUM_SERIES = PremiumSeriesResult(
    la_code="E08000003",
    la_name="Manchester",
    period_start_label="Year ending Sep 2015",
    period_end_label="Year ending Sep 2025",
    points=[
        PremiumResult(
            la_code="E08000003",
            la_name="Manchester",
            period_label="Year ending Sep 2015",
            new_build_price=177995,
            existing_price=220000,
            premium_pct=(177995 - 220000) / 220000 * 100,
            premium_gbp=177995 - 220000,
            suppressed_components=[],
        ),
        PremiumResult(
            la_code="E08000003",
            la_name="Manchester",
            period_label="Year ending Dec 2015",
            new_build_price=None,
            existing_price=221000,
            premium_pct=None,
            premium_gbp=None,
            suppressed_components=["new_build"],
        ),
        PremiumResult(
            la_code="E08000003",
            la_name="Manchester",
            period_label="Year ending Sep 2025",
            new_build_price=495000,
            existing_price=400000,
            premium_pct=23.75,
            premium_gbp=95000,
            suppressed_components=[],
        ),
    ],
)


def test_premium_chart_series_pct_unit_labels_negative_as_discount() -> None:
    labels, values, point_labels = _premium_chart_series(PREMIUM_SERIES, "%")
    assert labels == ["Year ending Sep 2015", "Year ending Dec 2015", "Year ending Sep 2025"]
    assert values[0] < 0  # Manchester 2015: new-build cheaper than existing
    assert point_labels[0] == "discount"
    assert point_labels[1] is None  # suppressed point has no label to derive
    assert point_labels[2] == "premium"


def test_premium_chart_series_gbp_unit_switches_values_without_changing_labels() -> None:
    _, pct_values, pct_labels = _premium_chart_series(PREMIUM_SERIES, "%")
    _, gbp_values, gbp_labels = _premium_chart_series(PREMIUM_SERIES, "£")
    assert gbp_values[0] == 177995 - 220000
    assert gbp_values[2] == 95000
    assert pct_labels == gbp_labels  # discount/premium labelling is unit-independent


def test_premium_chart_series_suppressed_point_is_none_not_zero() -> None:
    _, values, _ = _premium_chart_series(PREMIUM_SERIES, "%")
    assert values[1] is None


def test_latest_non_suppressed_premium_point_skips_trailing_suppressed_points() -> None:
    series_with_trailing_gap = PREMIUM_SERIES.model_copy(
        update={
            "points": PREMIUM_SERIES.points
            + [
                PremiumResult(
                    la_code="E08000003",
                    la_name="Manchester",
                    period_label="Year ending Dec 2025",
                    new_build_price=None,
                    existing_price=None,
                    premium_pct=None,
                    premium_gbp=None,
                    suppressed_components=["new_build", "existing"],
                )
            ]
        }
    )
    result = _latest_non_suppressed_premium_point(series_with_trailing_gap)
    assert result is not None
    label, pct, gbp = result
    assert label == "Year ending Sep 2025"
    assert pct == 23.75
    assert gbp == 95000


def test_latest_non_suppressed_premium_point_none_when_all_suppressed() -> None:
    all_suppressed = PremiumSeriesResult(
        la_code="X",
        la_name="X",
        period_start_label="a",
        period_end_label="b",
        points=[
            PremiumResult(
                la_code="X",
                la_name="X",
                period_label="p",
                new_build_price=None,
                existing_price=None,
                premium_pct=None,
                premium_gbp=None,
                suppressed_components=["new_build", "existing"],
            )
        ],
    )
    assert _latest_non_suppressed_premium_point(all_suppressed) is None
