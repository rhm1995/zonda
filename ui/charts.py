"""Chart/table rendering contract for "Ask the data" (`CMP-017`, `TASK-018`,
design §8.7, `ADR-015`).

The agent selects a chart only from a small fixed `chart_type` enum
(`ChartSpec`, `core/models.py`) and names only fields that are checked, at
render time, against that turn's actual typed result -- it never supplies
chart code, markup, or a chart-config structure of its own (the same risk
class `ADR-001` closed for SQL, closed here for chart rendering, per
`ADR-015`). `render_table` renders the same turn's result(s) generically,
the same non-recomputation pattern `TASK-007`'s CSV export already
established for the two deterministic tabs.

Only `ui/ask_the_data.py` imports this module -- `ui/explore_trends.py` and
`ui/compare_rank.py` render their own charts directly with no agent
involvement (`ADR-011`) and have no dependency on this contract. This
module itself must never import `agent`/`openai`/`agents` (enforced by
`tests/unit/test_charts.py`'s import-linter check, mirroring `TASK-008`'s
pattern for the deterministic tabs) -- a chart-rendering code path must
never gain a route to the OpenAI client.
"""

from __future__ import annotations

from typing import Callable

import plotly.graph_objects as go
from pydantic import BaseModel

from core.models import ChartSpec, list_row_field_and_type

#: A suppressed/missing value renders as blank, never as a fabricated "0"
#: or an empty string that could be misread as "zero was returned" (`ADR-018`).
_BLANK = "—"


def _rows_and_row_type(result: BaseModel) -> tuple[list[BaseModel], type[BaseModel]]:
    """The list of row objects to render/chart, and the type whose fields
    `x_field`/`y_fields` must be validated against -- the row type itself
    for a list-valued result, or `result`'s own type for a scalar one (a
    single-item "row list" of one). Built on `core.models`'s shared
    reflection helper (also used by `agent/guardrails.py`'s `EvidenceRef`
    resolution, TASK-010) so the two addressing schemes can never diverge."""
    found = list_row_field_and_type(result)
    if found is None:
        return [result], type(result)
    field_name, item_type = found
    return list(getattr(result, field_name)), item_type


def _row_dict(model: BaseModel) -> dict:
    return {key: (_BLANK if value is None else value) for key, value in model.model_dump().items()}


def render_table(result: BaseModel) -> list[dict]:
    """Generic typed-object-to-table-rows projection -- the same
    no-recomputation, blank-not-zero pattern `TASK-007`'s CSV export
    already established, applied generically here since "Ask the data" can
    hand this any of the tool registry's result types, not just the three
    CSV export supports."""
    rows, _ = _rows_and_row_type(result)
    return [_row_dict(row) for row in rows]


def _series(rows: list[BaseModel], field: str) -> list[object]:
    """`None`/suppressed values pass through unchanged -- Plotly (with
    `connectgaps=False` for lines, or simply omitting a bar) renders a
    `None` in a data series as an explicit gap, never a plotted `0`."""
    return [getattr(row, field) for row in rows]


def _build_line_chart(rows: list[BaseModel], spec: ChartSpec) -> go.Figure:
    x_values = _series(rows, spec.x_field)
    figure = go.Figure()
    for y_field in spec.y_fields:
        figure.add_trace(
            go.Scatter(x=x_values, y=_series(rows, y_field), mode="lines+markers", name=y_field, connectgaps=False)
        )
    figure.update_layout(title=spec.title)
    return figure


def _build_bar_chart(rows: list[BaseModel], spec: ChartSpec) -> go.Figure:
    x_values = _series(rows, spec.x_field)
    figure = go.Figure()
    for y_field in spec.y_fields:
        figure.add_trace(go.Bar(x=x_values, y=_series(rows, y_field), name=y_field))
    figure.update_layout(title=spec.title, barmode="group" if len(spec.y_fields) > 1 else "relative")
    return figure


def _build_grouped_bar_chart(rows: list[BaseModel], spec: ChartSpec) -> go.Figure:
    x_values = _series(rows, spec.x_field)
    figure = go.Figure()
    for y_field in spec.y_fields:
        figure.add_trace(go.Bar(x=x_values, y=_series(rows, y_field), name=y_field))
    figure.update_layout(title=spec.title, barmode="group")
    return figure


#: `ChartSpec.chart_type`'s fixed dispatch -- one developer-written,
#: Plotly-building function per approved type (design §8.7's contract
#: rule). No code, template string, or chart-config structure the agent
#: supplies is ever executed or interpreted.
_CHART_BUILDERS: dict[str, Callable[[list[BaseModel], ChartSpec], go.Figure]] = {
    "line": _build_line_chart,
    "bar": _build_bar_chart,
    "grouped_bar": _build_grouped_bar_chart,
}


def render_chart(structured_data: list[BaseModel], spec: ChartSpec) -> go.Figure | None:
    """Returns `None` (never raises) if `spec.chart_type` is outside the
    approved enum, `spec.source_result_index` is out of range, or
    `spec.x_field`/`y_fields` are not attributes of the referenced object
    (or its row/item type, for list-valued results). The caller renders
    table-only when `None` comes back."""
    builder = _CHART_BUILDERS.get(spec.chart_type)
    if builder is None:
        return None
    if not (0 <= spec.source_result_index < len(structured_data)):
        return None

    result = structured_data[spec.source_result_index]
    rows, row_type = _rows_and_row_type(result)
    field_names = set(row_type.model_fields)
    if spec.x_field not in field_names:
        return None
    if not spec.y_fields or any(y_field not in field_names for y_field in spec.y_fields):
        return None

    return builder(rows, spec)
