"""TASK-010 unit tests: the grounding guardrail (CMP-008). Every case here
is the concrete regression evidence that RSK-004 is mitigated structurally,
not lexically -- especially the pct-vs-pct_point and rank-vs-price
collision cases `ADR-009` (v14) specifically names."""

from __future__ import annotations

from datetime import date

from agent.guardrails import autofill_missing_claims, build_repair_instruction, build_templated_fallback, check_grounding
from core.models import (
    DraftAnswer,
    EvidenceRef,
    GroundedClaim,
    PremiumTrendResult,
    PriceLookupResult,
    PricePoint,
    RankedArea,
    RankingCoverageSummary,
    RankingResult,
    TrendResult,
)

PRICE_RESULT = PriceLookupResult(
    la_code="E08000003", la_name="Manchester", dataset="existing",
    period_label="Year ending Sep 2025", price_gbp=400000, suppressed=False,
)
SUPPRESSED_PRICE_RESULT = PriceLookupResult(
    la_code="E08000003", la_name="Manchester", dataset="existing",
    period_label="Year ending Sep 2010", price_gbp=None, suppressed=True,
)
RANKING_RESULT = RankingResult(
    metric="premium_pct",
    period_label_or_range="Year ending Sep 2025",
    direction="top",
    rows=[
        RankedArea(rank=1, la_code="E08000003", la_name="Manchester", value=23.75, suppressed=False),
        RankedArea(rank=2, la_code="E08000025", la_name="Birmingham", value=18.5, suppressed=False),
    ],
    coverage=RankingCoverageSummary(areas_in_scope=2, areas_ranked=2, areas_excluded=0),
)
TREND_RESULT = TrendResult(
    la_code="E08000025", la_name="Birmingham", dataset="existing",
    period_start_label="Year ending Sep 2015", period_end_label="Year ending Sep 2017",
    points=[
        PricePoint(
            dataset="existing", region_country_code="E12000005", region_country_name="West Midlands",
            la_code="E08000025", la_name="Birmingham", period_label="Year ending Sep 2015",
            period_end_date=date(2015, 9, 30), price_gbp=310000, suppressed=False,
        ),
        PricePoint(
            dataset="existing", region_country_code="E12000005", region_country_name="West Midlands",
            la_code="E08000025", la_name="Birmingham", period_label="Year ending Sep 2017",
            period_end_date=date(2017, 9, 30), price_gbp=342000, suppressed=False,
        ),
    ],
    suppressed_periods=[],
)
PREMIUM_TREND_RESULT = PremiumTrendResult(
    la_code="E08000035", la_name="Leeds",
    period_start_label="Year ending Sep 2015", period_end_label="Year ending Sep 2025",
    start_premium_pct=13.6, start_premium_gbp=17500,
    end_premium_pct=23.0, end_premium_gbp=37500,
    premium_percentage_point_change=9.4, premium_gbp_change=20000,
    suppressed_components=[],
)


def _valid_claim(**overrides: object) -> GroundedClaim:
    defaults = dict(
        value=400000, unit="gbp", la_code="E08000003", period_label="Year ending Sep 2025",
        evidence=[EvidenceRef(result_index=0, row_index=None, field="price_gbp")],
    )
    defaults.update(overrides)
    return GroundedClaim(**defaults)  # type: ignore[arg-type]


# -- Pass case --------------------------------------------------------------


def test_valid_claim_passes() -> None:
    draft = DraftAnswer(answer_text="The price was £400,000.", claims=[_valid_claim()])
    result = check_grounding(draft, [PRICE_RESULT])
    assert result.valid
    assert result.invalid_claim_indices == []
    assert result.reasons == []


def test_valid_claim_against_a_list_valued_result_row_passes() -> None:
    claim = GroundedClaim(
        value=23.75, unit="pct", la_code="E08000003", period_label=None,
        evidence=[EvidenceRef(result_index=0, row_index=0, field="value")],
    )
    draft = DraftAnswer(answer_text="Manchester's premium was 23.75%.", claims=[claim])
    result = check_grounding(draft, [RANKING_RESULT])
    assert result.valid


def test_period_label_on_a_row_without_its_own_falls_back_to_the_parent_result() -> None:
    """Regression test for a real bug TASK-009's live-API testing
    surfaced: RankedArea (and InsightCandidate) carry no period_label of
    their own -- every row of one RankingResult call shares the parent's
    single period_label_or_range. A perfectly correct claim citing that
    shared value must not be rejected just because the *row* type has no
    period_label field to match against."""
    claim = GroundedClaim(
        value=23.75, unit="pct", la_code="E08000003", period_label="Year ending Sep 2025",
        evidence=[EvidenceRef(result_index=0, row_index=0, field="value")],
    )
    draft = DraftAnswer(answer_text="Manchester's premium was 23.75%.", claims=[claim])
    result = check_grounding(draft, [RANKING_RESULT])
    assert result.valid


