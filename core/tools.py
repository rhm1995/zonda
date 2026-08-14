"""The shared Analysis Tool Library (TASK-003/TASK-004, design CMP-004,
ADR-001).

These functions are the *only* place growth/CAGR/premium figures are
computed. They read rows through `core/repository.py` (never re-parsing
Excel, never touching DuckDB directly) and apply the pure formulas in
`core/metrics.py`. The same implementations back "Explore trends"
(direct calls, this increment) and, from Increment 3 onward, the agent's
tool-calling path (ADR-011's "two call sites, one implementation").

`area` is accepted as an already-resolved `la_code` in this increment --
free-text geography resolution is `TASK-011`'s job (Increment 4) and is out
of scope here; the deterministic tabs' closed selectors mean there is
nothing to resolve (ADR-012).
"""

from __future__ import annotations

import statistics
from typing import Literal

from core import metrics
from core.errors import InvalidRangeError, PeriodOutOfRangeError
from core.models import (
    ComparisonResult,
    Dataset,
    GrowthMetricsResult,
    InsightCandidate,
    InsightCategory,
    LocalAuthority,
    PatternScanResult,
    Period,
    PremiumResult,
    PremiumSeriesResult,
    PremiumTrendResult,
    PriceLookupResult,
    RankedArea,
    RankingCoverageSummary,
    RankingMetric,
    RankingResult,
    TrendResult,
)
from core.repository import Repository

MAX_PER_CATEGORY = 3
MAX_CANDIDATES = 20

#: Metrics computed from a single period (a plain `Period`, not a range).
#: Public (not `_`-prefixed): `agent/agent_definition.py`'s tool wrappers
#: (`TASK-009`) also need this classification, to dispatch a model-supplied
#: `period` vs `period_start`/`period_end` correctly rather than duplicating
#: this list a second time.
LEVEL_METRICS: frozenset[str] = frozenset({"price", "premium_pct", "premium_gbp"})
#: Metrics computed from a `(start, end)` period pair -- growth/CAGR are
#: always range-based (a rate of change needs two points), same as the
#: (v5) premium-*change* metrics.
RANGE_METRICS: frozenset[str] = frozenset(
    {"growth_pct", "growth_gbp", "cagr_pct", "premium_percentage_point_change", "premium_gbp_change"}
)
_PREMIUM_METRICS: frozenset[str] = frozenset(
    {"premium_pct", "premium_gbp", "premium_percentage_point_change", "premium_gbp_change"}
)
MAX_TOP_N = 50


def _require_valid_range(period_start: Period, period_end: Period) -> None:
    if period_start.end_date >= period_end.end_date:
        raise InvalidRangeError(
            f"period_start {period_start.label!r} must be strictly before "
            f"period_end {period_end.label!r}"
        )


def median_price_lookup(
    repository: Repository, area: str, dataset: Dataset, period: Period
) -> PriceLookupResult:
    """FR-002: a single area/dataset/period figure."""
    rows = repository.get_price_series(area, dataset, period.end_date, period.end_date)
    if not rows:
        raise PeriodOutOfRangeError(
            f"No data for area {area!r}, dataset {dataset!r}, period {period.label!r}"
        )
    row = rows[0]
    return PriceLookupResult(
        la_code=row.la_code,
        la_name=row.la_name,
        dataset=dataset,
        period_label=row.period_label,
        price_gbp=row.price_gbp,
        suppressed=row.suppressed,
    )


def price_trend(
    repository: Repository,
    area: str,
    dataset: Dataset,
    period_start: Period,
    period_end: Period,
) -> TrendResult:
    """FR-004/FR-028: the full per-period series across a range, powering
    Explore Trends' price-mode chart."""
    _require_valid_range(period_start, period_end)
    rows = repository.get_price_series(area, dataset, period_start.end_date, period_end.end_date)
    if not rows:
        raise PeriodOutOfRangeError(
            f"No data for area {area!r}, dataset {dataset!r} in range "
            f"{period_start.label!r}..{period_end.label!r}"
        )
    return TrendResult(
        la_code=rows[0].la_code,
        la_name=rows[0].la_name,
        dataset=dataset,
        period_start_label=period_start.label,
        period_end_label=period_end.label,
        points=rows,
        suppressed_periods=[r.period_label for r in rows if r.suppressed],
    )


