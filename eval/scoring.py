"""Pure scoring logic (`TASK-014`) -- deliberately separated from
`eval/run_eval.py`'s real API/repository calls so it is fully unit-tested
offline (Tier 1, design §13's own testing philosophy), against hand-built
`AgentTurnResult`/tool-result fixtures, the same "test the check apart from
a live run" split `agent/guardrails.py`'s own `check_grounding` already
uses.

Deliberately does **not** import `agent.guardrails`' own causal-language
denylist: an evaluation harness that shared its checking logic with the
mechanism it's meant to verify would prove the mechanism agrees with
itself, not that it actually works end to end (`RSK-010`'s own point --
the internal guardrail is a soft, not hard, guarantee, so the eval's
causal-language check is deliberately independent, not a re-import)."""

from __future__ import annotations

from dataclasses import dataclass, field

from pydantic import BaseModel

from core.models import AgentTurnResult, GroundedClaim, PatternScanResult, list_row_field_and_type, resolve_field_unit
from eval.fixtures import ChatTurn, DashboardFixture, ExpectedFact

#: Mirrors `agent/guardrails.py`'s `_CAUSAL_MARKERS` by deliberate,
#: independent duplication (see module docstring) -- not imported.
_CAUSAL_MARKERS: tuple[str, ...] = (
    "because", "due to", "caused by", "cause of", "leads to", "led to",
    "resulted in", "results in", "as a result of", "owing to", "the reason",
)


@dataclass
class TurnOutcome:
    passed: bool
    reasons: list[str] = field(default_factory=list)


def _fact_matches(fact: ExpectedFact, result: AgentTurnResult) -> bool:
    """A claim match is checked first; a safe templated fallback
    (`agent/guardrails.py`'s `build_templated_fallback`, released when the
    model's own citation couldn't be verified) carries the real figure
    directly in `structured_data` with no `GroundedClaim` at all -- still a
    correct, traceable answer for this fixture's purposes (design §13's own
    "traces to that turn's tool outputs" scoring criterion doesn't require
    a claim specifically), so it also counts as a match."""
    for claim in result.claims:
        if fact.la_code is not None and claim.la_code != fact.la_code:
            continue
        for alternative in fact.alternatives:
            if claim.unit == alternative.unit and abs(float(claim.value) - alternative.value) <= fact.tolerance:
                return True
    return _value_present_in_structured_data(fact, result.structured_data)


def _rows_of(result: BaseModel) -> list[tuple[BaseModel, str | None]]:
    """(row, that row's own `la_code` or the parent result's) pairs -- a
    scalar-shaped result (e.g. `PriceLookupResult`) yields itself once; a
    list-shaped result (e.g. `RankingResult`) yields each of its rows."""
    found = list_row_field_and_type(result)
    if found is None:
        return [(result, getattr(result, "la_code", None))]
    field_name, _ = found
    rows = getattr(result, field_name)
    return [(row, getattr(row, "la_code", None) or getattr(result, "la_code", None)) for row in rows]


def _value_present_in_structured_data(fact: ExpectedFact, structured_data: list[BaseModel]) -> bool:
    for source in structured_data:
        for row, la_code in _rows_of(source):
            if fact.la_code is not None and la_code != fact.la_code:
                continue
            for field_name in type(row).model_fields:
                actual = getattr(row, field_name, None)
                if actual is None or isinstance(actual, bool) or not isinstance(actual, (int, float)):
                    continue
                unit = resolve_field_unit(row, field_name, source)
                if unit is None:
                    continue
                for alternative in fact.alternatives:
                    if unit == alternative.unit and abs(float(actual) - alternative.value) <= fact.tolerance:
                        return True
    return False


def _distinct_insight_categories(result: AgentTurnResult) -> set[str]:
    """The categories a turn's claims actually cite, resolved via each
    claim's `EvidenceRef` back into a `PatternScanResult`'s `candidates` --
    mirrors the exact citation convention `SYSTEM_INSTRUCTIONS` requires
    for an open-ended answer (`row_index` = the candidate's position,
    `field="value"`), so this counts *cited*, evidenced categories, never
    just categories mentioned in prose."""
    categories: set[str] = set()
    for claim in result.claims:
        for ref in claim.evidence:
            if not (0 <= ref.result_index < len(result.structured_data)):
                continue
            source = result.structured_data[ref.result_index]
            if not isinstance(source, PatternScanResult):
                continue
            if ref.row_index is None or not (0 <= ref.row_index < len(source.candidates)):
                continue
            categories.add(source.candidates[ref.row_index].category)
    return categories


