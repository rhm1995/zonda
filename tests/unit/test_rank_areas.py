"""TASK-005 unit tests: `rank_areas`/`compare_areas`, against the real
bundled repository (spot-check figures) and small hand-authored fixtures
for coverage/exclusion/tie-break/one-call-per-question behaviour."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from core.errors import InvalidRangeError
from core.models import Period
from core.repository import Repository
from core.tools import MAX_TOP_N, compare_areas, rank_areas

MANCHESTER = "E08000003"
PERIOD_SEP_2025 = Period(label="Year ending Sep 2025", end_date=date(2025, 9, 30))
PERIOD_SEP_2015 = Period(label="Year ending Sep 2015", end_date=date(2015, 9, 30))

# A handful of other real, known local authorities to rank alongside
# Manchester (design §6.1's confirmed geography).
BIRMINGHAM = "E08000025"
LEEDS = "E08000035"
LIVERPOOL = "E08000012"
BRISTOL = "E06000023"


# -- Against the real bundled snapshot (confirmed §6.1 spot-check) ---------


def test_rank_areas_places_manchester_correctly_by_premium_pct(real_repository: Repository) -> None:
    result = rank_areas(
        real_repository,
        metric="premium_pct",
        period_or_range=PERIOD_SEP_2025,
        scope=[MANCHESTER, BIRMINGHAM, LEEDS, LIVERPOOL, BRISTOL],
        top_n=5,
        direction="top",
    )
    manchester_row = next(r for r in result.rows if r.la_code == MANCHESTER)
    assert manchester_row.value == pytest.approx(23.75)
    # Ranking is a true ordering of the known figures, not an arbitrary one.
    assert result.rows == sorted(result.rows, key=lambda r: r.value, reverse=True)


def test_rank_areas_by_premium_change_differs_materially_from_by_level(real_repository: Repository) -> None:
    scope = [MANCHESTER, BIRMINGHAM, LEEDS, LIVERPOOL, BRISTOL]
    by_level = rank_areas(
        real_repository,
        metric="premium_pct",
        period_or_range=PERIOD_SEP_2025,
        scope=scope,
        top_n=5,
        direction="top",
    )
    by_change = rank_areas(
        real_repository,
        metric="premium_percentage_point_change",
        period_or_range=(PERIOD_SEP_2015, PERIOD_SEP_2025),
        scope=scope,
        top_n=5,
        direction="top",
    )
    assert [r.la_code for r in by_level.rows] != [r.la_code for r in by_change.rows]


def test_top_n_bounds_the_result_and_direction_orders_it(real_repository: Repository) -> None:
    result = rank_areas(
        real_repository,
        metric="price",
        period_or_range=PERIOD_SEP_2025,
        scope="all",
        top_n=5,
        direction="top",
        dataset="new_build",
    )
    assert len(result.rows) == 5
    assert [r.rank for r in result.rows] == [1, 2, 3, 4, 5]
    assert result.rows == sorted(result.rows, key=lambda r: r.value, reverse=True)
    assert result.coverage.areas_in_scope == 318


def test_top_n_out_of_bounds_is_rejected(real_repository: Repository) -> None:
    with pytest.raises(InvalidRangeError):
        rank_areas(
            real_repository,
            metric="price",
            period_or_range=PERIOD_SEP_2025,
            scope="all",
            top_n=MAX_TOP_N + 1,
            direction="top",
        )
    with pytest.raises(InvalidRangeError):
        rank_areas(
            real_repository,
            metric="price",
            period_or_range=PERIOD_SEP_2025,
            scope="all",
            top_n=0,
            direction="top",
        )


def test_wrong_period_shape_for_metric_is_rejected(real_repository: Repository) -> None:
    with pytest.raises(InvalidRangeError):
        rank_areas(
            real_repository,
            metric="price",
            period_or_range=(PERIOD_SEP_2015, PERIOD_SEP_2025),  # price is a level metric
            scope="all",
            top_n=5,
            direction="top",
        )
    with pytest.raises(InvalidRangeError):
        rank_areas(
            real_repository,
            metric="growth_pct",
            period_or_range=PERIOD_SEP_2025,  # growth_pct is a range metric
            scope="all",
            top_n=5,
            direction="top",
        )


def test_compare_areas_matches_manchester_spot_check(real_repository: Repository) -> None:
    result = compare_areas(
        real_repository,
        areas=[MANCHESTER, BIRMINGHAM],
        metric="premium_gbp",
        period_or_range=PERIOD_SEP_2025,
    )
    manchester_row = next(r for r in result.areas if r.la_code == MANCHESTER)
    assert manchester_row.value == 95000
    assert len(result.areas) == 2  # both requested areas present, unordered


def test_repeated_ranking_calls_are_identical(real_repository: Repository) -> None:
    kwargs = dict(
        metric="cagr_pct",
        period_or_range=(PERIOD_SEP_2015, PERIOD_SEP_2025),
        scope=[MANCHESTER, BIRMINGHAM, LEEDS],
        top_n=3,
        direction="top",
        dataset="new_build",
    )
    first = rank_areas(real_repository, **kwargs)
    second = rank_areas(real_repository, **kwargs)
    assert first == second


# -- Fixture-based coverage/exclusion/tie-break tests -----------------------

AREA_A = {"la_code": "E06000001", "la_name": "Area A", "region_country_code": "E12000001", "region_country_name": "Test Region"}
AREA_B = {"la_code": "E06000002", "la_name": "Area B", "region_country_code": "E12000001", "region_country_name": "Test Region"}
AREA_C = {"la_code": "E06000003", "la_name": "Area C (no data)", "region_country_code": "E12000001", "region_country_name": "Test Region"}
PERIOD = Period(label="Year ending Sep 2020", end_date=date(2020, 9, 30))


def _row(area: dict, price: int | None, suppressed: bool = False) -> dict:
    return {
        "dataset": "new_build",
        "region_country_code": area["region_country_code"],
        "region_country_name": area["region_country_name"],
        "la_code": area["la_code"],
        "la_name": area["la_name"],
        "period_label": PERIOD.label,
        "period_end_date": PERIOD.end_date,
        "price_gbp": price,
        "suppressed": suppressed,
    }


@pytest.fixture
def coverage_repository(tmp_path: Path) -> Repository:
    """Area A and B have usable prices; Area C is suppressed at this
    period -- it must be excluded from `rows`/`areas` order but counted
    (never silently dropped) via `coverage`."""
    out_dir = tmp_path / "processed"
    out_dir.mkdir(parents=True)
    prices = [
        _row(AREA_A, 100000),
        _row(AREA_B, 100000),  # tie with Area A, broken by la_code ascending
        _row(AREA_C, None, suppressed=True),
    ]
    frame = pd.DataFrame(prices)
    frame["price_gbp"] = frame["price_gbp"].astype("Int64")
    frame.to_parquet(out_dir / "detached_house_prices.parquet", index=False)
    pd.DataFrame([{**AREA_A, "aliases": []}, {**AREA_B, "aliases": []}, {**AREA_C, "aliases": []}]).to_parquet(
        out_dir / "geography_reference.parquet", index=False
    )
    (out_dir / "BUILD_INFO.json").write_text(
        json.dumps(
            {
                "source": {},
                "edition": "fixture",
                "build_timestamp": "2026-01-01T00:00:00+00:00",
                "counts": {"local_authorities": 3, "periods": 1, "price_points": len(prices)},
            }
        )
    )
    return Repository.open(out_dir)


def test_rank_areas_excludes_suppressed_area_but_counts_it_in_coverage(
    coverage_repository: Repository,
) -> None:
    result = rank_areas(
        coverage_repository,
        metric="price",
        period_or_range=PERIOD,
        scope="all",
        top_n=5,
        direction="top",
    )
    assert {r.la_code for r in result.rows} == {"E06000001", "E06000002"}
    assert result.coverage.areas_in_scope == 3
    assert result.coverage.areas_ranked == 2
    assert result.coverage.areas_excluded == 1
    assert result.coverage.excluded_examples == ["Area C (no data)"]


def test_rank_areas_tie_break_is_deterministic_by_la_code(coverage_repository: Repository) -> None:
    result = rank_areas(
        coverage_repository,
        metric="price",
        period_or_range=PERIOD,
        scope="all",
        top_n=5,
        direction="top",
    )
    assert [r.la_code for r in result.rows] == ["E06000001", "E06000002"]  # ascending on tie


def test_rank_areas_excluded_examples_capped_at_five(tmp_path: Path) -> None:
    out_dir = tmp_path / "processed"
    out_dir.mkdir(parents=True)
    areas = [
        {"la_code": f"E0600000{i}", "la_name": f"Suppressed {i}", "region_country_code": "E12000001", "region_country_name": "Test Region"}
        for i in range(7)
    ]
    prices = [_row(a, None, suppressed=True) for a in areas]
    frame = pd.DataFrame(prices)
    frame["price_gbp"] = frame["price_gbp"].astype("Int64")
    frame.to_parquet(out_dir / "detached_house_prices.parquet", index=False)
    pd.DataFrame([{**a, "aliases": []} for a in areas]).to_parquet(
        out_dir / "geography_reference.parquet", index=False
    )
    (out_dir / "BUILD_INFO.json").write_text(
        json.dumps(
            {
                "source": {},
                "edition": "fixture",
                "build_timestamp": "2026-01-01T00:00:00+00:00",
                "counts": {"local_authorities": 7, "periods": 1, "price_points": len(prices)},
            }
        )
    )
    repository = Repository.open(out_dir)
    result = rank_areas(repository, metric="price", period_or_range=PERIOD, scope="all", top_n=5, direction="top")
    assert result.coverage.areas_excluded == 7
    assert len(result.coverage.excluded_examples) == 5


def test_compare_areas_includes_suppressed_area_flagged_not_dropped(
    coverage_repository: Repository,
) -> None:
    result = compare_areas(
        coverage_repository,
        areas=["E06000001", "E06000003"],
        metric="price",
        period_or_range=PERIOD,
    )
    assert len(result.areas) == 2  # Area C (suppressed) is NOT dropped
    suppressed_row = next(r for r in result.areas if r.la_code == "E06000003")
    assert suppressed_row.suppressed is True
    assert suppressed_row.value is None