def growth_metrics(
    repository: Repository,
    area: str,
    dataset: Dataset,
    period_start: Period,
    period_end: Period,
) -> GrowthMetricsResult:
    """FR-029-FR-032: latest price, absolute/percentage growth, CAGR over a
    range. Never raises for suppressed data -- reflected in
    `suppressed_periods`, and in `None` growth/CAGR figures when an
    endpoint itself is suppressed (ASM-010/ASM-011)."""
    _require_valid_range(period_start, period_end)
    rows = repository.get_price_series(area, dataset, period_start.end_date, period_end.end_date)
    if not rows:
        raise PeriodOutOfRangeError(
            f"No data for area {area!r}, dataset {dataset!r} in range "
            f"{period_start.label!r}..{period_end.label!r}"
        )
    suppressed_periods = [r.period_label for r in rows if r.suppressed]

    non_suppressed = [r for r in rows if not r.suppressed]
    if non_suppressed:
        # ASM-009: most recent non-suppressed price *within the selected
        # range* -- not necessarily the dataset's overall latest period.
        latest = max(non_suppressed, key=lambda r: r.period_end_date)
        latest_price, latest_price_period = latest.price_gbp, latest.period_label
    else:
        latest_price = latest_price_period = None

    start_row, end_row = rows[0], rows[-1]
    if start_row.suppressed or end_row.suppressed:
        growth_gbp_value = growth_pct_value = cagr_value = None
    else:
        assert start_row.price_gbp is not None and end_row.price_gbp is not None
        growth_gbp_value = metrics.growth_gbp(start_row.price_gbp, end_row.price_gbp)
        growth_pct_value = metrics.growth_pct(start_row.price_gbp, end_row.price_gbp)
        years = metrics.years_between(start_row.period_end_date, end_row.period_end_date)
        cagr_value = metrics.cagr_pct(start_row.price_gbp, end_row.price_gbp, years)

    return GrowthMetricsResult(
        la_code=rows[0].la_code,
        la_name=rows[0].la_name,
        dataset=dataset,
        period_start_label=period_start.label,
        period_end_label=period_end.label,
        latest_price=latest_price,
        latest_price_period=latest_price_period,
        growth_gbp=growth_gbp_value,
        growth_pct=growth_pct_value,
        cagr_pct=cagr_value,
        suppressed_periods=suppressed_periods,
    )


def _premium_for_row(row) -> tuple[float | None, int | None, list[Literal["new_build", "existing"]]]:
    suppressed_components: list[Literal["new_build", "existing"]] = []
    if row.new_build_suppressed:
        suppressed_components.append("new_build")
    if row.existing_suppressed:
        suppressed_components.append("existing")
    if suppressed_components:
        return None, None, suppressed_components
    assert row.new_build_price is not None and row.existing_price is not None
    return (
        metrics.premium_pct(row.new_build_price, row.existing_price),
        metrics.premium_gbp(row.new_build_price, row.existing_price),
        suppressed_components,
    )


def new_build_premium(repository: Repository, area: str, period: Period) -> PremiumResult:
    """ASM-003: point-in-time new-build premium for one area/period."""
    rows = repository.get_premium_series(area, period.end_date, period.end_date)
    if not rows:
        raise PeriodOutOfRangeError(f"No data for area {area!r}, period {period.label!r}")
    row = rows[0]
    premium_pct_value, premium_gbp_value, suppressed_components = _premium_for_row(row)
    return PremiumResult(
        la_code=row.la_code,
        la_name=row.la_name,
        period_label=row.period_label,
        new_build_price=row.new_build_price,
        existing_price=row.existing_price,
        premium_pct=premium_pct_value,
        premium_gbp=premium_gbp_value,
        suppressed_components=suppressed_components,
    )


