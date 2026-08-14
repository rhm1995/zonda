"""TASK-014: `eval/scoring.py`'s pure pass/fail logic, tested entirely
offline against hand-built `AgentTurnResult`/tool-result objects -- no API
call, no repository, matching design §13's "test the check apart from a
live run" philosophy already used for `agent/guardrails.py`."""

from __future__ import annotations

from core.models import (
    AgentTurnResult,
    ChartSpec,
    EvidenceRef,
    GroundedClaim,
    InsightCandidate,
    PatternScanResult,
    PremiumTrendResult,
    PriceLookupResult,
    RankedArea,
    RankingCoverageSummary,
    RankingResult,
)
from eval.fixtures import ChatTurn, DashboardFixture, ExpectedFact, FactAlternative
from eval.scoring import facts_equal, match_expected, score_dashboard_fixture, score_turn


def _result(**overrides: object) -> AgentTurnResult:
    defaults: dict[str, object] = {"status": "answered", "answer_text": "Manchester's price was £400,000."}
    defaults.update(overrides)
    return AgentTurnResult(**defaults)  # type: ignore[arg-type]


# -- expected_status -----------------------------------------------------------


def test_expected_status_passes_when_status_matches() -> None:
    turn = ChatTurn(question="q", expected_status=["answered"])
    assert score_turn(turn, _result(status="answered")).passed


def test_expected_status_fails_when_status_does_not_match() -> None:
    turn = ChatTurn(question="q", expected_status=["declined"])
    outcome = score_turn(turn, _result(status="answered"))
    assert not outcome.passed
    assert "status" in outcome.reasons[0]


def test_expected_status_none_means_no_check() -> None:
    turn = ChatTurn(question="q")
    assert score_turn(turn, _result(status="clarification_needed")).passed


# -- expected_facts --------------------------------------------------------------


def test_expected_fact_passes_when_a_claim_matches_an_alternative() -> None:
    turn = ChatTurn(
        question="q",
        expected_facts=[
            ExpectedFact(la_code="E08000003", alternatives=[FactAlternative(unit="gbp", value=400000)])
        ],
    )
    result = _result(
        claims=[GroundedClaim(value=400000, unit="gbp", la_code="E08000003")]
    )
    assert score_turn(turn, result).passed


def test_expected_fact_fails_when_no_claim_matches() -> None:
    turn = ChatTurn(
        question="q",
        expected_facts=[
            ExpectedFact(la_code="E08000003", alternatives=[FactAlternative(unit="gbp", value=400000)])
        ],
    )
    result = _result(claims=[GroundedClaim(value=999, unit="gbp", la_code="E08000003")])
    outcome = score_turn(turn, result)
    assert not outcome.passed
    assert "no claim matched" in outcome.reasons[0]


def test_expected_fact_matches_a_value_in_a_templated_fallbacks_structured_data() -> None:
    """`agent/guardrails.py`'s `build_templated_fallback` releases a real
    figure with `claims=[]` when the model's own citation couldn't be
    verified -- still a correct, traceable answer this fixture check must
    accept, not just a `GroundedClaim`."""
    turn = ChatTurn(
        question="q",
        expected_facts=[
            ExpectedFact(
                la_code="E08000035", alternatives=[FactAlternative(unit="pct_point", value=-9.37)], tolerance=0.5
            )
        ],
    )
    premium_trend = PremiumTrendResult(
        la_code="E08000035", la_name="Leeds",
        period_start_label="Year ending Sep 2015", period_end_label="Year ending Sep 2025",
        start_premium_pct=13.64, start_premium_gbp=37498, end_premium_pct=4.27, end_premium_gbp=17500,
        premium_percentage_point_change=-9.37, premium_gbp_change=-19998, suppressed_components=[],
    )
    result = _result(
        answer_text="I couldn't produce a fully verified answer...",
        claims=[],
        structured_data=[premium_trend],
    )
    assert score_turn(turn, result).passed