def test_period_label_still_fails_when_it_genuinely_does_not_match() -> None:
    claim = GroundedClaim(
        value=23.75, unit="pct", la_code="E08000003", period_label="Year ending Sep 1999",
        evidence=[EvidenceRef(result_index=0, row_index=0, field="value")],
    )
    draft = DraftAnswer(answer_text="Manchester's premium was 23.75%.", claims=[claim])
    result = check_grounding(draft, [RANKING_RESULT])
    assert not result.valid


def test_start_field_claim_matches_the_start_label_on_a_two_endpoint_scalar_result() -> None:
    """Regression test for a real bug TASK-009's live-API testing
    surfaced: PremiumTrendResult has no period_label/period_label_or_range
    field at all, only separate period_start_label/period_end_label -- a
    perfectly correct claim about start_premium_gbp citing the start label
    must not be rejected just because there's no single field to match
    against."""
    claim = GroundedClaim(
        value=17500, unit="gbp", la_code="E08000035", period_label="Year ending Sep 2015",
        evidence=[EvidenceRef(result_index=0, row_index=None, field="start_premium_gbp")],
    )
    draft = DraftAnswer(answer_text="Leeds' premium was £17,500 in 2015.", claims=[claim])
    result = check_grounding(draft, [PREMIUM_TREND_RESULT])
    assert result.valid


def test_end_field_claim_does_not_match_the_start_label() -> None:
    claim = GroundedClaim(
        value=37500, unit="gbp", la_code="E08000035", period_label="Year ending Sep 2015",  # wrong -- this is the *end* value
        evidence=[EvidenceRef(result_index=0, row_index=None, field="end_premium_gbp")],
    )
    draft = DraftAnswer(answer_text="Leeds' premium was £37,500 in 2015.", claims=[claim])
    result = check_grounding(draft, [PREMIUM_TREND_RESULT])
    assert not result.valid


def test_change_field_claim_matches_the_synthesised_combined_range_label() -> None:
    claim = GroundedClaim(
        value=9.4, unit="pct_point", la_code="E08000035",
        period_label="Year ending Sep 2015 to Year ending Sep 2025",
        evidence=[EvidenceRef(result_index=0, row_index=None, field="premium_percentage_point_change")],
    )
    draft = DraftAnswer(answer_text="Leeds' premium rose 9.4 percentage points.", claims=[claim])
    result = check_grounding(draft, [PREMIUM_TREND_RESULT])
    assert result.valid


def test_change_field_claim_also_matches_either_bare_endpoint_label() -> None:
    """A change spans the whole range -- either endpoint alone is also an
    honest, if less complete, period reference."""
    claim = GroundedClaim(
        value=9.4, unit="pct_point", la_code="E08000035", period_label="Year ending Sep 2025",
        evidence=[EvidenceRef(result_index=0, row_index=None, field="premium_percentage_point_change")],
    )
    draft = DraftAnswer(answer_text="Leeds' premium rose 9.4 percentage points by 2025.", claims=[claim])
    result = check_grounding(draft, [PREMIUM_TREND_RESULT])
    assert result.valid


def test_a_period_label_is_accepted_for_a_result_type_with_no_period_field_at_all() -> None:
    """Regression test for a real bug TASK-009's live-API testing
    surfaced: PatternScanResult (scan_for_patterns' output, STORY-006)
    carries no period_label/period_label_or_range/period_start_label/
    period_end_label field anywhere -- only a free-text scope_description.
    A claim about an InsightCandidate's value stating *any* period_label
    must not be auto-rejected just because there's no field to check it
    against; there being nothing to verify is not the same as it being
    wrong."""
    from core.models import InsightCandidate, PatternScanResult

    scan = PatternScanResult(
        scope_description="318 areas, Year ending Sep 2015 to Year ending Sep 2025",
        candidates=[
            InsightCandidate(
                category="growth_leader", salience_rank=1, la_code="E08000003", la_name="Manchester",
                value=42.8, value_unit="pct", evidence_ids=["E08000003"], data_completeness="complete",
                summary="Manchester had the highest price growth in scope (+42.8%).",
            ),
        ],
        coverage=RankingCoverageSummary(areas_in_scope=318, areas_ranked=316, areas_excluded=2),
    )
    claim = GroundedClaim(
        value=42.8, unit="pct", la_code="E08000003", period_label="Year ending Sep 2015 to Year ending Sep 2025",
        evidence=[EvidenceRef(result_index=0, row_index=0, field="value")],
    )
    draft = DraftAnswer(answer_text="Manchester had the highest growth (+42.8%).", claims=[claim])
    result = check_grounding(draft, [scan])
    assert result.valid