def premium_trend(
    repository: Repository, area: str, period_start: Period, period_end: Period
) -> PremiumTrendResult:
    """(v5) Premium *change* between two periods -- distinct from
    `new_build_premium`'s point-in-time reading. Powers ranking-by-premium-
    change (Increment 2)."""
    _require_valid_range(period_start, period_end)
    rows = repository.get_premium_series(area, period_start.end_date, period_end.end_date)
    if not rows:
        raise PeriodOutOfRangeError(
            f"No data for area {area!r} in range {period_start.label!r}..{period_end.label!r}"
        )
    start_row, end_row = rows[0], rows[-1]
    start_pct, start_gbp, start_suppressed = _premium_for_row(start_row)
    end_pct, end_gbp, end_suppressed = _premium_for_row(end_row)

    start_labels: dict[str, Literal["start_new_build", "start_existing"]] = {
        "new_build": "start_new_build",
        "existing": "start_existing",
    }
    end_labels: dict[str, Literal["end_new_build", "end_existing"]] = {
        "new_build": "end_new_build",
        "existing": "end_existing",
    }
    suppressed_components: list[
        Literal["start_new_build", "start_existing", "end_new_build", "end_existing"]
    ] = [start_labels[c] for c in start_suppressed] + [end_labels[c] for c in end_suppressed]
    pct_change = end_pct - start_pct if start_pct is not None and end_pct is not None else None
    gbp_change = end_gbp - start_gbp if start_gbp is not None and end_gbp is not None else None

    return PremiumTrendResult(
        la_code=start_row.la_code,
        la_name=start_row.la_name,
        period_start_label=period_start.label,
        period_end_label=period_end.label,
        start_premium_pct=start_pct,
        start_premium_gbp=start_gbp,
        end_premium_pct=end_pct,
        end_premium_gbp=end_gbp,
        premium_percentage_point_change=pct_change,
        premium_gbp_change=gbp_change,
        suppressed_components=suppressed_components,
    )


def premium_series(
    repository: Repository, area: str, period_start: Period, period_end: Period
) -> PremiumSeriesResult:
    """(v8/v9) Premium at every period across the range, powering Explore
    Trends' premium-mode chart -- not just the two-endpoint change
    `premium_trend` reports."""
    _require_valid_range(period_start, period_end)
    rows = repository.get_premium_series(area, period_start.end_date, period_end.end_date)
    if not rows:
        raise PeriodOutOfRangeError(
            f"No data for area {area!r} in range {period_start.label!r}..{period_end.label!r}"
        )
    points = []
    for row in rows:
        premium_pct_value, premium_gbp_value, suppressed_components = _premium_for_row(row)
        points.append(
            PremiumResult(
                la_code=row.la_code,
                la_name=row.la_name,
                period_label=row.period_label,
                new_build_price=row.new_build_price,
                existing_price=row.existing_price,
                premium_pct=premium_pct_value,
                premium_gbp=premium_gbp_value,
                suppressed_components=suppressed_components,
            )
        )
    return PremiumSeriesResult(
        la_code=rows[0].la_code,
        la_name=rows[0].la_name,
        period_start_label=period_start.label,
        period_end_label=period_end.label,
        points=points,
    )


# -- Multi-area ranking and comparison (TASK-005) ----------------------------


def _validate_ranking_call(metric: RankingMetric, period_or_range: Period | tuple[Period, Period]) -> None:
    """A range metric requires a `(start, end)` pair; a level metric
    requires a single `Period` -- never the other shape."""
    if isinstance(period_or_range, tuple):
        if metric in LEVEL_METRICS:
            raise InvalidRangeError(f"metric {metric!r} requires a single period, not a range")
        start, end = period_or_range
        _require_valid_range(start, end)
    elif metric in RANGE_METRICS:
        raise InvalidRangeError(f"metric {metric!r} requires a (period_start, period_end) range")