def test_expected_fact_fallback_match_still_respects_la_code() -> None:
    turn = ChatTurn(
        question="q",
        expected_facts=[
            ExpectedFact(
                la_code="E08000003", alternatives=[FactAlternative(unit="pct_point", value=-9.37)], tolerance=0.5
            )
        ],
    )
    premium_trend = PremiumTrendResult(
        la_code="E08000035", la_name="Leeds",  # a different area than the fact requires
        period_start_label="Year ending Sep 2015", period_end_label="Year ending Sep 2025",
        start_premium_pct=13.64, start_premium_gbp=37498, end_premium_pct=4.27, end_premium_gbp=17500,
        premium_percentage_point_change=-9.37, premium_gbp_change=-19998, suppressed_components=[],
    )
    result = _result(answer_text="...", claims=[], structured_data=[premium_trend])
    assert not score_turn(turn, result).passed


def test_expected_fact_within_tolerance_passes() -> None:
    turn = ChatTurn(
        question="q",
        expected_facts=[
            ExpectedFact(
                la_code="E08000035", alternatives=[FactAlternative(unit="pct_point", value=-9.37)], tolerance=0.5
            )
        ],
    )
    result = _result(claims=[GroundedClaim(value=-9.5, unit="pct_point", la_code="E08000035")])
    assert score_turn(turn, result).passed


def test_expected_fact_alternatives_any_one_matching_passes() -> None:
    turn = ChatTurn(
        question="q",
        expected_facts=[
            ExpectedFact(
                la_code="E08000035",
                alternatives=[
                    FactAlternative(unit="pct_point", value=-9.37),
                    FactAlternative(unit="gbp", value=-19998),
                ],
            )
        ],
    )
    result = _result(claims=[GroundedClaim(value=-19998, unit="gbp", la_code="E08000035")])
    assert score_turn(turn, result).passed


# -- text checks -----------------------------------------------------------------


def test_forbidden_substring_fails_the_turn() -> None:
    turn = ChatTurn(question="q", forbidden_substrings=["900,000"])
    outcome = score_turn(turn, _result(answer_text="Glasgow's price was £900,000."))
    assert not outcome.passed


def test_forbidden_substring_absent_passes() -> None:
    turn = ChatTurn(question="q", forbidden_substrings=["900,000"])
    assert score_turn(turn, _result(answer_text="Manchester's price was £400,000.")).passed


def test_required_substrings_any_passes_if_one_present() -> None:
    turn = ChatTurn(question="q", required_substrings_any=["Scotland", "not covered"])
    assert score_turn(turn, _result(answer_text="Scotland is not covered by this dataset.")).passed


def test_required_substrings_any_fails_if_none_present() -> None:
    turn = ChatTurn(question="q", required_substrings_any=["Scotland"])
    outcome = score_turn(turn, _result(answer_text="Manchester's price was £400,000."))
    assert not outcome.passed


def test_substring_checks_are_case_insensitive() -> None:
    turn = ChatTurn(question="q", required_substrings_any=["SCOTLAND"])
    assert score_turn(turn, _result(answer_text="scotland is not covered.")).passed


# -- coverage_caveats / period_assumptions ----------------------------------------


def test_min_coverage_caveats_fails_when_too_few() -> None:
    turn = ChatTurn(question="q", min_coverage_caveats=1)
    outcome = score_turn(turn, _result(coverage_caveats=[]))
    assert not outcome.passed


def test_min_coverage_caveats_passes_when_met() -> None:
    turn = ChatTurn(question="q", min_coverage_caveats=1)
    assert score_turn(turn, _result(coverage_caveats=["Scotland is not covered."])).passed


def test_min_period_assumptions_fails_when_too_few() -> None:
    turn = ChatTurn(question="q", min_period_assumptions=1)
    outcome = score_turn(turn, _result(period_assumptions=[]))
    assert not outcome.passed


# -- distinct insight categories --------------------------------------------------


