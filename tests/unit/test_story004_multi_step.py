"""STORY-004 integration tests: the "Ask the data" tab renders a turn's
`structured_data` as a chart/table (`TASK-018`) alongside the prose answer,
plus an expandable calculation/source-detail view (`FR-023`/`FR-024`).
Multi-step tool composition itself is exercised directly in
`test_agent_definition.py` (one test per wrapped tool) and was verified
against the real API during `TASK-009`; this file proves the UI wiring
around a turn's result, via `AppTest` with a stubbed `answer_question`."""

from __future__ import annotations

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

import ui.ask_the_data as ask_the_data_module
from agent.config import Config
from core.models import (
    AgentTurnResult,
    ChartSpec,
    GrowthMetricsResult,
    RankedArea,
    RankingCoverageSummary,
    RankingResult,
    ToolCallLog,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
AVAILABLE_CONFIG = Config(openai_api_key="sk-test", openai_model="gpt-4o-mini", openai_available=True, log_level="INFO")


def _run_dashboard() -> AppTest:
    app = AppTest.from_file(str(REPO_ROOT / "ui" / "dashboard.py"))
    app.run(timeout=30)
    assert app.exception == []
    return app


def test_a_ranking_answer_renders_a_chart_and_a_table(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ask_the_data_module, "load_config", lambda: AVAILABLE_CONFIG)

    ranking = RankingResult(
        metric="premium_percentage_point_change",
        period_label_or_range="Year ending Sep 2015 to Year ending Sep 2025",
        direction="top",
        rows=[
            RankedArea(rank=1, la_code="E08000003", la_name="Manchester", value=42.8, suppressed=False),
            RankedArea(rank=2, la_code="E07000102", la_name="Three Rivers", value=39.0, suppressed=False),
        ],
        coverage=RankingCoverageSummary(areas_in_scope=318, areas_ranked=2, areas_excluded=316),
    )

    def stub_answer_question(session, question):
        return AgentTurnResult(
            status="answered",
            answer_text="Manchester and Three Rivers saw the largest increases.",
            structured_data=[ranking],
            tool_calls=[ToolCallLog(tool_name="rank_areas", arguments={"metric": "premium_percentage_point_change"}, latency_ms=120.0)],
            chart_spec=ChartSpec(chart_type="bar", source_result_index=0, x_field="la_name", y_fields=["value"], title="Premium change"),
        )

    monkeypatch.setattr(ask_the_data_module, "answer_question", stub_answer_question)

    app = _run_dashboard()
    # "Explore trends" renders its own chart by default (all tab bodies
    # execute on every st.tabs() run) -- compare the delta, not an
    # absolute count, to isolate "Ask the data"'s own chart specifically.
    baseline_charts = len(app.get("plotly_chart"))
    app.text_input(key="ask_the_data_question").set_value("Which areas saw the largest premium increase?").run(timeout=30)
    app.button(key="ask_the_data_submit").click().run(timeout=30)
    assert app.exception == []
    assert any("Manchester and Three Rivers" in m.value for m in app.markdown)
    # A chart was rendered from the valid chart_spec.
    assert len(app.get("plotly_chart")) == baseline_charts + 1
    # The expandable detail view contains the table and tool-call caption.
    expander_labels = [e.label for e in app.expander]
    assert "Show calculation details" in expander_labels
    assert any("rank_areas" in c.value for c in app.caption)


def test_an_invalid_chart_spec_falls_back_to_table_only_with_no_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ask_the_data_module, "load_config", lambda: AVAILABLE_CONFIG)

    growth = GrowthMetricsResult(
        la_code="E08000025", la_name="Birmingham", dataset="existing",
        period_start_label="Year ending Sep 2015", period_end_label="Year ending Sep 2025",
        latest_price=445000, latest_price_period="Year ending Sep 2025",
        growth_gbp=135000.0, growth_pct=43.5, cagr_pct=3.7, suppressed_periods=[],
    )

    def stub_answer_question(session, question):
        return AgentTurnResult(
            status="answered",
            answer_text="Birmingham's existing house prices grew by £135,000 since 2015.",
            structured_data=[growth],
            tool_calls=[ToolCallLog(tool_name="growth_metrics", arguments={"area": "Birmingham"}, latency_ms=90.0)],
            # x_field doesn't exist on GrowthMetricsResult -- CMP-017 must degrade gracefully.
            chart_spec=ChartSpec(chart_type="line", source_result_index=0, x_field="not_a_field", y_fields=["growth_gbp"], title="t"),
        )

    monkeypatch.setattr(ask_the_data_module, "answer_question", stub_answer_question)

    app = _run_dashboard()
    baseline_charts = len(app.get("plotly_chart"))  # "Explore trends"' own default chart
    app.text_input(key="ask_the_data_question").set_value("How has Birmingham changed since 2015?").run(timeout=30)
    app.button(key="ask_the_data_submit").click().run(timeout=30)
    assert app.exception == []
    assert len(app.get("plotly_chart")) == baseline_charts  # no new chart -- the spec didn't validate
    assert any("Show calculation details" in e.label for e in app.expander)  # table-only still renders


def test_period_assumptions_surface_in_the_expandable_detail_view(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ask_the_data_module, "load_config", lambda: AVAILABLE_CONFIG)

    growth = GrowthMetricsResult(
        la_code="E08000025", la_name="Birmingham", dataset="existing",
        period_start_label="Year ending Sep 2015", period_end_label="Year ending Sep 2025",
        latest_price=445000, latest_price_period="Year ending Sep 2025",
        growth_gbp=135000.0, growth_pct=43.5, cagr_pct=3.7, suppressed_periods=[],
    )

    def stub_answer_question(session, question):
        return AgentTurnResult(
            status="answered",
            answer_text="Birmingham's existing house prices grew by £135,000.",
            structured_data=[growth],
            tool_calls=[ToolCallLog(tool_name="growth_metrics", arguments={}, latency_ms=90.0)],
            period_assumptions=["'2015' was interpreted as the year ending September 2015, since no month was given."],
        )

    monkeypatch.setattr(ask_the_data_module, "answer_question", stub_answer_question)

    app = _run_dashboard()
    app.text_input(key="ask_the_data_question").set_value("How has Birmingham changed since 2015?").run(timeout=30)
    app.button(key="ask_the_data_submit").click().run(timeout=30)
    assert app.exception == []
    assert any("year ending September 2015" in c.value for c in app.caption)
