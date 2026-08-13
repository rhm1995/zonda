"""STORY-002 component tests: `ui/compare_rank.py`'s pure rendering-data
helpers, against `core/tools.py`-shaped fixture values -- no live
Streamlit runtime required."""

from __future__ import annotations

from core.models import RankedArea, RankingCoverageSummary, RankingResult
from ui.compare_rank import _build_ranking_chart, _format_value, _table_rows

RESULT = RankingResult(
    metric="premium_pct",
    period_label_or_range="Year ending Sep 2025",
    direction="top",
    rows=[
        RankedArea(rank=1, la_code="E08000003", la_name="Manchester", value=23.75, suppressed=False),
        RankedArea(rank=2, la_code="E08000025", la_name="Birmingham", value=18.5, suppressed=False),
    ],
    coverage=RankingCoverageSummary(areas_in_scope=3, areas_ranked=2, areas_excluded=1, excluded_examples=["Leeds"]),
)


def test_format_value_gbp_metrics_use_pound_sign() -> None:
    assert _format_value("price", 495000) == "£495,000"
    assert _format_value("growth_gbp", 317005) == "£317,005"
    assert _format_value("premium_gbp", 95000) == "£95,000"


def test_format_value_pct_metrics_use_percent_sign() -> None:
    assert _format_value("premium_pct", 23.75) == "23.75%"
    assert _format_value("cagr_pct", 10.76) == "10.76%"


def test_format_value_none_renders_em_dash_not_zero() -> None:
    assert _format_value("premium_pct", None) == "—"


def test_table_rows_matches_ranking_order() -> None:
    rows = _table_rows(RESULT)
    assert rows[0] == {"Rank": "1", "Area": "Manchester", "Value": "23.75%"}
    assert rows[1] == {"Rank": "2", "Area": "Birmingham", "Value": "18.50%"}


def test_ranking_chart_orders_rank_one_at_the_top() -> None:
    figure = _build_ranking_chart(RESULT)
    bar = figure.data[0]
    # y is reversed so rank 1 renders at the top of a horizontal bar chart.
    assert list(bar.y) == ["Birmingham", "Manchester"]
    assert list(bar.x) == [18.5, 23.75]
