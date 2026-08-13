"""TASK-007 unit tests: CSV export round-trip fidelity, suppressed-value
marking, and repeat-export byte equality (DR-008/NFR-012)."""

from __future__ import annotations

import csv
import io

from core.models import (
    GrowthMetricsResult,
    PremiumResult,
    PremiumSeriesResult,
    RankedArea,
    RankingCoverageSummary,
    RankingResult,
)
from ui.export import export

GROWTH_RESULT_NO_SUPPRESSION = GrowthMetricsResult(
    la_code="E08000003",
    la_name="Manchester",
    dataset="new_build",
    period_start_label="Year ending Sep 2015",
    period_end_label="Year ending Sep 2025",
    latest_price=495000,
    latest_price_period="Year ending Sep 2025",
    growth_gbp=317005.0,
    growth_pct=178.09,
    cagr_pct=10.76,
    suppressed_periods=[],
)

GROWTH_RESULT_WITH_SUPPRESSION = GROWTH_RESULT_NO_SUPPRESSION.model_copy(
    update={"suppressed_periods": ["Year ending Jun 2020", "Year ending Sep 2020"]}
)


def _parse_csv(data: bytes) -> list[list[str]]:
    return list(csv.reader(io.StringIO(data.decode("utf-8"))))


def test_growth_metrics_round_trip_matches_every_field_exactly() -> None:
    csv_bytes = export(GROWTH_RESULT_NO_SUPPRESSION)
    rows = _parse_csv(csv_bytes)
    header, data_row = rows[0], rows[1]
    record = dict(zip(header, data_row))

    assert record["la_code"] == "E08000003"
    assert record["la_name"] == "Manchester"
    assert int(record["latest_price"]) == 495000
    assert float(record["growth_gbp"]) == 317005.0
    assert float(record["growth_pct"]) == 178.09
    assert float(record["cagr_pct"]) == 10.76
    assert record["suppressed_periods"] == ""


def test_growth_metrics_suppressed_periods_appear_not_omitted() -> None:
    csv_bytes = export(GROWTH_RESULT_WITH_SUPPRESSION)
    rows = _parse_csv(csv_bytes)
    record = dict(zip(rows[0], rows[1]))
    assert "Year ending Jun 2020" in record["suppressed_periods"]
    assert "Year ending Sep 2020" in record["suppressed_periods"]


def test_growth_metrics_none_growth_fields_render_blank_not_zero() -> None:
    result = GROWTH_RESULT_NO_SUPPRESSION.model_copy(
        update={"growth_gbp": None, "growth_pct": None, "cagr_pct": None}
    )
    csv_bytes = export(result)
    rows = _parse_csv(csv_bytes)
    record = dict(zip(rows[0], rows[1]))
    assert record["growth_gbp"] == ""
    assert record["growth_pct"] == ""
    assert record["cagr_pct"] == ""


def test_export_same_object_twice_is_byte_identical() -> None:
    first = export(GROWTH_RESULT_NO_SUPPRESSION)
    second = export(GROWTH_RESULT_NO_SUPPRESSION)
    assert first == second


PREMIUM_SERIES_RESULT = PremiumSeriesResult(
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
            premium_pct=-19.09,
            premium_gbp=-42005,
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


def test_premium_series_round_trip_matches_every_point() -> None:
    csv_bytes = export(PREMIUM_SERIES_RESULT)
    rows = _parse_csv(csv_bytes)
    assert len(rows) == 1 + 3  # header + 3 points
    header = rows[0]
    last_row = dict(zip(header, rows[-1]))
    assert last_row["period_label"] == "Year ending Sep 2025"
    assert int(last_row["premium_gbp"]) == 95000
    assert float(last_row["premium_pct"]) == 23.75


def test_premium_series_suppressed_point_included_with_flag_not_omitted() -> None:
    csv_bytes = export(PREMIUM_SERIES_RESULT)
    rows = _parse_csv(csv_bytes)
    header = rows[0]
    suppressed_row = dict(zip(header, rows[2]))  # "Year ending Dec 2015"
    assert suppressed_row["period_label"] == "Year ending Dec 2015"
    assert suppressed_row["suppressed"] == "true"
    assert suppressed_row["new_build_price"] == ""  # blank, never "0"
    assert suppressed_row["suppressed_components"] == "new_build"


def test_export_raises_no_exception_and_returns_pure_bytes() -> None:
    result = export(GROWTH_RESULT_NO_SUPPRESSION)
    assert isinstance(result, bytes)


RANKING_RESULT = RankingResult(
    metric="premium_pct",
    period_label_or_range="Year ending Sep 2025",
    direction="top",
    rows=[
        RankedArea(rank=1, la_code="E08000003", la_name="Manchester", value=23.75, suppressed=False),
        RankedArea(rank=2, la_code="E08000025", la_name="Birmingham", value=None, suppressed=True),
    ],
    coverage=RankingCoverageSummary(areas_in_scope=2, areas_ranked=1, areas_excluded=1, excluded_examples=["Birmingham"]),
)


def test_ranking_result_round_trip_matches_every_row() -> None:
    csv_bytes = export(RANKING_RESULT)
    rows = _parse_csv(csv_bytes)
    assert len(rows) == 1 + 2  # header + 2 rows
    header = rows[0]
    manchester = dict(zip(header, rows[1]))
    assert manchester["la_name"] == "Manchester"
    assert float(manchester["value"]) == 23.75
    assert manchester["suppressed"] == "false"


def test_ranking_result_suppressed_row_included_with_blank_value_not_zero() -> None:
    csv_bytes = export(RANKING_RESULT)
    rows = _parse_csv(csv_bytes)
    header = rows[0]
    birmingham = dict(zip(header, rows[2]))
    assert birmingham["suppressed"] == "true"
    assert birmingham["value"] == ""


def test_ranking_result_export_is_byte_identical_on_repeat() -> None:
    assert export(RANKING_RESULT) == export(RANKING_RESULT)