def _period_label_or_range(period_or_range: Period | tuple[Period, Period]) -> str:
    if isinstance(period_or_range, tuple):
        start, end = period_or_range
        return f"{start.label} to {end.label}"
    return period_or_range.label


def _price_level_values(
    repository: Repository, la_codes: list[str], dataset: Dataset, period: Period
) -> dict[str, tuple[float | None, bool]]:
    rows = repository.get_price_series_multi(la_codes, dataset, period.end_date, period.end_date)
    by_area = {row.la_code: row for row in rows}
    result: dict[str, tuple[float | None, bool]] = {}
    for code in la_codes:
        row = by_area.get(code)
        if row is None or row.suppressed or row.price_gbp is None:
            result[code] = (None, True)
        else:
            result[code] = (float(row.price_gbp), False)
    return result


def _price_range_values(
    repository: Repository,
    la_codes: list[str],
    dataset: Dataset,
    metric: RankingMetric,
    period_start: Period,
    period_end: Period,
) -> dict[str, tuple[float | None, bool]]:
    rows = repository.get_price_series_multi(la_codes, dataset, period_start.end_date, period_end.end_date)
    rows_by_area: dict[str, list] = {}
    for row in rows:
        rows_by_area.setdefault(row.la_code, []).append(row)

    result: dict[str, tuple[float | None, bool]] = {}
    for code in la_codes:
        area_rows = rows_by_area.get(code)
        if not area_rows:
            result[code] = (None, True)
            continue
        # Repository orders by (la_code, period_end_date) -- see §8.6.
        start_row, end_row = area_rows[0], area_rows[-1]
        if start_row.suppressed or end_row.suppressed:
            result[code] = (None, True)
            continue
        assert start_row.price_gbp is not None and end_row.price_gbp is not None
        if metric == "growth_gbp":
            value = metrics.growth_gbp(start_row.price_gbp, end_row.price_gbp)
        elif metric == "growth_pct":
            value = metrics.growth_pct(start_row.price_gbp, end_row.price_gbp)
        else:  # cagr_pct
            years = metrics.years_between(start_row.period_end_date, end_row.period_end_date)
            value = metrics.cagr_pct(start_row.price_gbp, end_row.price_gbp, years)
        result[code] = (value, False)
    return result


def _premium_level_values(
    repository: Repository, la_codes: list[str], metric: RankingMetric, period: Period
) -> dict[str, tuple[float | None, bool]]:
    result: dict[str, tuple[float | None, bool]] = {}
    for code in la_codes:
        rows = repository.get_premium_series(code, period.end_date, period.end_date)
        if not rows:
            result[code] = (None, True)
            continue
        premium_pct_value, premium_gbp_value, suppressed_components = _premium_for_row(rows[0])
        if suppressed_components:
            result[code] = (None, True)
        else:
            value = premium_pct_value if metric == "premium_pct" else premium_gbp_value
            result[code] = (value, False)
    return result


def _premium_range_values(
    repository: Repository,
    la_codes: list[str],
    metric: RankingMetric,
    period_start: Period,
    period_end: Period,
) -> dict[str, tuple[float | None, bool]]:
    result: dict[str, tuple[float | None, bool]] = {}
    for code in la_codes:
        rows = repository.get_premium_series(code, period_start.end_date, period_end.end_date)
        if not rows:
            result[code] = (None, True)
            continue
        start_pct, start_gbp, start_suppressed = _premium_for_row(rows[0])
        end_pct, end_gbp, end_suppressed = _premium_for_row(rows[-1])
        if start_suppressed or end_suppressed:
            result[code] = (None, True)
            continue
        assert start_pct is not None and end_pct is not None
        assert start_gbp is not None and end_gbp is not None
        if metric == "premium_percentage_point_change":
            result[code] = (end_pct - start_pct, False)
        else:  # premium_gbp_change
            result[code] = (end_gbp - start_gbp, False)
    return result


