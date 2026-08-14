"""TASK-018 unit tests: `ui/charts.py`'s `render_chart`/`render_table`
(`CMP-017`), against hand-built `ChartSpec`/`structured_data` fixtures --
no Streamlit or browser needed."""

from __future__ import annotations

import ast
from datetime import date
from pathlib import Path

import plotly.graph_objects as go
import pytest

from core.models import (
    ChartSpec,
    PricePoint,
    PriceLookupResult,
    RankedArea,
    RankingCoverageSummary,
    RankingResult,
    TrendResult,
)
from ui.charts import render_chart, render_table

REPO_ROOT = Path(__file__).resolve().parents[2]

PRICE_RESULT = PriceLookupResult(
    la_code="E08000003", la_name="Manchester", dataset="existing",
    period_label="Year ending Sep 2025", price_gbp=400000, suppressed=False,
)
SUPPRESSED_PRICE_RESULT = PriceLookupResult(
    la_code="E08000003", la_name="Manchester", dataset="existing",
    period_label="Year ending Sep 2010", price_gbp=None, suppressed=True,
)
def _price_point(period_label: str, end_date: date, price_gbp: int | None, suppressed: bool) -> PricePoint:
    return PricePoint(
        dataset="existing", region_country_code="E12000002", region_country_name="North West",
        la_code="E08000003", la_name="Manchester", period_label=period_label, period_end_date=end_date,
        price_gbp=price_gbp, suppressed=suppressed,
    )


TREND_RESULT = TrendResult(
    la_code="E08000003", la_name="Manchester", dataset="existing",
    period_start_label="Year ending Sep 2015", period_end_label="Year ending Sep 2017",
    points=[
        _price_point("Year ending Sep 2015", date(2015, 9, 30), 200000, False),
        _price_point("Year ending Jun 2016", date(2016, 6, 30), None, True),
        _price_point("Year ending Sep 2017", date(2017, 9, 30), 220000, False),
    ],
    suppressed_periods=["Year ending Jun 2016"],
)
RANKING_RESULT = RankingResult(
    metric="premium_pct",
    period_label_or_range="Year ending Sep 2025",
    direction="top",
    rows=[
        RankedArea(rank=1, la_code="A", la_name="Area A", value=25.0, suppressed=False),
        RankedArea(rank=2, la_code="B", la_name="Area B", value=None, suppressed=True),
    ],
    coverage=RankingCoverageSummary(areas_in_scope=2, areas_ranked=2, areas_excluded=0),
)


# -- render_chart -------------------------------------------------------


def test_valid_line_chart_renders() -> None:
    spec = ChartSpec(chart_type="line", source_result_index=0, x_field="period_label", y_fields=["price_gbp"], title="t")
    figure = render_chart([TREND_RESULT], spec)
    assert isinstance(figure, go.Figure)


def test_valid_bar_chart_renders() -> None:
    spec = ChartSpec(chart_type="bar", source_result_index=0, x_field="la_name", y_fields=["value"], title="t")
    figure = render_chart([RANKING_RESULT], spec)
    assert isinstance(figure, go.Figure)


def test_valid_grouped_bar_chart_with_two_y_fields_renders() -> None:
    spec = ChartSpec(chart_type="grouped_bar", source_result_index=0, x_field="la_name", y_fields=["value", "rank"], title="t")
    figure = render_chart([RANKING_RESULT], spec)
    assert isinstance(figure, go.Figure)
    assert len(figure.data) == 2  # one trace per y_field


def test_scalar_result_can_be_charted_as_a_single_row() -> None:
    spec = ChartSpec(chart_type="bar", source_result_index=0, x_field="la_name", y_fields=["price_gbp"], title="t")
    figure = render_chart([PRICE_RESULT], spec)
    assert isinstance(figure, go.Figure)
    assert list(figure.data[0].y) == [400000]


def test_invalid_chart_type_returns_none_not_raise() -> None:
    spec = ChartSpec.model_construct(
        chart_type="pie", source_result_index=0, x_field="la_name", y_fields=["value"], title="t"
    )
    assert render_chart([RANKING_RESULT], spec) is None


def test_invalid_x_field_returns_none() -> None:
    spec = ChartSpec(chart_type="bar", source_result_index=0, x_field="not_a_real_field", y_fields=["value"], title="t")
    assert render_chart([RANKING_RESULT], spec) is None


def test_invalid_y_field_returns_none() -> None:
    spec = ChartSpec(chart_type="bar", source_result_index=0, x_field="la_name", y_fields=["not_a_real_field"], title="t")
    assert render_chart([RANKING_RESULT], spec) is None


def test_empty_y_fields_returns_none() -> None:
    spec = ChartSpec(chart_type="bar", source_result_index=0, x_field="la_name", y_fields=[], title="t")
    assert render_chart([RANKING_RESULT], spec) is None


@pytest.mark.parametrize("index", [-1, 1, 99])
def test_out_of_range_source_result_index_returns_none(index: int) -> None:
    spec = ChartSpec(chart_type="bar", source_result_index=index, x_field="la_name", y_fields=["value"], title="t")
    assert render_chart([RANKING_RESULT], spec) is None


def test_suppressed_value_renders_as_an_explicit_gap_never_a_plotted_zero() -> None:
    spec = ChartSpec(chart_type="line", source_result_index=0, x_field="period_label", y_fields=["price_gbp"], title="t")
    figure = render_chart([TREND_RESULT], spec)
    assert figure is not None
    y_values = list(figure.data[0].y)
    assert y_values == [200000, None, 220000]
    assert figure.data[0].connectgaps is False


# -- render_table ---------------------------------------------------------


def test_render_table_for_a_scalar_result_is_one_row() -> None:
    rows = render_table(PRICE_RESULT)
    assert len(rows) == 1
    assert rows[0]["price_gbp"] == 400000


def test_render_table_suppressed_scalar_value_is_blank_not_zero() -> None:
    rows = render_table(SUPPRESSED_PRICE_RESULT)
    assert rows[0]["price_gbp"] == "—"


def test_render_table_for_a_list_valued_result_is_one_row_per_item() -> None:
    rows = render_table(RANKING_RESULT)
    assert len(rows) == 2
    assert rows[0]["la_name"] == "Area A"
    assert rows[0]["value"] == 25.0
    assert rows[1]["value"] == "—"  # suppressed area's value is blank, never 0


# -- Import-linter: ui/charts.py must never gain access to the OpenAI client --


def test_charts_module_never_imports_agent_openai_or_agents() -> None:
    source = (REPO_ROOT / "ui" / "charts.py").read_text()
    tree = ast.parse(source, filename="ui/charts.py")
    forbidden = {"agent", "openai", "agents"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name.split(".")[0] not in forbidden, f"ui/charts.py imports forbidden module {alias.name!r}"
        elif isinstance(node, ast.ImportFrom) and node.module:
            assert node.module.split(".")[0] not in forbidden, f"ui/charts.py imports from forbidden module {node.module!r}"
