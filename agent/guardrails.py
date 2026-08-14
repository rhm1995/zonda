"""Grounding Guardrail (`CMP-008`, `TASK-010`, design §5/§11, `ADR-009` v14,
`ADR-017` v11, `ADR-018` v13).

The design's primary defence against hallucinated figures (`RSK-004`,
`THR-004`): every numeric claim in a draft answer must resolve,
*structurally*, to a real, non-suppressed field on a real row of that same
turn's tool outputs -- never checked lexically (does this digit appear
somewhere), which cannot distinguish a period year from a metric value, a
percentage from a percentage-point figure, or two rows that coincidentally
share a number.

**Turn-scoping is structural, not a policy choice**: `check_grounding` is
only ever given *this* turn's fresh `structured_data` (populated per call by
`agent/orchestrator.py`) -- a prior turn's results, or `CMP-007`'s session
state, are never in scope, so a claim can never be satisfied by stale
evidence.

**Repair, not just rejection**: an invalid claim or an unevidenced
currency-/percentage-formatted omission doesn't fail the turn outright --
`agent/orchestrator.py` gets one repair attempt (a corrected re-run) before
falling back to `build_templated_fallback`'s tool-output-only answer. This
module owns the *check* and the *fallback text*; the repair re-run itself
is the orchestrator's concern (the same seam that already owns the
API-failure retry loop).

**Auto-fill before checking, found necessary by TASK-009's live-API
testing**: `gpt-4o-mini` was observed to consistently produce a correct,
grounded-in-fact prose answer with `claims` left completely empty, even
under an explicit system-instruction rule requiring one -- a smaller
model's instruction-following gap, not a grounding failure as such (the
*data* it stated was always real). Re-prompting alone did not reliably fix
it either. `autofill_missing_claims` closes this deterministically and
safely instead: for each stated currency-/percentage-formatted figure with
no claim at all, it searches this turn's `structured_data` for a real
field whose value already matches -- never inventing a match, never
changing what the model said, only filling in the citation the model
itself omitted for a figure that demonstrably came from a real tool
output. A figure with no matching field is left exactly as-is, for
`check_grounding`'s omission check to catch as before.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, Field

from core.metrics import SUPPRESSION_MESSAGE
from core.models import (
    ClaimUnit,
    DraftAnswer,
    EvidenceRef,
    GroundedClaim,
    list_row_field_and_type,
    resolve_field_unit,
    resolve_row,
)

#: Calibrated to the display formatters' own precision (`ui/explore_trends.py`/
#: `ui/compare_rank.py` render gbp as whole pounds, pct/pct_point/cagr_pct to
#: 2 decimal places) plus a small allowance for the model's own prose
#: rounding (e.g. stating "23.8%" for an underlying 23.75) -- generous
#: enough to tolerate that, tight enough to still reject a materially wrong
#: figure or a pct-vs-pct_point-scale confusion. Neither the backlog nor the
#: design pins an exact number; this is a deliberate, documented choice.
_TOLERANCE_BY_UNIT: dict[str, float] = {
    "gbp": 1.0,
    "count": 0.5,
    "pct": 0.1,
    "pct_point": 0.1,
    "cagr_pct": 0.1,
}
_DEFAULT_TOLERANCE = 0.1

#: (v11, ADR-017) Flags a likely causal claim in narration for repair --
#: "no causal interpretation" is enforced structurally in `InsightCandidate`
#: (no field can hold a cause) and, as a second, explicitly heuristic layer,
#: here (RSK-010: a soft, not hard, guarantee).
_CAUSAL_MARKERS: tuple[str, ...] = (
    "because", "due to", "caused by", "cause of", "leads to", "led to",
    "resulted in", "results in", "as a result of", "owing to", "the reason",
)
#: (v13, ADR-018) Extends the same denylist mechanism to unevidenced
#: suppression-cause phrasing -- one shared check, not a parallel one.
_SUPPRESSION_CAUSE_MARKERS: tuple[str, ...] = (
    "small sample size", "too few transactions", "data protection",
    "privacy", "confidentiality reasons", "not enough sales",
)
_DENYLISTED_PHRASES: tuple[str, ...] = _CAUSAL_MARKERS + _SUPPRESSION_CAUSE_MARKERS

#: Scoped to currency/percentage-formatted numerals only, so a bare year in
#: prose ("...since 2015...") is never flagged as an omitted claim.
_CURRENCY_RE = re.compile(r"£\s?(-?[\d,]+(?:\.\d+)?)")
_PERCENT_RE = re.compile(r"(-?\d+(?:\.\d+)?)\s?(?:percentage points?|pp\b|%)")
#: An omission is a *missing citation*, not a value check -- generous on
#: purpose (this only decides whether *some* claim plausibly covers the
#: stated figure, not whether it's the right one; that precision belongs to
#: claim validation itself).
_OMISSION_TOLERANCE = 0.5


class GroundingCheckResult(BaseModel):
    """`check_grounding`'s verdict -- `valid` is the single boolean the
    orchestrator branches on; the rest is diagnostic detail for logging and
    for building the repair prompt."""

    valid: bool
    invalid_claim_indices: list[int] = Field(default_factory=list)
    unevidenced_omissions: list[float] = Field(default_factory=list)
    denylisted_phrases: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)


def _candidate_period_labels(row: BaseModel, result: BaseModel, field: str) -> list[str]:
    """Every period-label spelling a claim citing `field` could honestly
    match. A row like `RankedArea`/`InsightCandidate` carries no
    `period_label` of its own -- every row of a `RankingResult`/
    `ComparisonResult`/`PatternScanResult` call shares the parent's one
    `period_label_or_range`. A scalar two-endpoint result like
    `PremiumTrendResult` is a second, distinct shape this must handle:
    it has no `period_label`/`period_label_or_range` field at *all*, only
    separate `period_start_label`/`period_end_label` -- so a claim citing
    a `start_*` field may honestly match the start label, a claim citing
    an `end_*` field the end label, and a claim about a *change* field
    (spanning the whole range) may honestly match either endpoint or a
    synthesised "start to end" combined label (the same format
    `core.tools._period_label_or_range` already produces elsewhere)."""
    row_period_label = getattr(row, "period_label", None)
    if row_period_label is not None:
        return [row_period_label]

    candidates: list[str] = []
    for attr in ("period_label_or_range", "period_label"):
        value = getattr(result, attr, None)
        if value:
            candidates.append(value)
    start_label = getattr(result, "period_start_label", None)
    end_label = getattr(result, "period_end_label", None)
    if field.startswith("start_") and start_label:
        candidates.append(start_label)
    elif field.startswith("end_") and end_label:
        candidates.append(end_label)
    elif start_label and end_label:
        candidates.append(f"{start_label} to {end_label}")
        candidates.append(start_label)
        candidates.append(end_label)
    return candidates


def _claim_failure_reason(claim: GroundedClaim, structured_data: list[BaseModel]) -> str | None:
    """`None` if `claim` is fully valid; otherwise a short, specific reason
    (fed into the repair prompt and the log). Every `EvidenceRef` on the
    claim must, independently, resolve to a real field whose value/unit/
    area/period all match the claim -- a deliberately conservative reading
    of "up to 3" evidence refs as redundant citations of the *same* figure,
    never as components the model is allowed to combine via its own
    arithmetic (that would reopen exactly the risk `ADR-001` closed)."""
    if not claim.evidence:
        return "claim has no evidence"

    for ref in claim.evidence:
        if not (0 <= ref.result_index < len(structured_data)):
            return f"evidence result_index {ref.result_index} is out of range"
        result = structured_data[ref.result_index]
        row = resolve_row(result, ref.row_index)
        if row is None:
            return f"evidence row_index {ref.row_index!r} does not resolve on result {ref.result_index}"
        if ref.field not in type(row).model_fields:
            return f"field {ref.field!r} does not exist on {type(row).__name__}"

        actual_value = getattr(row, ref.field)
        if actual_value is None:
            # Every suppressed value in this codebase's schemas is
            # represented as field=None, never a separate reason (design
            # §6.3) -- so this one check is exactly "cannot cite a
            # suppressed field as evidence" (AC5), with no separate
            # per-type suppressed-flag cross-check needed.
            return f"field {ref.field!r} is suppressed/unavailable, cannot be cited"
        if isinstance(actual_value, bool) or not isinstance(actual_value, (int, float)):
            return f"field {ref.field!r} is not a numeric field"

        unit = resolve_field_unit(row, ref.field, result)
        if unit is None:
            return f"field {ref.field!r} has no resolvable claim unit"
        if unit != claim.unit:
            return f"unit mismatch on field {ref.field!r}: field is {unit!r}, claim says {claim.unit!r}"

        tolerance = _TOLERANCE_BY_UNIT.get(claim.unit, _DEFAULT_TOLERANCE)
        if abs(float(actual_value) - float(claim.value)) > tolerance:
            return f"value mismatch on field {ref.field!r}: claim={claim.value}, actual={actual_value}"

        if claim.la_code is not None:
            row_la_code = getattr(row, "la_code", None) or getattr(result, "la_code", None)
            if row_la_code != claim.la_code:
                return f"la_code mismatch: claim={claim.la_code!r}, resolved={row_la_code!r}"
        if claim.period_label is not None:
            candidate_labels = _candidate_period_labels(row, result, ref.field)
            # An empty candidate list means this result type carries no
            # period concept at all (e.g. InsightCandidate/PatternScanResult
            # -- there is no ground-truth field to check a stated period
            # against), not that every possible label is wrong. Only a
            # *non-empty* candidate list the claim fails to match is a
            # genuine mismatch -- this never weakens the value/unit/la_code
            # checks above, only a period framing nothing can verify.
            if candidate_labels and claim.period_label not in candidate_labels:
                return f"period_label mismatch: claim={claim.period_label!r}, candidates={candidate_labels!r}"

    return None


def _formatted_numerals(text: str) -> list[float]:
    values: list[float] = []
    for match in _CURRENCY_RE.finditer(text):
        values.append(float(match.group(1).replace(",", "")))
    for match in _PERCENT_RE.finditer(text):
        values.append(float(match.group(1)))
    return values


def _find_unevidenced_omissions(draft: DraftAnswer) -> list[float]:
    """AC4: a stated currency-/percentage-formatted figure with no
    accompanying claim at all -- an omission, not a mismatch (mismatches
    are `_claim_failure_reason`'s job). Demoted secondary check per
    `ADR-009` (v14): it can only catch a missing citation, never a wrong
    value for an existing one."""
    claim_values = [float(claim.value) for claim in draft.claims]
    return [
        numeral
        for numeral in _formatted_numerals(draft.answer_text)
        if not any(abs(numeral - value) <= _OMISSION_TOLERANCE for value in claim_values)
    ]


def _find_denylisted_phrases(text: str) -> list[str]:
    lowered = text.casefold()
    return [phrase for phrase in _DENYLISTED_PHRASES if phrase in lowered]


#: A currency-formatted numeral can only mean "gbp"; a percentage-formatted
#: one is ambiguous between these three units without more context (a plain
#: "%" reads the same whether the underlying field is a rate or a point
#: change) -- `_find_matching_evidence` tries each in turn.
_PERCENT_CANDIDATE_UNITS: tuple[ClaimUnit, ...] = ("pct", "pct_point", "cagr_pct")


def _numeral_matches_with_candidate_units(text: str) -> list[tuple[float, tuple[ClaimUnit, ...]]]:
    matches: list[tuple[float, tuple[ClaimUnit, ...]]] = []
    for match in _CURRENCY_RE.finditer(text):
        matches.append((float(match.group(1).replace(",", "")), ("gbp",)))
    for match in _PERCENT_RE.finditer(text):
        matches.append((float(match.group(1)), _PERCENT_CANDIDATE_UNITS))
    return matches


def _find_matching_evidence(
    value: float, candidate_units: tuple[ClaimUnit, ...], structured_data: list[BaseModel]
) -> GroundedClaim | None:
    """Searches every field of every row of every one of this turn's
    `structured_data` entries for one whose value already matches `value`
    within its unit's tolerance -- the first match wins (deterministic,
    since `structured_data` order is fixed for a given turn). `None` if
    nothing matches; never guesses or picks the "closest" field."""
    for result_index, result in enumerate(structured_data):
        found = list_row_field_and_type(result)
        rows: list[tuple[int | None, BaseModel]]
        if found is None:
            rows = [(None, result)]
        else:
            field_name, _ = found
            rows = list(enumerate(getattr(result, field_name)))

        for row_index, row in rows:
            for candidate_field in type(row).model_fields:
                actual = getattr(row, candidate_field, None)
                if actual is None or isinstance(actual, bool) or not isinstance(actual, (int, float)):
                    continue
                unit = resolve_field_unit(row, candidate_field, result)
                if unit is None or unit not in candidate_units:
                    continue
                tolerance = _TOLERANCE_BY_UNIT.get(unit, _DEFAULT_TOLERANCE)
                if abs(float(actual) - value) > tolerance:
                    continue
                return GroundedClaim(
                    value=value,
                    unit=unit,
                    la_code=getattr(row, "la_code", None) or getattr(result, "la_code", None),
                    period_label=getattr(row, "period_label", None),
                    evidence=[EvidenceRef(result_index=result_index, row_index=row_index, field=candidate_field)],
                )
    return None


def _autocorrect_row_index(claim: GroundedClaim, structured_data: list[BaseModel]) -> GroundedClaim:
    """The second most common real citation mistake `TASK-009`'s live-API
    testing surfaced (after the empty-`claims` case `autofill_missing_claims`
    already handles): a claim citing `row_index=None` (i.e. addressing the
    *result itself*) with a `field` that doesn't exist at that top level --
    but does exist on the result's list-row type (e.g. citing `price_gbp`
    directly on a `TrendResult`, which only has it per-`point`, not at the
    top level). Corrects the row index to whichever row's own field value
    matches the claim, if one does; otherwise returns `claim` completely
    unchanged, so a genuinely wrong figure is still caught downstream by
    `check_grounding` exactly as before -- this only ever fixes an
    *address*, never a value."""
    new_evidence: list[EvidenceRef] = []
    changed = False
    for ref in claim.evidence:
        if ref.row_index is not None or not (0 <= ref.result_index < len(structured_data)):
            new_evidence.append(ref)
            continue
        result = structured_data[ref.result_index]
        if ref.field in type(result).model_fields:
            new_evidence.append(ref)  # already addresses a real top-level field -- nothing to fix
            continue
        found = list_row_field_and_type(result)
        if found is None:
            new_evidence.append(ref)
            continue
        field_name, item_type = found
        if ref.field not in item_type.model_fields:
            new_evidence.append(ref)  # the field doesn't exist anywhere on this result -- not this fix's job
            continue

        tolerance = _TOLERANCE_BY_UNIT.get(claim.unit, _DEFAULT_TOLERANCE)
        match_index = None
        for row_index, row in enumerate(getattr(result, field_name)):
            actual = getattr(row, ref.field, None)
            if actual is None or isinstance(actual, bool) or not isinstance(actual, (int, float)):
                continue
            if abs(float(actual) - float(claim.value)) <= tolerance:
                match_index = row_index
                break

        if match_index is None:
            new_evidence.append(ref)
        else:
            new_evidence.append(EvidenceRef(result_index=ref.result_index, row_index=match_index, field=ref.field))
            changed = True

    if not changed:
        return claim
    return claim.model_copy(update={"evidence": new_evidence})


def autofill_missing_claims(draft: DraftAnswer, structured_data: list[BaseModel]) -> DraftAnswer:
    """Two deterministic, value-preserving repairs to `draft.claims` before
    `check_grounding` ever sees it -- see module docstring:
    1. `_autocorrect_row_index` -- fixes a claim addressing a list-shaped
       result without specifying which row.
    2. A synthesised `GroundedClaim` for a stated currency-/percentage-
       formatted figure with no claim at all but a real matching field.
    Never invents a match for either: an unresolvable claim/omission is
    left exactly as `check_grounding` would have found it originally.
    Returns `draft` unchanged (same object) if neither repair did
    anything, so a caller can cheaply check via identity if it ever needs
    to."""
    corrected_claims = [_autocorrect_row_index(claim, structured_data) for claim in draft.claims]

    seen_values = [float(claim.value) for claim in corrected_claims]
    added: list[GroundedClaim] = []
    for value, candidate_units in _numeral_matches_with_candidate_units(draft.answer_text):
        if any(abs(value - existing) <= _OMISSION_TOLERANCE for existing in seen_values):
            continue
        claim = _find_matching_evidence(value, candidate_units, structured_data)
        if claim is not None:
            added.append(claim)
            seen_values.append(value)

    if corrected_claims == draft.claims and not added:
        return draft
    return draft.model_copy(update={"claims": [*corrected_claims, *added]})


def check_grounding(draft: DraftAnswer, structured_data: list[BaseModel]) -> GroundingCheckResult:
    """Never raises. Adds no OpenAI API call itself in any case -- purely
    structural validation against data already in hand; the repair *re-run*
    this triggers on failure is the orchestrator's job, not this function's."""
    invalid_indices: list[int] = []
    reasons: list[str] = []
    for index, claim in enumerate(draft.claims):
        failure = _claim_failure_reason(claim, structured_data)
        if failure is not None:
            invalid_indices.append(index)
            reasons.append(f"claim {index}: {failure}")

    omissions = _find_unevidenced_omissions(draft)
    if omissions:
        reasons.append(f"answer_text states {omissions} with no matching claim")
    denylisted = _find_denylisted_phrases(draft.answer_text)
    if denylisted:
        reasons.append(f"answer_text contains denylisted phrase(s): {denylisted}")

    return GroundingCheckResult(
        valid=not invalid_indices and not omissions and not denylisted,
        invalid_claim_indices=invalid_indices,
        unevidenced_omissions=omissions,
        denylisted_phrases=denylisted,
        reasons=reasons,
    )


def build_repair_instruction(check: GroundingCheckResult) -> str:
    """A corrective instruction appended to the repair re-run's input --
    specific enough that the model can plausibly fix the actual problem,
    without ever telling it what number to say (that would just be a
    different route to fabrication)."""
    return (
        "Your previous answer failed an automated grounding check for the following reason(s): "
        + "; ".join(check.reasons)
        + ". Provide a corrected answer: call the necessary tool(s) again this turn, state only "
        "figures a tool call this turn actually returned, and give every stated currency/"
        "percentage figure a GroundedClaim citing the exact result/row/field it came from. "
        "Do not state a figure you cannot cite, and do not state a cause/reason unless a tool "
        "result explicitly provided one."
    )


#: Fields that are legitimately `None` by design (e.g. `InsightCandidate.la_code`
#: for a scope-wide observation) -- never a suppressed *value*, so
#: `SUPPRESSION_MESSAGE` (a specific, strong claim: "ONS does not report a
#: value") must not be attached to them. A blank marker, not silence,
#: still distinguishes "not applicable" from a fabricated value.
_IDENTITY_FIELDS = frozenset({"la_code", "la_name", "region_country_code", "region_country_name"})
_BLANK = "—"


def _plain_field_summary(model: BaseModel) -> str:
    parts = []
    for key, value in model.model_dump().items():
        if isinstance(value, (list, dict)):
            continue  # bulk/nested fields are skipped in this plain summary
        if value is not None:
            parts.append(f"{key}={value}")
        elif key in _IDENTITY_FIELDS:
            parts.append(f"{key}={_BLANK}")
        else:
            parts.append(f"{key}={SUPPRESSION_MESSAGE}")
    return ", ".join(parts)


def _plain_result_summary(result: BaseModel) -> str:
    found = list_row_field_and_type(result)
    if found is None:
        return _plain_field_summary(result)
    field_name, _ = found
    rows = list(getattr(result, field_name))
    if not rows:
        return "no rows"
    # Capped so a large ranking/candidate set can't produce a runaway wall
    # of text in the one path specifically meant to be a safe, minimal answer.
    return "; ".join(_plain_field_summary(row) for row in rows[:10])


def build_templated_fallback(structured_data: list[BaseModel]) -> str:
    """AC2's "templated, tool-output-only answer": a purely mechanical
    projection of this turn's tool outputs, with no model-authored prose at
    all -- released only when the model's own drafted answer couldn't be
    verified even after one repair attempt. `InsightCandidate` rows never
    appear here with a fabricated reason attached, since this function
    reads only the fields already on the object (design's own no-cause-
    field guarantee for that schema, `ADR-017`)."""
    if not structured_data:
        return (
            "I wasn't able to produce a fully verified answer to this question, and no data was "
            "retrieved this turn. Please try rephrasing your question, or use the Explore trends / "
            "Compare and rank tabs directly."
        )
    lines = [
        "I couldn't produce a fully verified answer to this question, so here is the underlying "
        "data retrieved this turn:"
    ]
    for result in structured_data:
        lines.append(f"- {type(result).__name__}: {_plain_result_summary(result)}")
    return "\n".join(lines)