def _compute_metric_values(
    repository: Repository,
    la_codes: list[str],
    metric: RankingMetric,
    period_or_range: Period | tuple[Period, Period],
    dataset: Dataset,
) -> dict[str, tuple[float | None, bool]]:
    """(value, suppressed) per `la_code`, computed with the fewest possible
    repository round trips: one `get_price_series_multi` call for the whole
    scope for price/growth/CAGR metrics (ADR-014's "one call, one complete
    operation", applied at the repository boundary); a per-area
    `get_premium_series` call for premium metrics, since the repository
    contract (design §8.6) has no multi-area premium method -- still one
    Python-level call to `rank_areas`/`compare_areas`, never a second
    round trip back through the agent/caller."""
    if metric == "price":
        assert isinstance(period_or_range, Period)
        return _price_level_values(repository, la_codes, dataset, period_or_range)
    if metric in ("growth_gbp", "growth_pct", "cagr_pct"):
        assert isinstance(period_or_range, tuple)
        return _price_range_values(repository, la_codes, dataset, metric, *period_or_range)
    if metric in ("premium_pct", "premium_gbp"):
        assert isinstance(period_or_range, Period)
        return _premium_level_values(repository, la_codes, metric, period_or_range)
    assert isinstance(period_or_range, tuple)
    return _premium_range_values(repository, la_codes, metric, *period_or_range)


def _resolve_scope(
    repository: Repository, scope: list[str] | Literal["all"]
) -> tuple[list[LocalAuthority], int]:
    """Returns (resolved areas, count of requested-but-unknown codes)."""
    all_areas = repository.get_geography_reference()
    if scope == "all":
        return all_areas, 0
    by_code = {la.la_code: la for la in all_areas}
    resolved = [by_code[code] for code in scope if code in by_code]
    unknown_count = sum(1 for code in scope if code not in by_code)
    return resolved, unknown_count


def rank_areas(
    repository: Repository,
    metric: RankingMetric,
    period_or_range: Period | tuple[Period, Period],
    scope: list[str] | Literal["all"],
    top_n: int,
    direction: Literal["top", "bottom"],
    dataset: Dataset = "new_build",
) -> RankingResult:
    """FR-005/FR-038: top/bottom-`n` ranking across `scope` by `metric`.
    (ADR-014) Fetches, computes, excludes, and ranks entirely inside this
    one call -- only the top-`n` rows plus an aggregate `coverage` summary
    are returned; areas with no usable figure never occupy a rank slot and
    are never enumerated beyond `coverage.excluded_examples`'s 5-name cap.

    `dataset` selects new-build vs. existing for the dataset-dependent
    metrics (`price`, `growth_*`, `cagr_pct`); ignored for premium metrics,
    which always combine both datasets by definition (ASM-003) -- the same
    dataset-selection control `STORY-001`'s price mode already exposes,
    extended here since "Compare and rank" ranks across areas for
    exactly the same dataset-dependent metrics.
    """
    _validate_ranking_call(metric, period_or_range)
    if not 1 <= top_n <= MAX_TOP_N:
        raise InvalidRangeError(f"top_n must be between 1 and {MAX_TOP_N}, got {top_n}")

    areas, unknown_count = _resolve_scope(repository, scope)
    la_codes = [la.la_code for la in areas]
    values = _compute_metric_values(repository, la_codes, metric, period_or_range, dataset)

    ranked_rows: list[RankedArea] = []
    excluded_names: list[str] = []
    for la in areas:
        value, suppressed = values[la.la_code]
        if value is None:
            excluded_names.append(la.la_name)
            continue
        ranked_rows.append(
            RankedArea(rank=0, la_code=la.la_code, la_name=la.la_name, value=value, suppressed=suppressed)
        )

    def _value(row: RankedArea) -> float:
        assert row.value is not None  # only non-None values were appended above
        return row.value

    # Stable sort: la_code ascending first (deterministic tie-break), then
    # by value -- the la_code order survives ties because Python's sort is
    # stable (NFR-002/design "deterministic ordering ... including tie-
    # breaking").
    ranked_rows.sort(key=lambda row: row.la_code)
    ranked_rows.sort(key=_value, reverse=(direction == "top"))

    top_rows = [
        row.model_copy(update={"rank": position})
        for position, row in enumerate(ranked_rows[:top_n], start=1)
    ]

    coverage = RankingCoverageSummary(
        areas_in_scope=len(areas) + unknown_count,
        areas_ranked=len(top_rows),
        areas_excluded=len(excluded_names) + unknown_count,
        excluded_examples=excluded_names[:5],
    )
    return RankingResult(
        metric=metric,
        period_label_or_range=_period_label_or_range(period_or_range),
        direction=direction,
        rows=top_rows,
        coverage=coverage,
    )