def _pattern_scan_result() -> PatternScanResult:
    return PatternScanResult(
        scope_description="318 areas",
        candidates=[
            InsightCandidate(
                category="growth_leader", salience_rank=1, la_code="E08000003", la_name="Manchester",
                value=178.1, value_unit="pct", data_completeness="complete", summary="...",
            ),
            InsightCandidate(
                category="growth_laggard", salience_rank=1, la_code="E09000001", la_name="City of London",
                value=1.0, value_unit="pct", data_completeness="complete", summary="...",
            ),
            InsightCandidate(
                category="coverage_gap", salience_rank=1, value=5.0, value_unit="count",
                data_completeness="partial", summary="...",
            ),
        ],
        coverage=RankingCoverageSummary(areas_in_scope=318, areas_ranked=316, areas_excluded=2),
    )


def test_min_distinct_insight_categories_counts_only_cited_categories() -> None:
    turn = ChatTurn(question="q", min_distinct_insight_categories=3)
    scan = _pattern_scan_result()
    result = _result(
        structured_data=[scan],
        claims=[
            GroundedClaim(value=178.1, unit="pct", evidence=[EvidenceRef(result_index=0, row_index=0, field="value")]),
            GroundedClaim(value=1.0, unit="pct", evidence=[EvidenceRef(result_index=0, row_index=1, field="value")]),
            # Only 2 distinct categories cited so far -- the 3rd candidate exists but isn't claimed.
        ],
    )
    outcome = score_turn(turn, result)
    assert not outcome.passed
    assert "2" in outcome.reasons[0]


def test_min_distinct_insight_categories_passes_when_three_are_cited() -> None:
    turn = ChatTurn(question="q", min_distinct_insight_categories=3)
    scan = _pattern_scan_result()
    result = _result(
        structured_data=[scan],
        claims=[
            GroundedClaim(value=178.1, unit="pct", evidence=[EvidenceRef(result_index=0, row_index=0, field="value")]),
            GroundedClaim(value=1.0, unit="pct", evidence=[EvidenceRef(result_index=0, row_index=1, field="value")]),
            GroundedClaim(value=5.0, unit="count", evidence=[EvidenceRef(result_index=0, row_index=2, field="value")]),
        ],
    )
    assert score_turn(turn, result).passed


# -- causal language / claims / chart / allowed_la_codes --------------------------


def test_check_no_causal_language_fails_on_a_denylisted_marker() -> None:
    turn = ChatTurn(question="q", check_no_causal_language=True)
    outcome = score_turn(turn, _result(answer_text="Prices rose because of high demand."))
    assert not outcome.passed


def test_check_no_causal_language_passes_on_plain_description() -> None:
    turn = ChatTurn(question="q", check_no_causal_language=True)
    assert score_turn(turn, _result(answer_text="Manchester had the highest price growth in scope.")).passed


def test_expect_no_claims_fails_when_claims_present() -> None:
    turn = ChatTurn(question="q", expect_no_claims=True)
    outcome = score_turn(turn, _result(claims=[GroundedClaim(value=1, unit="gbp")]))
    assert not outcome.passed


def test_expect_chart_fails_when_chart_spec_missing() -> None:
    turn = ChatTurn(question="q", expect_chart=True)
    outcome = score_turn(turn, _result(chart_spec=None))
    assert not outcome.passed


def test_expect_chart_passes_when_chart_spec_present() -> None:
    turn = ChatTurn(question="q", expect_chart=True)
    spec = ChartSpec(chart_type="bar", source_result_index=0, x_field="la_name", y_fields=["value"], title="t")
    assert score_turn(turn, _result(chart_spec=spec)).passed


def test_allowed_la_codes_fails_when_a_claim_cites_an_outside_area() -> None:
    turn = ChatTurn(question="q", allowed_la_codes=["E08000003"])
    outcome = score_turn(turn, _result(claims=[GroundedClaim(value=1, unit="gbp", la_code="E07000102")]))
    assert not outcome.passed
    assert "E07000102" in outcome.reasons[0]


