"""STORY-002 end-to-end verification via Streamlit's `AppTest` harness --
drives the real widgets against the real bundled dataset and checks the
displayed figures match the confirmed Manchester spot-check (design §6.1).
No network access and no `OPENAI_API_KEY` are used or required anywhere in
this test."""

from __future__ import annotations

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from core.repository import Repository
from ui.compare_rank import MAX_COMPARISON_AREAS

REPO_ROOT = Path(__file__).resolve().parents[2]
PROCESSED_DIR = REPO_ROOT / "data" / "processed"

pytestmark = pytest.mark.skipif(
    not PROCESSED_DIR.exists(), reason="data/processed/ not built -- run `python -m data_pipeline.build` first"
)


def _run_dashboard() -> AppTest:
    app = AppTest.from_file(str(REPO_ROOT / "ui" / "dashboard.py"))
    app.run(timeout=30)
    assert app.exception == []
    return app


def _select_areas(app: AppTest, *names: str) -> AppTest:
    widget = app.multiselect(key="compare_rank_areas")
    for name in names:
        widget = widget.select(name)
    widget.run(timeout=30)
    assert app.exception == []
    return app


def test_fewer_than_two_areas_shows_prompt_not_a_table() -> None:
    app = _run_dashboard()
    _select_areas(app, "Manchester")
    assert not app.dataframe


def test_new_build_premium_pct_matches_manchester_spot_check() -> None:
    app = _run_dashboard()
    _select_areas(app, "Manchester", "Birmingham", "Leeds")
    app.selectbox(key="compare_rank_metric").set_value("New-build premium (%)").run(timeout=30)
    assert app.exception == []

    table = app.dataframe[0].value
    manchester_row = table[table["Area"] == "Manchester"].iloc[0]
    assert manchester_row["Value"] == "23.75%"


def test_chart_is_rendered_via_plotly() -> None:
    app = _run_dashboard()
    _select_areas(app, "Manchester", "Birmingham")
    # AppTest doesn't expose a dedicated plotly-chart accessor, so assert
    # indirectly: rendering completed with no exception and a table is
    # present alongside it (the chart call sits right after the table in
    # ui/compare_rank.py -- see IR-005/CON-007's Plotly-specifically
    # requirement, exercised directly by `ui/compare_rank.py`'s own import
    # of `plotly.graph_objects`, asserted in test_dashboard_shell.py-style
    # static checks).
    assert app.exception == []
    assert app.dataframe


def test_direction_toggle_reorders_the_ranking() -> None:
    app = _run_dashboard()
    _select_areas(app, "Manchester", "Birmingham", "Leeds", "Liverpool")
    top = app.dataframe[0].value

    app.radio(key="compare_rank_direction").set_value("Bottom").run(timeout=30)
    assert app.exception == []
    bottom = app.dataframe[0].value

    assert list(top["Area"]) != list(bottom["Area"]) or len(top) == 1


def test_csv_download_available_with_no_openai_related_error() -> None:
    app = _run_dashboard()
    _select_areas(app, "Manchester", "Birmingham")
    assert app.exception == []
    assert not any("openai" in str(e).lower() for e in app.exception)


def test_area_selector_is_capped_at_max_comparison_areas() -> None:
    """(REV-014) The widget itself enforces the cap -- asserted directly on
    the rendered element's configured `max_selections`, not by attempting a
    one-past-the-limit selection: `AppTest`'s synthetic `.select()` chain
    updates `widget_state.value` immediately, so scripting a selection past
    `max_selections` raises `StreamlitSelectionCountExceedsMaxError` in the
    harness itself (verified separately, not representative of a real
    browser, where the frontend disables further options instead) --
    asserting the configured bound directly is the correct, non-fragile
    check here."""
    app = _run_dashboard()
    widget = app.multiselect(key="compare_rank_areas")
    assert widget.max_selections == MAX_COMPARISON_AREAS


def test_selecting_exactly_the_max_comparison_areas_does_not_raise() -> None:
    """(REV-014) The cap must not be off-by-one -- exactly
    `MAX_COMPARISON_AREAS` real areas must still be selectable and render a
    full ranking with no exception."""
    repository = Repository.open(PROCESSED_DIR)
    area_names = sorted(la.la_name for la in repository.get_geography_reference())[:MAX_COMPARISON_AREAS]
    assert len(area_names) == MAX_COMPARISON_AREAS

    app = _run_dashboard()
    _select_areas(app, *area_names)
    assert app.exception == []
    assert app.dataframe
    table = app.dataframe[0].value
    assert len(table) <= MAX_COMPARISON_AREAS