def compare_areas(
    repository: Repository,
    areas: list[str],
    metric: RankingMetric,
    period_or_range: Period | tuple[Period, Period],
    dataset: Dataset = "new_build",
) -> ComparisonResult:
    """FR-003/FR-035-FR-039: direct comparison of a fixed, explicitly-named
    set of areas -- unlike `rank_areas`, every requested area that exists
    in the geography reference gets a row (suppressed areas are flagged via
    `RankedArea.suppressed`/`value=None`, never dropped), since there is no
    top-`n` truncation to make room for. Only an area code that doesn't
    exist in the geography reference at all is excluded (`coverage`)."""
    _validate_ranking_call(metric, period_or_range)

    resolved_areas, unknown_count = _resolve_scope(repository, areas)
    la_codes = [la.la_code for la in resolved_areas]
    values = _compute_metric_values(repository, la_codes, metric, period_or_range, dataset)

    rows = [
        RankedArea(
            rank=0,
            la_code=la.la_code,
            la_name=la.la_name,
            value=values[la.la_code][0],
            suppressed=values[la.la_code][1],
        )
        for la in resolved_areas
    ]

    coverage = RankingCoverageSummary(
        areas_in_scope=len(resolved_areas) + unknown_count,
        areas_ranked=len(resolved_areas),
        areas_excluded=unknown_count,
        excluded_examples=[],
    )
    return ComparisonResult(
        metric=metric,
        period_label_or_range=_period_label_or_range(period_or_range),
        areas=rows,
        coverage=coverage,
    )


# -- Deterministic insight-candidate generation (TASK-006) -------------------

#: (v11, ADR-017) Categories that report one *area's* observation --
#: everything else is scope-wide (`la_code=None`). Kept as a lookup rather
#: than an if/elif chain per category, so a new category's scope-wide-ness
#: is a one-line addition, not a new branch elsewhere.
_AREA_CATEGORIES: frozenset[InsightCategory] = frozenset(
    {
        "growth_leader",
        "growth_laggard",
        "premium_expansion",
        "premium_contraction",
        "regional_divergence",
        "period_on_period_movement",
    }
)


def _ranked_candidates(
    category: InsightCategory,
    ranked_items: list[tuple[str, float]],
    value_unit: Literal["pct", "gbp", "count", "pct_point"],
    la_by_code: dict[str, LocalAuthority],
    max_per_category: int,
    summary_fn,
) -> list[InsightCandidate]:
    """Turns a pre-sorted (deterministic, ties broken by `la_code`) list of
    `(la_code, value)` pairs into up to `max_per_category` `InsightCandidate`
    rows -- shared by every area-scoped category so the salience-rank/
    evidence-capping/completeness logic exists in exactly one place."""
    candidates = []
    for rank, (code, value) in enumerate(ranked_items[:max_per_category], start=1):
        la = la_by_code[code]
        candidates.append(
            InsightCandidate(
                category=category,
                salience_rank=rank,
                la_code=la.la_code,
                la_name=la.la_name,
                value=value,
                value_unit=value_unit,
                evidence_ids=[la.la_code],
                data_completeness="complete",  # only areas with a clean, non-suppressed computation ever reach here
                summary=summary_fn(la, value),
            )
        )
    return candidates


