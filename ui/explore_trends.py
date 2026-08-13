""""Explore trends" tab (STORY-001, design CMP-014).

Calls `core.tools` **directly** -- no `agent` import anywhere in this
module (`ADR-011`), so this tab is fully functional with no OpenAI API key
configured (`BR-003`/`NFR-011`). Area/dataset/period selection uses closed
selectors populated from the repository's own reference data (`ADR-012`):
there is no free text here, so there is no geography-ambiguity or
out-of-coverage case to handle on this tab at all.

Rendering logic that can be tested without a live Streamlit runtime is
factored into plain functions (`_price_chart_series`, `_premium_chart_series`,
formatters) -- `render()` itself is a thin composition of those plus `st.*`
calls, per `STORY-001`'s "component test with functions stubbed to known
fixture values" verification approach.
"""

from __future__ import annotations

from typing import Literal

import plotly.graph_objects as go
import streamlit as st

from core.errors import InvalidRangeError, PeriodOutOfRangeError, RepositoryStartupError
from core.metrics import SUPPRESSION_MESSAGE, premium_label
from core.models import GrowthMetricsResult, Period, PremiumSeriesResult, TrendResult
from core.repository import Repository
from core.tools import growth_metrics, premium_series as compute_premium_series, price_trend
from ui._shared import get_repository, load_areas, load_periods
from ui.export import export

PremiumUnit = Literal["%", "£"]


# -- Pure formatting/rendering-data helpers (unit-testable) -----------------


def _format_gbp(value: float | int | None) -> str:
    return "—" if value is None else f"£{value:,.0f}"


def _format_pct(value: float | None) -> str:
    return "—" if value is None else f"{value:,.2f}%"


def _price_chart_series(trend: TrendResult) -> tuple[list[str], list[int | None]]:
    """(labels, values) for the price-mode chart -- a suppressed point's
    value is `None`, which Plotly (with `connectgaps=False`) renders as an
    explicit gap, never a plotted zero (FR-033)."""
    labels = [point.period_label for point in trend.points]
    values = [point.price_gbp for point in trend.points]
    return labels, values


def _premium_chart_series(
    series: PremiumSeriesResult, unit: PremiumUnit
) -> tuple[list[str], list[float | None], list[Literal["premium", "discount"] | None]]:
    """(labels, values, discount/premium labels) for the premium-mode
    chart. `values` selects `premium_pct` or `premium_gbp` per the unit
    toggle (FR-043); a suppressed point's value is `None` (FR-045); the
    per-point label is derived from the same signed value via
    `core.metrics.premium_label` -- never recomputed independently (FR-044)."""
    if unit == "%":
        values = [point.premium_pct for point in series.points]
    else:
        values = [point.premium_gbp for point in series.points]
    labels = [point.period_label for point in series.points]
    point_labels = [premium_label(value) for value in values]
    return labels, values, point_labels


def _build_price_chart(trend: TrendResult) -> go.Figure:
    labels, values = _price_chart_series(trend)
    figure = go.Figure()
    figure.add_trace(
        go.Scatter(x=labels, y=values, mode="lines+markers", name=trend.dataset, connectgaps=False)
    )
    figure.update_layout(xaxis_title="Period", yaxis_title="Price (£)")
    return figure


def _build_premium_chart(series: PremiumSeriesResult, unit: PremiumUnit) -> go.Figure:
    labels, values, point_labels = _premium_chart_series(series, unit)
    colors = ["crimson" if v is not None and v < 0 else "seagreen" for v in values]
    hover_text = [
        f"{label}: {'discount' if pl == 'discount' else 'premium'}" if pl else label
        for label, pl in zip(labels, point_labels)
    ]
    figure = go.Figure()
    figure.add_trace(
        go.Scatter(
            x=labels,
            y=values,
            mode="lines+markers",
            connectgaps=False,
            marker=dict(color=colors),
            text=hover_text,
            hoverinfo="text+y",
        )
    )
    figure.add_hline(y=0, line_dash="dot", line_color="gray")
    figure.update_layout(
        xaxis_title="Period", yaxis_title=f"Premium ({unit})"
    )
    return figure


def _latest_non_suppressed_premium_point(
    series: PremiumSeriesResult,
) -> tuple[str, float | None, float | None] | None:
    """(period_label, premium_pct, premium_gbp) of the most recent point
    with a computable premium, or `None` if the whole series is suppressed."""
    for point in reversed(series.points):
        if point.premium_pct is not None:
            return point.period_label, point.premium_pct, point.premium_gbp
    return None


# -- Rendering ----------------------------------------------------------------