# -- Unresolvable evidence ----------------------------------------------------


def test_wrong_result_index_fails() -> None:
    claim = _valid_claim(evidence=[EvidenceRef(result_index=5, row_index=None, field="price_gbp")])
    draft = DraftAnswer(answer_text="£400,000.", claims=[claim])
    result = check_grounding(draft, [PRICE_RESULT])
    assert not result.valid
    assert result.invalid_claim_indices == [0]


def test_wrong_row_index_fails() -> None:
    claim = GroundedClaim(
        value=23.75, unit="pct", evidence=[EvidenceRef(result_index=0, row_index=99, field="value")]
    )
    draft = DraftAnswer(answer_text="23.75%.", claims=[claim])
    result = check_grounding(draft, [RANKING_RESULT])
    assert not result.valid


def test_nonexistent_field_fails() -> None:
    claim = _valid_claim(evidence=[EvidenceRef(result_index=0, row_index=None, field="not_a_real_field")])
    draft = DraftAnswer(answer_text="£400,000.", claims=[claim])
    result = check_grounding(draft, [PRICE_RESULT])
    assert not result.valid


# -- Value/unit mismatch (the structural pct-vs-pct_point / rank-vs-price case) --


def test_percentage_claimed_against_a_percentage_point_field_fails() -> None:
    """The exact collision ADR-009 (v14) was rewritten to close
    structurally: a pct claim citing a pct_point-typed field must fail,
    even though both are "just numbers" a lexical scan couldn't tell apart."""
    claim = GroundedClaim(
        value=23.75, unit="pct",  # wrong -- RankedArea.value here is "pct" via metric="premium_pct", so this actually IS pct...
        evidence=[EvidenceRef(result_index=0, row_index=0, field="value")],
    )
    # Use a pct_point-typed field instead to force the real collision:
    trend = PremiumTrendResult(
        la_code="E08000003", la_name="Manchester",
        period_start_label="Year ending Sep 2015", period_end_label="Year ending Sep 2025",
        start_premium_pct=11.1, start_premium_gbp=10000,
        end_premium_pct=23.75, end_premium_gbp=95000,
        premium_percentage_point_change=12.65, premium_gbp_change=85000,
        suppressed_components=[],
    )
    pct_claim_on_pct_point_field = GroundedClaim(
        value=12.65, unit="pct",  # should be "pct_point"
        evidence=[EvidenceRef(result_index=0, row_index=None, field="premium_percentage_point_change")],
    )
    draft = DraftAnswer(answer_text="+12.65pp.", claims=[pct_claim_on_pct_point_field])
    result = check_grounding(draft, [trend])
    assert not result.valid
    assert "unit mismatch" in result.reasons[0]


def test_rank_claimed_as_a_price_fails() -> None:
    """rank is always "count" -- a claim stating a rank position must never
    be satisfied by treating it as a price/pct field, or vice versa."""
    claim = GroundedClaim(value=1, unit="gbp", evidence=[EvidenceRef(result_index=0, row_index=0, field="rank")])
    draft = DraftAnswer(answer_text="Ranked #1.", claims=[claim])
    result = check_grounding(draft, [RANKING_RESULT])
    assert not result.valid


def test_wrong_value_for_a_correctly_typed_field_fails() -> None:
    claim = _valid_claim(value=999999)  # actual price_gbp is 400000
    draft = DraftAnswer(answer_text="£999,999.", claims=[claim])
    result = check_grounding(draft, [PRICE_RESULT])
    assert not result.valid


def test_small_rounding_drift_within_tolerance_still_passes() -> None:
    claim = GroundedClaim(
        value=23.7,  # actual is 23.75 -- within the pct tolerance (model's own prose rounding)
        unit="pct", la_code="E08000003",
        evidence=[EvidenceRef(result_index=0, row_index=0, field="value")],
    )
    draft = DraftAnswer(answer_text="23.7%.", claims=[claim])
    result = check_grounding(draft, [RANKING_RESULT])
    assert result.valid


def test_wrong_area_or_period_fails() -> None:
    claim = _valid_claim(la_code="E08000025")  # actual row is Manchester, not Birmingham
    draft = DraftAnswer(answer_text="£400,000 in Birmingham.", claims=[claim])
    result = check_grounding(draft, [PRICE_RESULT])
    assert not result.valid


