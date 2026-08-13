"""STORY-003 unit tests: the agent's one tool implementation and `Agent`
construction. `median_price_lookup_impl` is tested as a plain function --
no Agents SDK call, no network, no API key needed."""

from __future__ import annotations

from core.models import DraftAnswer
from core.repository import Repository
from agent.agent_definition import (
    _resolve_basic_period,
    build_agent,
    build_median_price_lookup_tool,
    median_price_lookup_impl,
)

EXISTING = "existing"


def test_median_price_lookup_impl_matches_the_brief_example_question(
    real_repository: Repository,
) -> None:
    """The brief's own example Q1: "What was the median price of an
    existing detached house in Manchester in September 2025?" """
    result = median_price_lookup_impl(real_repository, "Manchester", EXISTING, "September", 2025)
    assert result == {
        "status": "ok",
        "la_code": "E08000003",
        "la_name": "Manchester",
        "dataset": "existing",
        "period_label": "Year ending Sep 2025",
        "price_gbp": 400000,
        "suppressed": False,
    }


def test_median_price_lookup_impl_accepts_month_abbreviation(real_repository: Repository) -> None:
    result = median_price_lookup_impl(real_repository, "Manchester", "new_build", "Sep", 2025)
    assert result["status"] == "ok"
    assert result["price_gbp"] == 495000


def test_median_price_lookup_impl_accepts_numeric_month(real_repository: Repository) -> None:
    """Regression test for a bug SPIKE-001's live run surfaced: a real
    model call chose "09" over "September" for the same question, and
    _MONTH_ALIASES (name/abbreviation-only, at the time) rejected it --
    causing a retry loop that exhausted MAX_TURNS. Both zero-padded and
    bare numeral forms must resolve."""
    for numeral in ("9", "09"):
        result = median_price_lookup_impl(real_repository, "Manchester", EXISTING, numeral, 2025)
        assert result["status"] == "ok", f"month={numeral!r} should resolve to September"
        assert result["price_gbp"] == 400000


def test_median_price_lookup_impl_out_of_coverage_area_never_fabricates(
    real_repository: Repository,
) -> None:
    result = median_price_lookup_impl(real_repository, "Glasgow", EXISTING, "September", 2025)
    assert result["status"] == "out_of_coverage"
    assert "message" in result
    assert "price_gbp" not in result


def test_median_price_lookup_impl_unrecognised_area_is_not_found(real_repository: Repository) -> None:
    result = median_price_lookup_impl(real_repository, "Not A Real Place", EXISTING, "September", 2025)
    assert result["status"] == "not_found"


def test_median_price_lookup_impl_unsupported_month_is_period_not_found(
    real_repository: Repository,
) -> None:
    result = median_price_lookup_impl(real_repository, "Manchester", EXISTING, "January", 2025)
    assert result["status"] == "period_not_found"
    assert "March, June, September, December" in result["message"]


def test_median_price_lookup_impl_out_of_range_year_is_period_not_found(
    real_repository: Repository,
) -> None:
    result = median_price_lookup_impl(real_repository, "Manchester", EXISTING, "September", 2099)
    assert result["status"] == "period_not_found"


def test_resolve_basic_period_matches_exact_quarter_end(real_repository: Repository) -> None:
    period = _resolve_basic_period(real_repository, "September", 2025)
    assert period is not None
    assert period.label == "Year ending Sep 2025"


def test_resolve_basic_period_returns_none_for_non_quarter_month(real_repository: Repository) -> None:
    assert _resolve_basic_period(real_repository, "April", 2025) is None


def test_build_median_price_lookup_tool_has_expected_name(real_repository: Repository) -> None:
    tool = build_median_price_lookup_tool(real_repository)
    assert tool.name == "median_price_lookup"


def test_build_agent_wires_one_tool_and_draft_answer_output_type(real_repository: Repository) -> None:
    agent = build_agent(real_repository, model="gpt-4o-mini")
    assert [t.name for t in agent.tools] == ["median_price_lookup"]
    assert agent.output_type is DraftAnswer
    assert agent.model == "gpt-4o-mini"