def render() -> None:
    st.header("Explore trends")

    try:
        repository = get_repository()
    except RepositoryStartupError as exc:
        st.error(f"Could not load the processed dataset: {exc}")
        return

    areas = load_areas(repository)
    periods = load_periods(repository)
    if not areas or not periods:
        st.error("No data available in the processed snapshot.")
        return

    code_by_name = {name: code for code, name in areas}
    selected_area_name = st.selectbox("Area", sorted(code_by_name), key="explore_trends_area")
    la_code = code_by_name[selected_area_name]

    dataset_options: list[Literal["new_build", "existing"]] = ["new_build", "existing"]
    dataset = st.radio(
        "Dataset",
        options=dataset_options,
        format_func=lambda d: "New build" if d == "new_build" else "Existing",
        horizontal=True,
        key="explore_trends_dataset",
    )

    period_labels = [p.label for p in periods]
    period_by_label = {p.label: p for p in periods}
    start_col, end_col = st.columns(2)
    with start_col:
        start_label = st.selectbox(
            "Start period", period_labels, index=0, key="explore_trends_start"
        )
    with end_col:
        end_label = st.selectbox(
            "End period", period_labels, index=len(period_labels) - 1, key="explore_trends_end"
        )
    period_start, period_end = period_by_label[start_label], period_by_label[end_label]

    chart_mode = st.radio(
        "Chart", options=["Price", "Premium"], horizontal=True, key="explore_trends_mode"
    )

    if chart_mode == "Price":
        _render_price_mode(repository, la_code, dataset, period_start, period_end)
    else:
        _render_premium_mode(repository, la_code, period_start, period_end)


def _render_price_mode(
    repository: Repository,
    la_code: str,
    dataset: Literal["new_build", "existing"],
    period_start: Period,
    period_end: Period,
) -> None:
    try:
        trend = price_trend(repository, la_code, dataset, period_start, period_end)
        growth: GrowthMetricsResult = growth_metrics(
            repository, la_code, dataset, period_start, period_end
        )
    except InvalidRangeError:
        st.warning("Start period must be before end period.")
        return
    except PeriodOutOfRangeError as exc:
        st.error(f"No data available for this selection: {exc}")
        return

    tile_1, tile_2, tile_3, tile_4 = st.columns(4)
    tile_1.metric(
        "Latest price",
        _format_gbp(growth.latest_price),
        help=growth.latest_price_period or "No non-suppressed price in range",
    )
    tile_2.metric("Growth (£)", _format_gbp(growth.growth_gbp))
    tile_3.metric("Growth (%)", _format_pct(growth.growth_pct))
    tile_4.metric("CAGR", _format_pct(growth.cagr_pct))

    if growth.suppressed_periods:
        st.caption(f"⚠️ {SUPPRESSION_MESSAGE} Affected periods: {', '.join(growth.suppressed_periods)}")

    st.plotly_chart(_build_price_chart(trend), width="stretch")

    csv_bytes = export(growth)
    st.download_button(
        "Download CSV",
        data=csv_bytes,
        file_name=f"{trend.la_name}_{dataset}_price_{period_start.label}_{period_end.label}.csv".replace(
            " ", "_"
        ),
        mime="text/csv",
        key="explore_trends_price_download",
    )


def _render_premium_mode(
    repository: Repository, la_code: str, period_start: Period, period_end: Period
) -> None:
    try:
        series = compute_premium_series(repository, la_code, period_start, period_end)
    except InvalidRangeError:
        st.warning("Start period must be before end period.")
        return
    except PeriodOutOfRangeError as exc:
        st.error(f"No data available for this selection: {exc}")
        return

    unit: PremiumUnit = st.radio(
        "Unit", options=["%", "£"], horizontal=True, key="explore_trends_premium_unit"
    )

    missing_periods = [p.period_label for p in series.points if p.suppressed_components]
    if missing_periods:
        st.caption(f"⚠️ {SUPPRESSION_MESSAGE} Affected periods: {', '.join(missing_periods)}")

    latest = _latest_non_suppressed_premium_point(series)
    if latest is not None:
        period_label, premium_pct_value, premium_gbp_value = latest
        value = premium_pct_value if unit == "%" else premium_gbp_value
        label = premium_label(value)
        formatted = _format_pct(value) if unit == "%" else _format_gbp(value)
        st.metric(f"Latest premium ({period_label})", f"{formatted} ({label})")
    else:
        st.metric("Latest premium", "—")

    st.plotly_chart(_build_premium_chart(series, unit), width="stretch")

    csv_bytes = export(series)
    st.download_button(
        "Download CSV",
        data=csv_bytes,
        file_name=f"{series.la_name}_premium_{period_start.label}_{period_end.label}.csv".replace(
            " ", "_"
        ),
        mime="text/csv",
        key="explore_trends_premium_download",
    )