# -- Omission (no claim at all) ----------------------------------------------


def test_stated_currency_figure_with_no_claim_is_flagged_as_omission() -> None:
    draft = DraftAnswer(answer_text="The price was £400,000.", claims=[])
    result = check_grounding(draft, [PRICE_RESULT])
    assert not result.valid
    assert result.unevidenced_omissions == [400000.0]


def test_stated_percentage_figure_with_no_claim_is_flagged_as_omission() -> None:
    draft = DraftAnswer(answer_text="The premium was 23.75%.", claims=[])
    result = check_grounding(draft, [RANKING_RESULT])
    assert not result.valid
    assert result.unevidenced_omissions == [23.75]


def test_a_bare_year_in_prose_is_never_flagged_as_an_omission() -> None:
    """The whole reason the omission scan is scoped to currency/percentage
    formatting: "...since 2015..." must never be treated as a stated,
    uncited £/％ figure."""
    draft = DraftAnswer(answer_text="Since 2015, the market has changed.", claims=[])
    result = check_grounding(draft, [PRICE_RESULT])
    assert result.unevidenced_omissions == []


# -- Suppressed data honesty --------------------------------------------------


def test_claim_citing_a_suppressed_field_is_invalid_by_construction() -> None:
    claim = _valid_claim(value=0, evidence=[EvidenceRef(result_index=0, row_index=None, field="price_gbp")])
    draft = DraftAnswer(answer_text="£0.", claims=[claim])
    result = check_grounding(draft, [SUPPRESSED_PRICE_RESULT])
    assert not result.valid


# -- Causal-language / suppression-cause denylist (v11/v13) ------------------


def test_causal_language_is_flagged() -> None:
    draft = DraftAnswer(answer_text="Prices rose because demand increased.", claims=[])
    result = check_grounding(draft, [])
    assert not result.valid
    assert result.denylisted_phrases == ["because"]


def test_unevidenced_suppression_cause_phrase_is_flagged() -> None:
    draft = DraftAnswer(
        answer_text=f"{PRICE_RESULT.la_name}'s price is unavailable, likely due to small sample size.",
        claims=[],
    )
    result = check_grounding(draft, [])
    assert not result.valid
    assert "small sample size" in result.denylisted_phrases


def test_clean_narration_with_no_causal_language_passes() -> None:
    draft = DraftAnswer(answer_text="Manchester had the highest growth in scope.", claims=[])
    result = check_grounding(draft, [])
    assert result.valid


# -- Row-index autocorrection (the other common real citation mistake) ------


def test_a_claim_citing_a_list_results_field_without_a_row_index_is_corrected() -> None:
    """The single most common real mistake TASK-009's live-API testing
    surfaced after the empty-claims case: citing `price_gbp` directly on a
    `TrendResult` (row_index=None) instead of on one of its `points`."""
    claim = GroundedClaim(
        value=342000, unit="gbp", la_code="E08000025", period_label="Year ending Sep 2017",
        evidence=[EvidenceRef(result_index=0, row_index=None, field="price_gbp")],
    )
    draft = DraftAnswer(answer_text="Birmingham's price reached £342,000.", claims=[claim])
    filled = autofill_missing_claims(draft, [TREND_RESULT])
    assert filled.claims[0].evidence[0].row_index == 1  # the Sep 2017 point
    check = check_grounding(filled, [TREND_RESULT])
    assert check.valid


def test_row_index_autocorrection_never_touches_a_genuinely_wrong_value() -> None:
    """A claim citing a field that exists on the row type but with a value
    matching *no* row must be left exactly as-is -- this fix only ever
    corrects an address, never invents a match for a wrong number."""
    claim = GroundedClaim(
        value=999999, unit="gbp", evidence=[EvidenceRef(result_index=0, row_index=None, field="price_gbp")]
    )
    draft = DraftAnswer(answer_text="£999,999.", claims=[claim])
    filled = autofill_missing_claims(draft, [TREND_RESULT])
    assert filled.claims[0].evidence[0].row_index is None  # unchanged
    check = check_grounding(filled, [TREND_RESULT])
    assert not check.valid


def test_row_index_autocorrection_leaves_an_already_valid_scalar_claim_alone() -> None:
    claim = _valid_claim()  # cites PriceLookupResult.price_gbp directly, row_index=None -- already correct
    draft = DraftAnswer(answer_text="£400,000.", claims=[claim])
    filled = autofill_missing_claims(draft, [PRICE_RESULT])
    assert filled is draft  # nothing needed fixing at all