def test_allowed_la_codes_ignores_claims_with_no_la_code() -> None:
    turn = ChatTurn(question="q", allowed_la_codes=["E08000003"])
    assert score_turn(turn, _result(claims=[GroundedClaim(value=1, unit="gbp", la_code=None)])).passed


# -- facts_equal (reproducibility) ------------------------------------------------


def test_facts_equal_no_mismatches_for_identical_runs() -> None:
    left = _result(claims=[GroundedClaim(value=400000, unit="gbp", la_code="E08000003")])
    right = _result(claims=[GroundedClaim(value=400000.0, unit="gbp", la_code="E08000003")])
    assert facts_equal(left, right) == []


def test_facts_equal_flags_a_status_difference() -> None:
    left = _result(status="answered")
    right = _result(status="declined")
    mismatches = facts_equal(left, right)
    assert any("status differs" in m for m in mismatches)


def test_facts_equal_flags_a_value_difference() -> None:
    left = _result(claims=[GroundedClaim(value=400000, unit="gbp", la_code="E08000003")])
    right = _result(claims=[GroundedClaim(value=350000, unit="gbp", la_code="E08000003")])
    assert facts_equal(left, right) != []


# -- match_expected / score_dashboard_fixture (dashboard fixtures) ---------------


def test_match_expected_scalar_within_tolerance_passes() -> None:
    result = PriceLookupResult(
        la_code="E08000003", la_name="Manchester", dataset="existing",
        period_label="Year ending Sep 2025", price_gbp=400000, suppressed=False,
    )
    assert match_expected(result, {"price_gbp": 400000}, tolerance=1) == []


def test_match_expected_scalar_out_of_tolerance_fails() -> None:
    result = PriceLookupResult(
        la_code="E08000003", la_name="Manchester", dataset="existing",
        period_label="Year ending Sep 2025", price_gbp=400000, suppressed=False,
    )
    mismatches = match_expected(result, {"price_gbp": 350000}, tolerance=1)
    assert mismatches and "price_gbp" in mismatches[0]


def test_match_expected_none_asserts_suppressed_field() -> None:
    result = PriceLookupResult(
        la_code="E08000003", la_name="Manchester", dataset="new_build",
        period_label="Year ending Sep 2010", price_gbp=None, suppressed=True,
    )
    assert match_expected(result, {"price_gbp": None}, tolerance=1) == []


def test_match_expected_nested_list_checks_only_specified_positions() -> None:
    ranking = RankingResult(
        metric="premium_percentage_point_change",
        period_label_or_range="Year ending Sep 2015 to Year ending Sep 2025",
        direction="top",
        rows=[
            RankedArea(rank=1, la_code="E08000003", la_name="Manchester", value=42.84, suppressed=False),
            RankedArea(rank=2, la_code="E07000102", la_name="Three Rivers", value=39.01, suppressed=False),
        ],
        coverage=RankingCoverageSummary(areas_in_scope=318, areas_ranked=2, areas_excluded=316),
    )
    expected = {"rows": [{"la_code": "E08000003", "value": 42.84}]}
    assert match_expected(ranking, expected, tolerance=0.5) == []


def test_match_expected_missing_field_reports_a_mismatch() -> None:
    result = PriceLookupResult(
        la_code="E08000003", la_name="Manchester", dataset="existing",
        period_label="Year ending Sep 2025", price_gbp=400000, suppressed=False,
    )
    mismatches = match_expected(result, {"not_a_real_field": 1}, tolerance=1)
    assert mismatches and "does not exist" in mismatches[0]


def test_score_dashboard_fixture_wraps_match_expected() -> None:
    fixture = DashboardFixture(
        id="x", category="happy_path", description="d", tool="median_price_lookup",
        args={}, expected={"price_gbp": 400000}, tolerance=1,
    )
    result = PriceLookupResult(
        la_code="E08000003", la_name="Manchester", dataset="existing",
        period_label="Year ending Sep 2025", price_gbp=400000, suppressed=False,
    )
    assert score_dashboard_fixture(fixture, result).passed