def scan_for_patterns(
    repository: Repository,
    scope: list[str] | Literal["all"],
    period_or_range: tuple[Period, Period],
    max_per_category: int = 1,
    max_candidates: int = 8,
    dataset: Dataset = "new_build",
) -> PatternScanResult:
    """FR-009: a bounded, categorised, evidence-linked set of deterministic
    observations -- the agent selects and narrates a subset from it
    (`ADR-017`); it never computes a pattern itself. (`ADR-014` applies
    identically to `rank_areas`/`compare_areas`): fetch, join, compute, and
    select every candidate happens inside this one call.

    `period_or_range` must be a `(start, end)` pair -- every category here
    is inherently a *change*-over-time or *distribution*-over-time
    observation (a single point in time has no growth, no premium change,
    and no period-on-period movement to report), unlike `rank_areas`, which
    also supports level metrics. This is a deliberate, narrower contract for
    this function specifically, documented here since neither the backlog
    nor the design table states it explicitly.

    `dataset` selects new-build vs. existing for the dataset-dependent
    growth categories (`growth_leader`/`growth_laggard`/
    `regional_growth_distribution`/`regional_divergence`/
    `period_on_period_movement`); ignored for the premium categories, which
    always combine both datasets by definition (`ASM-003`) -- the same
    `dataset` convention `rank_areas`/`compare_areas` already use. Like
    `dataset` itself, this parameter is not named in the design's `§8.3`
    tool-contract table, which omits it for every dataset-dependent metric
    shape alike; documented here on the same conservative, precedent-
    following basis as `TASK-005`'s dataset selector.
    """
    if not isinstance(period_or_range, tuple):
        raise InvalidRangeError("scan_for_patterns requires a (period_start, period_end) range")
    period_start, period_end = period_or_range
    _require_valid_range(period_start, period_end)
    if not 1 <= max_per_category <= MAX_PER_CATEGORY:
        raise InvalidRangeError(f"max_per_category must be between 1 and {MAX_PER_CATEGORY}, got {max_per_category}")
    if not 1 <= max_candidates <= MAX_CANDIDATES:
        raise InvalidRangeError(f"max_candidates must be between 1 and {MAX_CANDIDATES}, got {max_candidates}")

    areas, unknown_count = _resolve_scope(repository, scope)
    la_codes = [la.la_code for la in areas]
    la_by_code = {la.la_code: la for la in areas}

    # -- one repository round trip covers every growth-based category -------
    price_rows = repository.get_price_series_multi(la_codes, dataset, period_start.end_date, period_end.end_date)
    rows_by_area: dict[str, list] = {}
    for row in price_rows:
        rows_by_area.setdefault(row.la_code, []).append(row)

    growth_by_area: dict[str, float] = {}
    period_jump_by_area: dict[str, float] = {}
    suppressed_observations = 0
    suppressed_la_codes: list[str] = []
    for la in areas:
        area_rows = rows_by_area.get(la.la_code, [])
        area_suppressed_count = sum(1 for row in area_rows if row.suppressed)
        suppressed_observations += area_suppressed_count
        if area_suppressed_count:
            suppressed_la_codes.append(la.la_code)
        if len(area_rows) < 2:
            continue
        start_row, end_row = area_rows[0], area_rows[-1]
        if not start_row.suppressed and not end_row.suppressed:
            assert start_row.price_gbp is not None and end_row.price_gbp is not None
            growth_by_area[la.la_code] = metrics.growth_pct(start_row.price_gbp, end_row.price_gbp)

        best_jump: float | None = None
        for previous, current in zip(area_rows, area_rows[1:]):
            if previous.suppressed or current.suppressed:
                continue
            assert previous.price_gbp is not None and current.price_gbp is not None
            jump = metrics.growth_pct(previous.price_gbp, current.price_gbp)
            if best_jump is None or abs(jump) > abs(best_jump):
                best_jump = jump
        if best_jump is not None:
            period_jump_by_area[la.la_code] = best_jump

    # -- premium-change per area (reuses rank_areas'/compare_areas' own helper) --
    premium_change_raw = _premium_range_values(
        repository, la_codes, "premium_percentage_point_change", period_start, period_end
    )
    premium_change_by_area = {
        code: value for code, (value, suppressed) in premium_change_raw.items() if not suppressed and value is not None
    }

    candidates: list[InsightCandidate] = []

    if growth_by_area:
        leaders = sorted(growth_by_area.items(), key=lambda kv: (-kv[1], kv[0]))
        candidates += _ranked_candidates(
            "growth_leader", leaders, "pct", la_by_code, max_per_category,
            lambda la, v: f"{la.la_name} had the highest price growth in scope ({v:+.1f}%).",
        )
        laggards = sorted(growth_by_area.items(), key=lambda kv: (kv[1], kv[0]))
        candidates += _ranked_candidates(
            "growth_laggard", laggards, "pct", la_by_code, max_per_category,
            lambda la, v: f"{la.la_name} had the lowest price growth in scope ({v:+.1f}%).",
        )

        median_growth = statistics.median(growth_by_area.values())
        evidence = sorted(growth_by_area)[:5]
        candidates.append(
            InsightCandidate(
                category="regional_growth_distribution",
                salience_rank=1,
                value=median_growth,
                value_unit="pct",
                evidence_ids=evidence,
                data_completeness="partial" if len(growth_by_area) < len(areas) else "complete",
                summary=f"Median price growth across {len(growth_by_area)} areas in scope was {median_growth:+.1f}%.",
            )
        )

        divergent = sorted(growth_by_area.items(), key=lambda kv: (-abs(kv[1] - median_growth), kv[0]))
        candidates += _ranked_candidates(
            "regional_divergence", divergent, "pct", la_by_code, max_per_category,
            lambda la, v, med=median_growth: (
                f"{la.la_name}'s growth ({v:+.1f}%) diverged most from the scope's median ({med:+.1f}%)."
            ),
        )

    if premium_change_by_area:
        expansions = sorted(premium_change_by_area.items(), key=lambda kv: (-kv[1], kv[0]))
        candidates += _ranked_candidates(
            "premium_expansion", expansions, "pct_point", la_by_code, max_per_category,
            lambda la, v: f"{la.la_name} saw the largest increase in new-build premium ({v:+.1f} percentage points).",
        )
        contractions = sorted(premium_change_by_area.items(), key=lambda kv: (kv[1], kv[0]))
        candidates += _ranked_candidates(
            "premium_contraction", contractions, "pct_point", la_by_code, max_per_category,
            lambda la, v: f"{la.la_name} saw the largest decrease in new-build premium ({v:+.1f} percentage points).",
        )

    if period_jump_by_area:
        movements = sorted(period_jump_by_area.items(), key=lambda kv: (-abs(kv[1]), kv[0]))
        candidates += _ranked_candidates(
            "period_on_period_movement", movements, "pct", la_by_code, max_per_category,
            lambda la, v: f"{la.la_name} had the largest single-period movement in scope ({v:+.1f}%).",
        )

    if suppressed_observations > 0 or unknown_count > 0:
        candidates.append(
            InsightCandidate(
                category="coverage_gap",
                salience_rank=1,
                value=float(suppressed_observations + unknown_count),
                value_unit="count",
                evidence_ids=sorted(suppressed_la_codes)[:5],
                data_completeness="partial",
                summary=(
                    f"{suppressed_observations} area/period observation(s) in scope were suppressed"
                    + (f" and {unknown_count} requested area code(s) were unrecognised" if unknown_count else "")
                    + "."
                ),
            )
        )

    candidates = candidates[:max_candidates]

    coverage = RankingCoverageSummary(
        areas_in_scope=len(areas) + unknown_count,
        areas_ranked=len(growth_by_area),
        areas_excluded=len(areas) + unknown_count - len(growth_by_area),
        excluded_examples=[la_by_code[code].la_name for code in la_codes if code not in growth_by_area][:5],
    )

    return PatternScanResult(
        scope_description=f"{len(areas)} areas, {period_start.label} to {period_end.label}",
        candidates=candidates,
        coverage=coverage,
    )
