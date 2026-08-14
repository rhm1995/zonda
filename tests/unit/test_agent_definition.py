"""STORY-003/TASK-009 unit tests: every tool wrapper's plain `_impl`
function, tested directly (no Agents SDK call, no network, no API key
needed) -- against the real bundled repository's confirmed spot-check
values (design §6.1)."""

from __future__ import annotations

from core.models import DraftAnswer
from core.repository import Repository
from agent.agent_definition import (
    SYSTEM_INSTRUCTIONS,
    build_agent,
    build_price_trend_tool,
    compare_areas_impl,
    growth_metrics_impl,
    median_price_lookup_impl,
    new_build_premium_impl,
    premium_series_impl,
    premium_trend_impl,
    price_trend_impl,
    rank_areas_impl,
    resolve_geography_impl,
    resolve_period_impl,
    scan_for_patterns_impl,
)

EXISTING = "existing"
NEW_BUILD = "new_build"
SEP_2025 = "September 2025"
SEP_2015 = "September 2015"


# -- median_price_lookup -------------------------------------------------


def test_median_price_lookup_matches_the_brief_example_question(real_repository: Repository) -> None:
    """The brief's own example Q1: "What was the median price of an
    existing detached house in Manchester in September 2025?" """
    result = median_price_lookup_impl(real_repository, "Manchester", EXISTING, SEP_2025)
    assert result["status"] == "ok"
    assert result["price_gbp"] == 400000
    assert result["period_label"] == "Year ending Sep 2025"


def test_median_price_lookup_accepts_an_ons_label_directly(real_repository: Repository) -> None:
    result = median_price_lookup_impl(real_repository, "Manchester", NEW_BUILD, "Year ending Sep 2025")
    assert result["status"] == "ok"
    assert result["price_gbp"] == 495000


def test_median_price_lookup_out_of_coverage_area_never_fabricates(real_repository: Repository) -> None:
    result = median_price_lookup_impl(real_repository, "Glasgow", EXISTING, SEP_2025)
    assert result["status"] == "out_of_coverage"
    assert "price_gbp" not in result


def test_median_price_lookup_ambiguous_area_lists_candidates(real_repository: Repository) -> None:
    result = median_price_lookup_impl(real_repository, "Newcastle", EXISTING, SEP_2025)
    assert result["status"] == "ambiguous"
    assert set(result["candidates"]) == {"Newcastle upon Tyne", "Newcastle-under-Lyme"}


def test_median_price_lookup_unparseable_period_is_not_found(real_repository: Repository) -> None:
    result = median_price_lookup_impl(real_repository, "Manchester", EXISTING, "not a period")
    assert result["status"] == "not_found"


def test_median_price_lookup_bare_year_carries_an_assumption_note(real_repository: Repository) -> None:
    result = median_price_lookup_impl(real_repository, "Manchester", EXISTING, "2015")
    assert result["status"] == "ok"
    assert result["price_gbp"] == 220000
    assert "assumption_note" in result and "September 2015" in result["assumption_note"]


def test_median_price_lookup_range_expression_for_a_single_period_arg_is_rejected(
    real_repository: Repository,
) -> None:
    """AC4: a wrapper never forwards an unresolved value -- "since 2015" is
    a range, not a single period, and must not silently pick an endpoint."""
    result = median_price_lookup_impl(real_repository, "Manchester", EXISTING, "since 2015")
    assert result["status"] == "not_found"


# -- price_trend / growth_metrics ------------------------------------------


def test_price_trend_matches_the_confirmed_spot_checks(real_repository: Repository) -> None:
    result = price_trend_impl(real_repository, "Manchester", NEW_BUILD, SEP_2015, SEP_2025)
    assert result["status"] == "ok"
    prices = {p["period_label"]: p["price_gbp"] for p in result["points"]}
    assert prices["Year ending Sep 2015"] == 177995
    assert prices["Year ending Sep 2025"] == 495000


def test_growth_metrics_matches_asm010_formula(real_repository: Repository) -> None:
    result = growth_metrics_impl(real_repository, "Manchester", NEW_BUILD, SEP_2015, SEP_2025)
    assert result["status"] == "ok"
    assert result["growth_gbp"] == 495000 - 177995


