""""Compare and rank" tab (STORY-002, design CMP-015).

Calls `core.tools` **directly** -- no `agent` import anywhere in this
module (`ADR-011`), so this tab is fully functional with no OpenAI API key
configured (`BR-003`/`NFR-011`). Area selection uses a closed multi-select
populated from the repository's own reference data (`ADR-012`), the same
zero-ambiguity pattern `explore_trends.py` uses.

Implementation note (documented interpretive decision -- see completion
report): `FR-036`'s metric set ("price, price growth, CAGR, new-build
premium") is dataset-dependent for its non-premium members exactly the way
`STORY-001`'s price mode already is (`FR-026`), but the story text does not
separately call out a dataset selector. This module adds one, active only
for the dataset-dependent metrics, as the smallest coherent extension that
makes the ticket's own metric list actually computable -- new-build
premium metrics ignore it, since a premium always combines both datasets
by definition (ASM-003).

Ranks the user's own selected areas (via `rank_areas` with `top_n` set to
the full selection size) rather than scanning the full 318-area scope --
"top/bottom ranking of the *selected* areas" (FR-038), not open-ended
discovery.
"""

from __future__ import annotations

from typing import Literal

import plotly.graph_objects as go
import streamlit as st

from core.errors import InvalidRangeError, PeriodOutOfRangeError, RepositoryStartupError
from core.models import Period, RankingMetric, RankingResult
from core.tools import MAX_TOP_N, rank_areas
from ui._shared import get_repository, load_areas, load_periods
from ui.export import export

_METRIC_OPTIONS: list[tuple[RankingMetric, str]] = [
    ("price", "Price"),
    ("growth_gbp", "Growth (£)"),
    ("growth_pct", "Growth (%)"),
    ("cagr_pct", "CAGR (%)"),
    ("premium_pct", "New-build premium (%)"),
    ("premium_gbp", "New-build premium (£)"),
]
_METRIC_LABELS = dict(_METRIC_OPTIONS)
_RANGE_METRICS = {"growth_gbp", "growth_pct", "cagr_pct"}
_DATASET_DEPENDENT_METRICS = {"price", "growth_gbp", "growth_pct", "cagr_pct"}


def _format_value(metric: RankingMetric, value: float | None) -> str:
    if value is None:
        return "—"
    if metric in ("price", "growth_gbp", "premium_gbp"):
        return f"£{value:,.0f}"
    return f"{value:,.2f}%"


def _table_rows(result: RankingResult) -> list[dict[str, str]]:
    """Plain dict rows for `st.dataframe`/inspection -- suppressed/excluded
    values render as an em dash, never as 0 or blank-that-looks-like-zero."""
    return [
        {
            "Rank": str(row.rank),
            "Area": row.la_name,
            "Value": _format_value(result.metric, row.value),
        }
        for row in result.rows
    ]


def _build_ranking_chart(result: RankingResult) -> go.Figure:
    # Horizontal bar, ordered to match the ranking (top of chart = rank 1).
    la_names = [row.la_name for row in reversed(result.rows)]
    values = [row.value for row in reversed(result.rows)]
    figure = go.Figure(go.Bar(x=values, y=la_names, orientation="h"))
    figure.update_layout(
        xaxis_title=_METRIC_LABELS[result.metric], yaxis_title="Area", margin=dict(l=10, r=10, t=10, b=10)
    )
    return figure


def render() -> None:
    st.header("Compare and rank")

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
    selected_names = st.multiselect(
        "Areas", sorted(code_by_name), default=[], key="compare_rank_areas"
    )
    if len(selected_names) < 2:
        st.info("Select two or more areas to compare.")
        return
    selected_codes = [code_by_name[name] for name in selected_names]

    metric_label = st.selectbox(
        "Metric", [label for _, label in _METRIC_OPTIONS], key="compare_rank_metric"
    )
    metric: RankingMetric = next(m for m, label in _METRIC_OPTIONS if label == metric_label)

    if metric in _DATASET_DEPENDENT_METRICS:
        dataset_options: list[Literal["new_build", "existing"]] = ["new_build", "existing"]
        dataset = st.radio(
            "Dataset",
            options=dataset_options,
            format_func=lambda d: "New build" if d == "new_build" else "Existing",
            horizontal=True,
            key="compare_rank_dataset",
        )
    else:
        dataset = "new_build"  # unused for premium metrics (ASM-003 combines both)

    period_labels = [p.label for p in periods]
    period_by_label = {p.label: p for p in periods}
    period_or_range: Period | tuple[Period, Period]
    if metric in _RANGE_METRICS:
        start_col, end_col = st.columns(2)
        with start_col:
            start_label = st.selectbox(
                "Start period", period_labels, index=0, key="compare_rank_start"
            )
        with end_col:
            end_label = st.selectbox(
                "End period", period_labels, index=len(period_labels) - 1, key="compare_rank_end"
            )
        period_or_range = (period_by_label[start_label], period_by_label[end_label])
    else:
        period_label = st.selectbox(
            "Period", period_labels, index=len(period_labels) - 1, key="compare_rank_period"
        )
        period_or_range = period_by_label[period_label]

    direction_options: list[Literal["top", "bottom"]] = ["top", "bottom"]
    direction = st.radio(
        "Direction",
        options=direction_options,
        format_func=lambda d: "Top" if d == "top" else "Bottom",
        horizontal=True,
        key="compare_rank_direction",
    )

    try:
        result = rank_areas(
            repository,
            metric=metric,
            period_or_range=period_or_range,
            scope=selected_codes,
            top_n=min(len(selected_codes), MAX_TOP_N),
            direction=direction,
            dataset=dataset,
        )
    except InvalidRangeError:
        st.warning("Start period must be before end period.")
        return
    except PeriodOutOfRangeError as exc:
        st.error(f"No data available for this selection: {exc}")
        return

    if result.coverage.areas_excluded:
        excluded_note = ", ".join(result.coverage.excluded_examples)
        st.caption(
            f"⚠️ {result.coverage.areas_excluded} of {result.coverage.areas_in_scope} selected "
            f"area(s) have no usable figure for this selection and are excluded from the "
            f"ranking below: {excluded_note}."
        )

    st.dataframe(_table_rows(result), width="stretch", hide_index=True)
    st.plotly_chart(_build_ranking_chart(result), width="stretch")

    csv_bytes = export(result)
    st.download_button(
        "Download CSV",
        data=csv_bytes,
        file_name=f"compare_rank_{metric}_{result.period_label_or_range}.csv".replace(" ", "_"),
        mime="text/csv",
        key="compare_rank_download",
    )
