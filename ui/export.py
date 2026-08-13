"""CSV export utility (TASK-007, design CMP-016, ADR-013).

Serialises an already-computed, already-displayed result object directly --
no parallel query or recomputation path. This is what makes DR-008/NFR-012
("the download matches what's on screen") hold by construction: the same
object renders and exports, so the two can never silently drift apart.

Uses the standard-library `csv` module, deliberately not Pandas -- CON-008
scopes Pandas to the offline ingestion pipeline only, keeping this runtime
path free of a Pandas dependency entirely.

The `RankingResult` branch (design §8.5), deferred from `TASK-007`
(Increment 1) to `TASK-005` (Increment 2) per that ticket's own explicitly
allowed sub-sequencing, is completed here now that `RankingResult` exists.
"""

from __future__ import annotations

import csv
import io

from core.models import GrowthMetricsResult, PremiumSeriesResult, RankingResult

_BLANK = ""  # a suppressed/missing value renders as blank, never as 0 (ADR-018)


def export(result: GrowthMetricsResult | PremiumSeriesResult | RankingResult) -> bytes:
    """A direct tabular projection of `result`'s own fields -- no rounding,
    unit conversion, or recomputation beyond what already produced the
    on-screen display. Pure function of its input: identical input always
    yields identical bytes (NFR-012)."""
    if isinstance(result, GrowthMetricsResult):
        rows = _growth_metrics_rows(result)
    elif isinstance(result, PremiumSeriesResult):
        rows = _premium_series_rows(result)
    elif isinstance(result, RankingResult):
        rows = _ranking_result_rows(result)
    else:
        raise TypeError(f"export() does not support {type(result).__name__}")

    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerows(rows)
    return buffer.getvalue().encode("utf-8")


def _growth_metrics_rows(result: GrowthMetricsResult) -> list[list[str]]:
    header = [
        "la_code",
        "la_name",
        "dataset",
        "period_start_label",
        "period_end_label",
        "latest_price",
        "latest_price_period",
        "growth_gbp",
        "growth_pct",
        "cagr_pct",
        "suppressed_periods",
    ]
    row = [
        result.la_code,
        result.la_name,
        result.dataset,
        result.period_start_label,
        result.period_end_label,
        _value(result.latest_price),
        result.latest_price_period or _BLANK,
        _value(result.growth_gbp),
        _value(result.growth_pct),
        _value(result.cagr_pct),
        "; ".join(result.suppressed_periods),
    ]
    return [header, row]


def _premium_series_rows(result: PremiumSeriesResult) -> list[list[str]]:
    header = [
        "la_code",
        "la_name",
        "period_start_label",
        "period_end_label",
        "period_label",
        "new_build_price",
        "existing_price",
        "premium_pct",
        "premium_gbp",
        "suppressed",
        "suppressed_components",
    ]
    rows = [header]
    for point in result.points:
        rows.append(
            [
                result.la_code,
                result.la_name,
                result.period_start_label,
                result.period_end_label,
                point.period_label,
                _value(point.new_build_price),
                _value(point.existing_price),
                _value(point.premium_pct),
                _value(point.premium_gbp),
                "true" if point.suppressed_components else "false",
                "; ".join(point.suppressed_components),
            ]
        )
    return rows


def _ranking_result_rows(result: RankingResult) -> list[list[str]]:
    header = [
        "rank",
        "la_code",
        "la_name",
        "metric",
        "period_label_or_range",
        "direction",
        "value",
        "suppressed",
    ]
    rows = [header]
    for row in result.rows:
        rows.append(
            [
                str(row.rank),
                row.la_code,
                row.la_name,
                result.metric,
                result.period_label_or_range,
                result.direction,
                _value(row.value),
                "true" if row.suppressed else "false",
            ]
        )
    return rows


def _value(value: float | int | None) -> str:
    return _BLANK if value is None else str(value)