def score_turn(turn: ChatTurn, result: AgentTurnResult) -> TurnOutcome:
    """Never raises -- every check appends a specific, human-readable
    reason on failure rather than asserting, so one fixture's report can
    show every way it failed, not just the first."""
    reasons: list[str] = []

    if turn.expected_status is not None and result.status not in turn.expected_status:
        reasons.append(f"status {result.status!r} not in expected {turn.expected_status!r}")

    for fact in turn.expected_facts:
        if not _fact_matches(fact, result):
            reasons.append(f"no claim matched expected fact {fact!r}")

    lowered_answer = result.answer_text.casefold()
    for phrase in turn.forbidden_substrings:
        if phrase.casefold() in lowered_answer:
            reasons.append(f"forbidden substring {phrase!r} present in answer_text")

    if turn.required_substrings_any and not any(
        phrase.casefold() in lowered_answer for phrase in turn.required_substrings_any
    ):
        reasons.append(f"none of {turn.required_substrings_any!r} found in answer_text")

    if len(result.coverage_caveats) < turn.min_coverage_caveats:
        reasons.append(
            f"expected >= {turn.min_coverage_caveats} coverage_caveats, got {len(result.coverage_caveats)}"
        )

    if len(result.period_assumptions) < turn.min_period_assumptions:
        reasons.append(
            f"expected >= {turn.min_period_assumptions} period_assumptions, got {len(result.period_assumptions)}"
        )

    if turn.min_distinct_insight_categories:
        distinct = _distinct_insight_categories(result)
        if len(distinct) < turn.min_distinct_insight_categories:
            reasons.append(
                f"expected >= {turn.min_distinct_insight_categories} distinct evidenced insight "
                f"categories, got {len(distinct)} ({sorted(distinct)!r})"
            )

    if turn.check_no_causal_language:
        found = [marker for marker in _CAUSAL_MARKERS if marker in lowered_answer]
        if found:
            reasons.append(f"causal language present: {found!r}")

    if turn.expect_no_claims and result.claims:
        reasons.append(f"expected no claims, got {len(result.claims)}")

    if turn.expect_chart and result.chart_spec is None:
        reasons.append("expected a chart_spec, got none")

    if turn.allowed_la_codes is not None:
        offenders = sorted(
            {
                claim.la_code
                for claim in result.claims
                if claim.la_code is not None and claim.la_code not in turn.allowed_la_codes
            }
        )
        if offenders:
            reasons.append(f"claims reference la_code(s) outside the allowed set: {offenders!r}")

    return TurnOutcome(passed=not reasons, reasons=reasons)


def facts_equal(left: AgentTurnResult, right: AgentTurnResult) -> list[str]:
    """`TASK-014`'s reproducibility check (`NFR-002`): two independent runs
    of the *same* question must agree on status and on every claimed
    value/unit/area -- returns mismatch descriptions, empty if they agree.
    Claim order is not required to match (the model may state the same
    facts in a different sequence); each left-hand claim just needs some
    matching right-hand claim within a small fixed tolerance."""
    mismatches: list[str] = []
    if left.status != right.status:
        mismatches.append(f"status differs: {left.status!r} vs {right.status!r}")

    def _find_match(claim: GroundedClaim, pool: list[GroundedClaim]) -> bool:
        return any(
            claim.unit == other.unit
            and claim.la_code == other.la_code
            and abs(float(claim.value) - float(other.value)) <= 0.5
            for other in pool
        )

    for claim in left.claims:
        if not _find_match(claim, right.claims):
            mismatches.append(f"claim {claim!r} from the first run has no match in the second run's claims")
    for claim in right.claims:
        if not _find_match(claim, left.claims):
            mismatches.append(f"claim {claim!r} from the second run has no match in the first run's claims")
    return mismatches


# -- Dashboard fixtures: generic structural subset-match ---------------------


def match_expected(actual: BaseModel, expected: dict, tolerance: float, path: str = "") -> list[str]:
    """Recursive structural subset-match: every key in `expected` must be
    present on `actual` with a matching value. A nested `dict` recurses
    into a nested `BaseModel`; a `list` compares only the positions
    `expected` actually specifies against `actual`'s list at the same
    positions (so `{"rows": [{"la_code": "..."}]}` checks `actual.rows[0]`
    without needing every row enumerated); a float/int compares within
    `tolerance`; `None` asserts the field is suppressed; anything else
    compares by equality. Returns mismatch descriptions, empty on a full
    match -- never raises on a missing/wrong-shaped field, so one
    fixture's report can show every mismatch, not just the first."""
    mismatches: list[str] = []
    for key, expected_value in expected.items():
        full_key = f"{path}.{key}" if path else key
        if not hasattr(actual, key):
            mismatches.append(f"{full_key}: field does not exist on {type(actual).__name__}")
            continue
        actual_value = getattr(actual, key)
        mismatches.extend(_match_value(full_key, actual_value, expected_value, tolerance))
    return mismatches


def _match_value(path: str, actual_value: object, expected_value: object, tolerance: float) -> list[str]:
    if isinstance(expected_value, dict):
        if not isinstance(actual_value, BaseModel):
            return [f"{path}: expected a nested object, got {actual_value!r}"]
        return match_expected(actual_value, expected_value, tolerance, path)
    if isinstance(expected_value, list):
        if not isinstance(actual_value, list):
            return [f"{path}: expected a list, got {actual_value!r}"]
        mismatches: list[str] = []
        for index, item_expected in enumerate(expected_value):
            if index >= len(actual_value):
                mismatches.append(f"{path}[{index}]: expected an item, actual list has only {len(actual_value)}")
                continue
            mismatches.extend(_match_value(f"{path}[{index}]", actual_value[index], item_expected, tolerance))
        return mismatches
    if expected_value is None:
        if actual_value is not None:
            return [f"{path}: expected None (suppressed), got {actual_value!r}"]
        return []
    if isinstance(expected_value, (int, float)) and not isinstance(expected_value, bool):
        if actual_value is None or isinstance(actual_value, bool) or not isinstance(actual_value, (int, float)):
            return [f"{path}: expected numeric {expected_value!r}, got {actual_value!r}"]
        if abs(float(actual_value) - float(expected_value)) > tolerance:
            return [f"{path}: expected {expected_value!r} (+/-{tolerance}), got {actual_value!r}"]
        return []
    if actual_value != expected_value:
        return [f"{path}: expected {expected_value!r}, got {actual_value!r}"]
    return []


def score_dashboard_fixture(fixture: DashboardFixture, actual: BaseModel) -> TurnOutcome:
    mismatches = match_expected(actual, fixture.expected, fixture.tolerance)
    return TurnOutcome(passed=not mismatches, reasons=mismatches)