# -- Autofill (found necessary by TASK-009's live-API testing) --------------


def test_autofill_synthesises_a_claim_for_a_matching_currency_figure() -> None:
    draft = DraftAnswer(answer_text="The price was £400,000.", claims=[])
    filled = autofill_missing_claims(draft, [PRICE_RESULT])
    assert len(filled.claims) == 1
    assert filled.claims[0].value == 400000
    assert filled.claims[0].unit == "gbp"
    assert filled.claims[0].evidence[0].field == "price_gbp"
    check = check_grounding(filled, [PRICE_RESULT])
    assert check.valid


def test_autofill_synthesises_a_claim_for_a_matching_percentage_figure() -> None:
    draft = DraftAnswer(answer_text="Manchester's premium was 23.75%.", claims=[])
    filled = autofill_missing_claims(draft, [RANKING_RESULT])
    assert len(filled.claims) == 1
    assert filled.claims[0].unit == "pct"
    check = check_grounding(filled, [RANKING_RESULT])
    assert check.valid


def test_autofill_leaves_a_genuinely_unmatched_figure_alone() -> None:
    """Never invents a match -- a figure absent from structured_data stays
    an unresolved omission, exactly as before autofill existed."""
    draft = DraftAnswer(answer_text="The price was £999,999,999.", claims=[])
    filled = autofill_missing_claims(draft, [PRICE_RESULT])
    assert filled.claims == []
    check = check_grounding(filled, [PRICE_RESULT])
    assert not check.valid


def test_autofill_does_not_duplicate_an_already_claimed_figure() -> None:
    existing_claim = _valid_claim()
    draft = DraftAnswer(answer_text="The price was £400,000.", claims=[existing_claim])
    filled = autofill_missing_claims(draft, [PRICE_RESULT])
    assert filled.claims == [existing_claim]  # untouched -- nothing to add


def test_autofill_returns_the_same_draft_when_nothing_needs_filling() -> None:
    draft = DraftAnswer(answer_text="Manchester had the highest growth in scope.", claims=[])
    filled = autofill_missing_claims(draft, [PRICE_RESULT])
    assert filled is draft


def test_autofill_handles_multiple_omissions_across_a_multi_result_turn() -> None:
    draft = DraftAnswer(
        answer_text="Manchester's premium was 23.75%, versus Birmingham's 18.5%.", claims=[]
    )
    filled = autofill_missing_claims(draft, [RANKING_RESULT])
    assert len(filled.claims) == 2
    check = check_grounding(filled, [RANKING_RESULT])
    assert check.valid


# -- Repair prompt / templated fallback --------------------------------------


def test_repair_instruction_cites_the_specific_reasons() -> None:
    claim = _valid_claim(value=999999)
    draft = DraftAnswer(answer_text="£999,999.", claims=[claim])
    result = check_grounding(draft, [PRICE_RESULT])
    instruction = build_repair_instruction(result)
    assert "value mismatch" in instruction


def test_templated_fallback_never_states_a_fabricated_figure_and_reflects_suppression() -> None:
    text = build_templated_fallback([SUPPRESSED_PRICE_RESULT])
    assert "PriceLookupResult" in text
    assert "999999" not in text
    from core.metrics import SUPPRESSION_MESSAGE

    assert SUPPRESSION_MESSAGE in text


def test_templated_fallback_with_no_data_is_an_honest_unavailable_statement() -> None:
    text = build_templated_fallback([])
    assert "no data was" in text


def test_templated_fallback_never_calls_a_scope_wide_candidates_absent_area_suppressed() -> None:
    """Regression test for a real bug TASK-009's live-API testing
    surfaced: InsightCandidate.la_code is None for a scope-wide category
    (e.g. regional_growth_distribution) *by design*, not because ONS
    suppressed a value -- the fallback must not conflate "not applicable"
    with "suppressed"."""
    from core.models import InsightCandidate, PatternScanResult

    scan = PatternScanResult(
        scope_description="316 areas",
        candidates=[
            InsightCandidate(
                category="regional_growth_distribution", salience_rank=1, la_code=None, la_name=None,
                value=46.2, value_unit="pct", evidence_ids=[], data_completeness="partial",
                summary="Median price growth across 316 areas in scope was +46.2%.",
            ),
        ],
        coverage=RankingCoverageSummary(areas_in_scope=318, areas_ranked=316, areas_excluded=2),
    )
    from core.metrics import SUPPRESSION_MESSAGE

    text = build_templated_fallback([scan])
    assert SUPPRESSION_MESSAGE not in text
    assert "la_code=—" in text
