"""TASK-003/TASK-004 unit tests: the shared analysis tool library, against
the real bundled repository (confirmed §6.1 spot-check figures) and small
hand-authored fixtures for suppressed-data/error-path cases."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from core.errors import InvalidRangeError, PeriodOutOfRangeError
from core.models import Period
from core.repository import Repository
from core.tools import (
    growth_metrics,
    median_price_lookup,
    new_build_premium,
    premium_series,
    premium_trend,
    price_trend,
)

MANCHESTER = "E08000003"
PERIOD_SEP_2025 = Period(label="Year ending Sep 2025", end_date=date(2025, 9, 30))
PERIOD_SEP_2015 = Period(label="Year ending Sep 2015", end_date=date(2015, 9, 30))


# -- Against the real bundled snapshot (confirmed §6.1 spot-checks) --------


def test_median_price_lookup_matches_manchester_spot_check(real_repository: Repository) -> None:
    result = median_price_lookup(real_repository, MANCHESTER, "new_build", PERIOD_SEP_2025)
    assert result.la_name == "Manchester"
    assert result.price_gbp == 495000
    assert result.suppressed is False


def test_growth_metrics_matches_manchester_spot_check(real_repository: Repository) -> None:
    result = growth_metrics(
        real_repository, MANCHESTER, "new_build", PERIOD_SEP_2015, PERIOD_SEP_2025
    )
    assert result.latest_price == 495000
    assert result.latest_price_period == "Year ending Sep 2025"
    assert result.growth_gbp == pytest.approx(495000 - 177995)
    assert result.growth_pct == pytest.approx((495000 - 177995) / 177995 * 100)
    expected_years = (date(2025, 9, 30) - date(2015, 9, 30)).days / 365.25
    expected_cagr = ((495000 / 177995) ** (1 / expected_years) - 1) * 100
    assert result.cagr_pct == pytest.approx(expected_cagr)


def test_price_trend_returns_full_series_for_manchester(real_repository: Repository) -> None:
    result = price_trend(real_repository, MANCHESTER, "new_build", PERIOD_SEP_2015, PERIOD_SEP_2025)
    assert result.points[0].period_label == "Year ending Sep 2015"
    assert result.points[-1].period_label == "Year ending Sep 2025"
    assert len(result.points) == 41  # quarterly, Sep2015..Sep2025 inclusive


def test_new_build_premium_matches_manchester_spot_check(real_repository: Repository) -> None:
    result = new_build_premium(real_repository, MANCHESTER, PERIOD_SEP_2025)
    assert result.premium_gbp == 95000
    assert result.premium_pct == pytest.approx(23.75)
    assert result.suppressed_components == []


def test_premium_trend_matches_manchester_endpoints_and_change(real_repository: Repository) -> None:
    result = premium_trend(real_repository, MANCHESTER, PERIOD_SEP_2015, PERIOD_SEP_2025)
    expected_start_pct = (177995 - 220000) / 220000 * 100
    expected_end_pct = 23.75
    assert result.start_premium_pct == pytest.approx(expected_start_pct)
    assert result.start_premium_gbp == 177995 - 220000
    assert result.end_premium_pct == pytest.approx(expected_end_pct)
    assert result.end_premium_gbp == 95000
    assert result.premium_percentage_point_change == pytest.approx(expected_end_pct - expected_start_pct)
    assert result.premium_gbp_change == pytest.approx((95000) - (177995 - 220000))


def test_premium_series_every_point_matches_new_build_premium_formula(real_repository: Repository) -> None:
    result = premium_series(real_repository, MANCHESTER, PERIOD_SEP_2015, PERIOD_SEP_2025)
    assert len(result.points) == 41
    assert result.points[0].period_label == "Year ending Sep 2015"
    assert result.points[-1].period_label == "Year ending Sep 2025"
    # Spot-check the endpoint premium values independently, per-point.
    end_point = result.points[-1]
    assert end_point.premium_gbp == 95000
    assert end_point.premium_pct == pytest.approx(23.75)
    for point in result.points:
        if point.new_build_price is not None and point.existing_price is not None:
            expected_pct = (point.new_build_price - point.existing_price) / point.existing_price * 100
            assert point.premium_pct == pytest.approx(expected_pct)


def test_invalid_range_raises_typed_error(real_repository: Repository) -> None:
    with pytest.raises(InvalidRangeError):
        growth_metrics(real_repository, MANCHESTER, "new_build", PERIOD_SEP_2025, PERIOD_SEP_2015)


def test_period_out_of_range_raises_typed_error(real_repository: Repository) -> None:
    far_future = Period(label="Year ending Sep 2099", end_date=date(2099, 9, 30))
    with pytest.raises(PeriodOutOfRangeError):
        median_price_lookup(real_repository, MANCHESTER, "new_build", far_future)


def test_repeated_calls_are_numerically_identical(real_repository: Repository) -> None:
    first = growth_metrics(real_repository, MANCHESTER, "new_build", PERIOD_SEP_2015, PERIOD_SEP_2025)
    second = growth_metrics(real_repository, MANCHESTER, "new_build", PERIOD_SEP_2015, PERIOD_SEP_2025)
    assert first == second


# -- Suppressed-data cases, against a small hand-authored fixture ----------


AREA = {
    "la_code": "E06000001",
    "la_name": "Area A",
    "region_country_code": "E12000001",
    "region_country_name": "Test Region",
}
P1 = Period(label="Year ending Mar 2020", end_date=date(2020, 3, 31))
P2 = Period(label="Year ending Jun 2020", end_date=date(2020, 6, 30))
P3 = Period(label="Year ending Sep 2020", end_date=date(2020, 9, 30))


def _row(dataset: str, period: Period, price: int | None, suppressed: bool = False) -> dict:
    return {
        "dataset": dataset,
        "region_country_code": AREA["region_country_code"],
        "region_country_name": AREA["region_country_name"],
        "la_code": AREA["la_code"],
        "la_name": AREA["la_name"],
        "period_label": period.label,
        "period_end_date": period.end_date,
        "price_gbp": price,
        "suppressed": suppressed,
    }


@pytest.fixture
def suppressed_repository(tmp_path: Path) -> Repository:
    out_dir = tmp_path / "processed"
    out_dir.mkdir(parents=True)
    prices = [
        _row("new_build", P1, 100000),
        _row("new_build", P2, None, suppressed=True),
        _row("new_build", P3, 120000),
        _row("existing", P1, 90000),
        _row("existing", P2, 95000),
        _row("existing", P3, None, suppressed=True),
    ]
    frame = pd.DataFrame(prices)
    frame["price_gbp"] = frame["price_gbp"].astype("Int64")
    frame.to_parquet(out_dir / "detached_house_prices.parquet", index=False)
    pd.DataFrame([{**AREA, "aliases": []}]).to_parquet(
        out_dir / "geography_reference.parquet", index=False
    )
    import json

    (out_dir / "BUILD_INFO.json").write_text(
        json.dumps(
            {
                "source": {},
                "edition": "fixture",
                "build_timestamp": "2026-01-01T00:00:00+00:00",
                "counts": {"local_authorities": 1, "periods": 3, "price_points": len(prices)},
            }
        )
    )
    return Repository.open(out_dir)


def test_growth_metrics_surfaces_suppressed_middle_period_without_using_it_as_latest(
    suppressed_repository: Repository,
) -> None:
    result = growth_metrics(suppressed_repository, AREA["la_code"], "new_build", P1, P3)
    assert "Year ending Jun 2020" in result.suppressed_periods
    # The suppressed middle period must never be reported as the latest
    # price -- the real (non-suppressed) endpoint, P3, must be.
    assert result.latest_price == 120000
    assert result.latest_price_period == "Year ending Sep 2020"


def test_growth_metrics_returns_none_when_an_endpoint_itself_is_suppressed(
    suppressed_repository: Repository,
) -> None:
    result = growth_metrics(suppressed_repository, AREA["la_code"], "existing", P1, P3)
    assert result.growth_gbp is None
    assert result.growth_pct is None
    assert result.cagr_pct is None
    assert "Year ending Sep 2020" in result.suppressed_periods


def test_new_build_premium_flags_suppressed_component(suppressed_repository: Repository) -> None:
    result = new_build_premium(suppressed_repository, AREA["la_code"], P3)
    assert result.suppressed_components == ["existing"]
    assert result.premium_pct is None
    assert result.premium_gbp is None


def test_premium_series_flags_suppressed_point_without_interpolating(
    suppressed_repository: Repository,
) -> None:
    result = premium_series(suppressed_repository, AREA["la_code"], P1, P3)
    by_label = {p.period_label: p for p in result.points}
    assert by_label["Year ending Sep 2020"].suppressed_components == ["existing"]
    assert by_label["Year ending Sep 2020"].premium_pct is None
    assert by_label["Year ending Mar 2020"].premium_pct is not None