def test_growth_metrics_invalid_range_is_a_structured_error_not_a_crash(
    real_repository: Repository,
) -> None:
    result = growth_metrics_impl(real_repository, "Manchester", NEW_BUILD, SEP_2025, SEP_2015)  # reversed
    assert result["status"] == "invalid_arguments"


# -- new_build_premium / premium_trend / premium_series ---------------------


def test_new_build_premium_matches_the_confirmed_spot_check(real_repository: Repository) -> None:
    result = new_build_premium_impl(real_repository, "Manchester", SEP_2025)
    assert result["status"] == "ok"
    assert result["premium_gbp"] == 95000
    assert result["premium_pct"] == 23.75


def test_premium_trend_reports_both_endpoints_and_the_change(real_repository: Repository) -> None:
    result = premium_trend_impl(real_repository, "Manchester", SEP_2015, SEP_2025)
    assert result["status"] == "ok"
    assert result["end_premium_gbp"] == 95000
    assert result["premium_percentage_point_change"] is not None


def test_premium_series_returns_every_period_not_just_endpoints(real_repository: Repository) -> None:
    result = premium_series_impl(real_repository, "Manchester", SEP_2015, SEP_2025)
    assert result["status"] == "ok"
    assert len(result["points"]) > 2


# -- rank_areas / compare_areas ----------------------------------------------


def test_rank_areas_level_metric_uses_period(real_repository: Repository) -> None:
    result = rank_areas_impl(
        real_repository, metric="premium_pct", top_n=5, direction="top",
        scope=["Manchester", "Birmingham", "Leeds"], period=SEP_2025,
    )
    assert result["status"] == "ok"
    assert any(row["la_code"] == "E08000003" for row in result["rows"])


def test_rank_areas_range_metric_uses_period_start_and_end(real_repository: Repository) -> None:
    result = rank_areas_impl(
        real_repository, metric="premium_percentage_point_change", top_n=5, direction="top",
        scope=["Manchester", "Birmingham", "Leeds"], period_start=SEP_2015, period_end=SEP_2025,
    )
    assert result["status"] == "ok"


def test_rank_areas_a_range_given_for_a_level_metric_tolerantly_uses_the_end_period(
    real_repository: Repository,
) -> None:
    """A model that supplies period_start/period_end for a level metric
    (the wrong shape) is not hard-rejected -- confirmed necessary by
    live-API testing, where a model routinely over-supplies every argument
    it has a plausible value for. Interpreted honestly as "at the end of
    that range"."""
    result = rank_areas_impl(
        real_repository, metric="price", top_n=5, direction="top", period_start=SEP_2015, period_end=SEP_2025,
    )
    assert result["status"] == "ok"
    assert result["period_label_or_range"] == "Year ending Sep 2025"


def test_rank_areas_both_period_and_range_given_for_a_range_metric_uses_the_range(
    real_repository: Repository,
) -> None:
    result = rank_areas_impl(
        real_repository, metric="growth_pct", top_n=5, direction="top",
        scope=["Manchester", "Birmingham"], period=SEP_2025, period_start=SEP_2015, period_end=SEP_2025,
    )
    assert result["status"] == "ok"


def test_rank_areas_range_metric_with_no_usable_period_at_all_is_structured_error(
    real_repository: Repository,
) -> None:
    result = rank_areas_impl(real_repository, metric="growth_pct", top_n=5, direction="top")
    assert result["status"] == "invalid_arguments"


def test_rank_areas_unresolvable_scope_member_is_dropped_and_reported(real_repository: Repository) -> None:
    result = rank_areas_impl(
        real_repository, metric="price", top_n=5, direction="top",
        scope=["Manchester", "Not A Real Place"], period=SEP_2025,
    )
    assert result["status"] == "ok"
    assert "scope_warnings" in result
    assert any("Not A Real Place" in w for w in result["scope_warnings"])


def test_compare_areas_matches_the_confirmed_spot_check(real_repository: Repository) -> None:
    result = compare_areas_impl(
        real_repository, areas=["Manchester", "Birmingham"], metric="premium_gbp", period=SEP_2025,
    )
    assert result["status"] == "ok"
    manchester = next(a for a in result["areas"] if a["la_code"] == "E08000003")
    assert manchester["value"] == 95000


def test_compare_areas_all_unresolvable_areas_is_not_found(real_repository: Repository) -> None:
    result = compare_areas_impl(real_repository, areas=["Glasgow", "Edinburgh"], metric="price", period=SEP_2025)
    assert result["status"] == "not_found"
    assert "scope_warnings" in result


# -- scan_for_patterns ---------------------------------------------------------


def test_scan_for_patterns_returns_bounded_evidence_linked_candidates(real_repository: Repository) -> None:
    result = scan_for_patterns_impl(
        real_repository, period_start=SEP_2015, period_end=SEP_2025,
        scope=["Manchester", "Birmingham", "Leeds", "Liverpool"],
    )
    assert result["status"] == "ok"
    categories = [c["category"] for c in result["candidates"]]
    assert len(categories) == len(set(categories))  # at most one per category by default


# -- resolve_geography / resolve_period as standalone tools -------------------


def test_resolve_geography_tool_returns_the_full_status_vocabulary(real_repository: Repository) -> None:
    assert resolve_geography_impl(real_repository, "Manchester")["status"] == "matched"
    assert resolve_geography_impl(real_repository, "Glasgow")["status"] == "out_of_coverage"
    assert resolve_geography_impl(real_repository, "Newcastle")["status"] == "ambiguous"
    assert resolve_geography_impl(real_repository, "Not A Real Place")["status"] == "not_found"


def test_resolve_period_tool_returns_the_full_status_vocabulary(real_repository: Repository) -> None:
    assert resolve_period_impl(real_repository, SEP_2025)["status"] == "resolved"
    assert resolve_period_impl(real_repository, "2015")["status"] == "resolved_with_assumption"
    assert resolve_period_impl(real_repository, "since 2015")["status"] == "range_resolved"
    assert resolve_period_impl(real_repository, "garbage")["status"] == "not_found"


# -- Agent construction --------------------------------------------------------


def test_build_agent_wires_the_full_tool_registry_and_draft_answer_output_type(
    real_repository: Repository,
) -> None:
    agent = build_agent(real_repository, model="gpt-4o-mini")
    tool_names = {t.name for t in agent.tools}
    assert tool_names == {
        "median_price_lookup", "price_trend", "growth_metrics",
        "new_build_premium", "premium_trend", "premium_series",
        "rank_areas", "compare_areas", "scan_for_patterns",
        "resolve_geography", "resolve_period",
    }
    assert agent.output_type is DraftAnswer
    assert agent.model == "gpt-4o-mini"


# -- SYSTEM_INSTRUCTIONS content (Increment 5, TASK-014 live-API finding) ----
#
# Found via the evaluation harness's real-API chat fixtures: with no
# explicit rule, gpt-4o-mini answered an unsupported-dwelling-type
# question ("semi-detached") by silently looking up the *detached*-house
# price and presenting it as if it were the answer -- a confidently wrong
# figure (FR-012), not caught by the grounding guardrail (the number was a
# real field value; only its dwelling-type label in prose was wrong, which
# no schema field captures). Fixed by an explicit instruction; these are
# cheap content-presence regressions against an accidental future removal
# of that instruction -- the model's actual live compliance is verified by
# `eval/fixtures/chat.yaml`'s `edge-non-detached-dwelling-type` fixture,
# not repeatable here without a real API call.


def test_system_instructions_cover_unsupported_dwelling_types() -> None:
    assert "detached houses only" in SYSTEM_INSTRUCTIONS
    assert "never substitute" in SYSTEM_INSTRUCTIONS


def test_system_instructions_direct_growth_metrics_for_trend_questions() -> None:
    assert "growth_metrics to get" in SYSTEM_INSTRUCTIONS


def test_price_trend_tool_description_no_longer_claims_trend_questions_for_itself(
    real_repository: Repository,
) -> None:
    """A second live-API finding: `price_trend`'s own tool description
    used to claim "how has the price changed" questions for itself,
    competing with `growth_metrics` -- the model picked `price_trend`
    alone for the brief's own Q2 phrasing, got no growth figure back, and
    then stated one anyway (self-computed, violating ADR-001), correctly
    caught and safely blocked by the grounding guardrail. Fixed by a
    corrected tool description plus the `SYSTEM_INSTRUCTIONS` rule above."""
    tool = build_price_trend_tool(real_repository)
    assert "Does NOT compute a growth or CAGR figure" in tool.description


def test_system_instructions_require_clarification_for_out_of_range_periods() -> None:
    assert 'status="clarification_needed"' in SYSTEM_INSTRUCTIONS
    assert "suggestions" in SYSTEM_INSTRUCTIONS
