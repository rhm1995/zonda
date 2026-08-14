"""Canonical Pydantic schemas shared by the deterministic core, the
dashboard UI, and (from Increment 3 onward) the agent's tool layer.

Schemas through Increment 3 ("Ask the data" walking skeleton) are defined
here: `PricePoint`/`LocalAuthority`/`Period` (ingestion + repository
layer), `PriceLookupResult`/`TrendResult`/`GrowthMetricsResult`
(TASK-003), `PremiumResult`/`PremiumTrendResult`/`PremiumSeriesResult`
(TASK-004), `RankingResult`/`ComparisonResult`/`RankedArea`/
`RankingCoverageSummary` (TASK-005), and `GeographyMatch`/`ChartSpec`/
`EvidenceRef`/`GroundedClaim`/`DraftAnswer`/`RecentMessage`/
`ConversationSession`/`ToolCallLog`/`AgentTurnResult` (STORY-003, design
§6.3/§8.2). `PeriodMatch` (TASK-019), `InsightCandidate`/`PatternScanResult`
(TASK-006) are Increment 4 additions, defined alongside the schemas above.

Field shapes follow design §6.3 exactly, including which values may be
`None` (suppressed/missing) — see ADR-018: a `None`/`suppressed` value is
never rendered or exported as zero.
"""

from __future__ import annotations

from datetime import date
from typing import Literal, get_args, get_origin
from uuid import uuid4

from pydantic import BaseModel, Field

Dataset = Literal["new_build", "existing"]
ClaimUnit = Literal["gbp", "pct", "pct_point", "count", "cagr_pct"]


class PricePoint(BaseModel):
    """One area/dataset/period observation (design §6.3)."""

    dataset: Dataset
    region_country_code: str
    region_country_name: str
    la_code: str
    la_name: str
    period_label: str
    period_end_date: date
    price_gbp: int | None = None
    suppressed: bool = False


class LocalAuthority(BaseModel):
    """One row of the geography reference table (design §6.3)."""

    la_code: str
    la_name: str
    region_country_code: str
    region_country_name: str
    aliases: list[str] = Field(default_factory=list)


class GeographyMatch(BaseModel):
    """(CMP-003) Result of resolving a free-text place name -- used only by
    the "Ask the data" tab (ADR-012); the two deterministic tabs' closed
    selectors never produce this ambiguity in the first place. Never
    guesses silently below a confidence threshold: `ambiguous`/`not_found`
    are returned instead of a best-effort pick."""

    query_text: str
    status: Literal["matched", "ambiguous", "out_of_coverage", "not_found"]
    matches: list[LocalAuthority] = Field(default_factory=list)
    coverage_note: str | None = None


class Period(BaseModel):
    """(v10, ADR-016) The canonical, typed representation of a dataset
    period — pairs the human-readable ONS label with its parsed end date,
    so every comparison/range-filter uses `end_date`, never `label` text
    (which does not sort chronologically)."""

    label: str
    end_date: date


class PeriodMatch(BaseModel):
    """(v10, ADR-016, TASK-019) Mirrors `GeographyMatch`'s shape for the time
    dimension -- a natural-language period expression resolved by
    `core/period.py`. `assumption_note` is populated whenever a detail was
    inferred rather than stated (e.g. a bare year's month) and must be
    surfaced in the answer, never applied silently. `suggestions` is
    populated for `out_of_range`, the same non-fabrication posture
    `GeographyMatch` already uses for geography."""

    query_text: str
    status: Literal["resolved", "resolved_with_assumption", "range_resolved", "out_of_range", "not_found"]
    period: Period | None = None
    period_range: tuple[Period, Period] | None = None
    assumption_note: str | None = None
    suggestions: list[Period] = Field(default_factory=list)


class PremiumRow(BaseModel):
    """Repository-layer output of `get_premium_series` (design §6.7/§8.6) —
    one area/period row with both datasets' raw figures already joined by
    the SQL, before any premium formula is applied. `core/tools.py` turns
    this into a `PremiumResult`/`PremiumSeriesResult`; it is never returned
    to a UI/agent caller directly."""

    la_code: str
    la_name: str
    period_label: str
    period_end_date: date
    new_build_price: int | None
    new_build_suppressed: bool
    existing_price: int | None
    existing_suppressed: bool


class PriceLookupResult(BaseModel):
    """Backs `median_price_lookup` (FR-002)."""

    la_code: str
    la_name: str
    dataset: Dataset
    period_label: str
    price_gbp: int | None
    suppressed: bool


class TrendResult(BaseModel):
    """Backs `price_trend` (FR-004) — the full per-period series across a
    range, powering Explore Trends' price-mode chart. `suppressed_periods`
    duplicates what `points[].suppressed` already encodes, as an explicit,
    directly-usable list (mirrors `GrowthMetricsResult.suppressed_periods`,
    the same UI convenience)."""

    la_code: str
    la_name: str
    dataset: Dataset
    period_start_label: str
    period_end_label: str
    points: list[PricePoint]
    suppressed_periods: list[str]


class GrowthMetricsResult(BaseModel):
    """(v2) Backs FR-029-FR-032 in "Explore trends". Same object renders on
    screen and serialises to CSV via ui/export.py — a single source of
    truth for both (DR-008/NFR-012 hold by construction, not convention)."""

    la_code: str
    la_name: str
    dataset: Dataset
    period_start_label: str
    period_end_label: str
    latest_price: int | None
    latest_price_period: str | None
    growth_gbp: float | None
    growth_pct: float | None
    cagr_pct: float | None
    suppressed_periods: list[str]


class PremiumResult(BaseModel):
    """Point-in-time new-build premium for one area/period (ASM-003)."""

    la_code: str
    la_name: str
    period_label: str
    new_build_price: int | None
    existing_price: int | None
    premium_pct: float | None
    premium_gbp: int | None
    suppressed_components: list[Literal["new_build", "existing"]]


class PremiumTrendResult(BaseModel):
    """(v5) Premium *change* between two periods — distinct from
    `PremiumResult`'s point-in-time reading. Powers ranking-by-premium-
    change (TASK-005, Increment 2); defined here alongside `TASK-004`'s
    other premium schemas since `premium_trend` itself is an Increment 1
    function."""

    la_code: str
    la_name: str
    period_start_label: str
    period_end_label: str
    start_premium_pct: float | None
    start_premium_gbp: int | None
    end_premium_pct: float | None
    end_premium_gbp: int | None
    premium_percentage_point_change: float | None
    premium_gbp_change: float | None
    suppressed_components: list[
        Literal["start_new_build", "start_existing", "end_new_build", "end_existing"]
    ]


class PremiumSeriesResult(BaseModel):
    """(v8/v9) Backs Explore Trends' premium-mode chart — premium at every
    period across the selected range, reusing `PremiumResult` as the
    per-period row type."""

    la_code: str
    la_name: str
    period_start_label: str
    period_end_label: str
    points: list[PremiumResult]


#: The full metric vocabulary `rank_areas`/`compare_areas` (TASK-005)
#: support. "price"/"premium_pct"/"premium_gbp" are *level* metrics (a
#: single `Period`); the rest are *range* metrics (a `(start, end)` pair) --
#: `core/tools.py` validates which shape a given metric requires.
RankingMetric = Literal[
    "price",
    "growth_pct",
    "growth_gbp",
    "cagr_pct",
    "premium_pct",
    "premium_gbp",
    "premium_percentage_point_change",
    "premium_gbp_change",
]


class RankingCoverageSummary(BaseModel):
    """(v6, ADR-014) Aggregate view of a ranking/comparison call's scope --
    `excluded_examples` is capped at 5 entries for citation purposes only;
    `areas_excluded` is the authoritative count, not the length of this
    list."""

    areas_in_scope: int
    areas_ranked: int
    areas_excluded: int
    excluded_examples: list[str] = Field(default_factory=list)


class RankedArea(BaseModel):
    """One area's row in a `RankingResult`/`ComparisonResult`. `rank` is
    unused/omitted (left at 0) in `ComparisonResult`'s unordered mode."""

    rank: int
    la_code: str
    la_name: str
    value: float | None
    suppressed: bool


class RankingResult(BaseModel):
    """Backs FR-005 (Ask the data) and FR-038 (Compare and rank). `rank_areas`
    computes this in a single internal call -- fetch, join, compute,
    exclude, rank (`ADR-014`); only the requested top-`n` rows plus
    `coverage` cross the tool boundary, never the full scanned scope."""

    metric: RankingMetric
    period_label_or_range: str
    direction: Literal["top", "bottom"]
    rows: list[RankedArea]
    coverage: RankingCoverageSummary


class ComparisonResult(BaseModel):
    """Backs FR-003 (Ask the data) and the multi-area path of FR-035-FR-039
    (Compare and rank) when comparing a fixed, explicitly-named set of
    areas rather than ranking a scope. Unlike `RankingResult`, every
    requested area that exists in the geography reference gets a row here
    (suppressed areas are flagged, not dropped) -- there is no top-`n`
    truncation to make room for."""

    metric: RankingMetric
    period_label_or_range: str
    areas: list[RankedArea]
    coverage: RankingCoverageSummary


#: (v11, ADR-017) The fixed, revisable menu of insight-candidate
#: categories `scan_for_patterns` (TASK-006) computes -- a genuinely novel
#: insight shape is simply absent from a scan's `candidates`, never forced
#: into an existing category.
InsightCategory = Literal[
    "growth_leader",
    "growth_laggard",
    "regional_growth_distribution",
    "premium_expansion",
    "premium_contraction",
    "regional_divergence",
    "period_on_period_movement",
    "coverage_gap",
]


class InsightCandidate(BaseModel):
    """(v11, ADR-017) One deterministically-computed observation from
    `scan_for_patterns` -- never causal, never a full narrative. The agent
    selects and narrates a bounded subset (typically 3, FR-009) from the
    full candidate set it receives; it never invents a candidate, its
    category, its evidence, or its value. Deliberately has no "cause"/
    "reason" field -- causal interpretation is structurally excluded from
    what this schema can express, not merely discouraged in prose."""

    category: InsightCategory
    salience_rank: int
    la_code: str | None = None
    la_name: str | None = None
    value: float | None = None
    value_unit: Literal["pct", "gbp", "count", "pct_point"] | None = None
    evidence_ids: list[str] = Field(default_factory=list)
    data_completeness: Literal["complete", "partial", "insufficient"]
    summary: str


class PatternScanResult(BaseModel):
    """(v11, formalised) `scan_for_patterns`' output: a bounded, categorised
    candidate set, never rendered as-is. The agent selects and narrates a
    subset from it (ADR-017); this object is not itself the final answer."""

    scope_description: str
    candidates: list[InsightCandidate] = Field(default_factory=list)
    coverage: RankingCoverageSummary


# -- Agent path (STORY-003 onward) -------------------------------------------


class ChartSpec(BaseModel):
    """(v7, ADR-015) A chart the agent may request alongside its answer,
    selected from a small fixed menu -- never generated as code or a
    chart-config structure. `x_field`/`y_fields` are validated, at render
    time, against the referenced result's actual fields (`CMP-017`,
    Increment 4); an invalid spec degrades to table-only rendering, never a
    crash and never a silently-wrong chart."""

    chart_type: Literal["line", "bar", "grouped_bar"]
    source_result_index: int
    x_field: str
    y_fields: list[str]
    title: str


class EvidenceRef(BaseModel):
    """(v14) The atomic unit a claim can cite: one field on one row of one
    of this turn's `structured_data` entries. `row_index` is `None` for a
    scalar-shaped result (e.g. `PriceLookupResult`); required for a
    list-valued result (e.g. an index into `RankingResult.rows`)."""

    result_index: int
    row_index: int | None = None
    field: str


class GroundedClaim(BaseModel):
    """(v14, ADR-009 revised) One number the agent's draft answer states,
    linked to the exact tool-output field(s) it came from. Structural
    validation of these (do the evidence refs resolve? does the value
    match? is the unit right?) is `TASK-010`'s job (Increment 4) -- this
    story only needs the agent to emit the shape correctly, per `TASK-010`'s
    own implementation note ("avoiding a rework gap between the two")."""

    value: float | int
    unit: ClaimUnit
    la_code: str | None = None
    period_label: str | None = None
    #: (TASK-010) bounded at 3, per design §6.3 -- a claim with more
    #: citations than that is rejected by schema validation itself, not
    #: left to the grounding guardrail to catch after the fact.
    evidence: list[EvidenceRef] = Field(default_factory=list, max_length=3)


class DraftAnswer(BaseModel):
    """(v14) The Agent's `output_type` -- `answer_text` plus its own
    citation list, so `CMP-008` (Increment 4) validates structure rather
    than re-deriving it by regex over prose.

    **(Increment 4, STORY-008)** `status` mirrors a subset of
    `AgentTurnResult.status` -- the model states its own turn intent
    structurally (a clarifying question has no figures to ground-check;
    a declined/out-of-coverage explanation likewise), the same "structural
    over lexical" shift `ADR-009` already made for claims, applied here to
    turn intent. `"unavailable"` is deliberately absent -- that status is
    exclusively the orchestrator's to set, on an API/infrastructure
    failure the model itself never decides.

    **(Increment 4, STORY-007)** `coverage_caveats` mirrors
    `AgentTurnResult.coverage_caveats` 1:1 -- plain informational strings
    (e.g. "Scotland and Northern Ireland are not covered by this data"),
    not numeric claims, so they need no grounding validation of their own;
    they only need to reach the UI, which this direct mirroring does.

    **(Increment 4, STORY-004)** `period_assumptions` mirrors
    `AgentTurnResult.period_assumptions` the same way -- e.g. a bare
    year's inferred month (`resolve_period`'s own `assumption_note`, which
    the model must restate here, not apply silently) -- surfaced in
    `FR-024`'s expandable detail view."""

    answer_text: str
    claims: list[GroundedClaim] = Field(default_factory=list)
    chart_spec: ChartSpec | None = None
    status: Literal["answered", "clarification_needed", "declined"] = "answered"
    coverage_caveats: list[str] = Field(default_factory=list)
    period_assumptions: list[str] = Field(default_factory=list)


class RecentMessage(BaseModel):
    """(v15) One turn's user question or the agent's rendered answer text,
    kept verbatim -- captures *how* something was asked (a pronoun, an
    elliptical follow-up), which the structured fields below cannot."""

    role: Literal["user", "assistant"]
    text: str


class ConversationSession(BaseModel):
    """(v15, ADR-008 revised) Per-session state passed into every turn: a
    bounded verbatim recent-message window, plus compact structured state
    reflecting only the most recent turn (never an accumulating history) --
    both parts stay independently bounded, keeping per-turn token cost
    roughly flat across a long follow-up chain (RSK-001/NFR-006). Follow-up
    resolution itself (`last_area_codes` etc. actually being read to
    resolve "those areas") is `STORY-005`'s job (Increment 4); this story's
    single-question scope only needs the shape to exist and be threaded
    through `answer_question` correctly.

    **(TASK-015)** `session_id`/`turn_number` exist purely for structured-log
    correlation (design §12) -- neither is read by any grounding, follow-up,
    or scoring logic anywhere else. `session_id` is generated once, at
    construction, and carried forward unchanged by every `record_turn` call
    for the rest of that session; `turn_number` starts at 0 and is
    incremented by `record_turn` on every completed turn."""

    session_id: str = Field(default_factory=lambda: uuid4().hex)
    turn_number: int = 0
    recent_messages: list[RecentMessage] = Field(default_factory=list)
    last_area_codes: list[str] = Field(default_factory=list)
    last_region_scope: str | None = None
    last_start_period: Period | None = None
    last_end_period: Period | None = None
    last_metric: str | None = None
    last_dwelling_status: Literal["new_build", "existing", "both"] | None = None
    last_result_reference: str | None = None


class ToolCallLog(BaseModel):
    """One tool invocation's observability record (design §8.2's
    `AgentTurnResult.tool_calls` -- referenced there as "name, args,
    latency" but, like several schemas this document's own revision
    history formalised after first being referenced only in prose
    (`PremiumTrendResult` v5, `ChartSpec` v7, `PatternScanResult` v11),
    never given its own field list. Formalised here on the same basis:
    name, the arguments the model supplied, and observed latency."""

    tool_name: str
    arguments: dict = Field(default_factory=dict)
    latency_ms: float


class AgentTurnResult(BaseModel):
    """(design §8.2) The UI/eval-agnostic result of one `answer_question`
    call. Never constructed by raising an exception for an expected
    failure mode (API unavailable, ambiguous question, ...) -- `status`
    represents that instead, so callers render rather than catch."""

    status: Literal["answered", "clarification_needed", "declined", "unavailable"]
    answer_text: str
    structured_data: list[BaseModel] = Field(default_factory=list)
    claims: list[GroundedClaim] = Field(default_factory=list)
    tool_calls: list[ToolCallLog] = Field(default_factory=list)
    coverage_caveats: list[str] = Field(default_factory=list)
    chart_spec: ChartSpec | None = None
    period_assumptions: list[str] = Field(default_factory=list)


# -- Shared result-object reflection (TASK-010, TASK-018) --------------------
#
# Both `ui/charts.py` (CMP-017, addressing a `ChartSpec`'s `x_field`/
# `y_fields`) and `agent/guardrails.py` (CMP-008, addressing a
# `GroundedClaim`'s `EvidenceRef`) need to resolve "the row a list-valued
# result's index points at" against the same schemas. Defined once here,
# in `core`, so neither module depends on the other (the design's
# dependency-direction rule, §9) and so the two addressing schemes can
# never silently diverge.


def list_row_field_and_type(result: BaseModel) -> tuple[str, type[BaseModel]] | None:
    """Finds the one field on `result` that holds a `list[BaseModel]` of
    per-row items (e.g. `RankingResult.rows`, `TrendResult.points`,
    `PatternScanResult.candidates`), found generically from the model's own
    field annotations -- a new result type with a differently-named list
    field needs no change here. `None` for a flat, scalar-shaped result
    (e.g. `PriceLookupResult`, `PremiumResult`)."""
    for name, field in type(result).model_fields.items():
        annotation = field.annotation
        if get_origin(annotation) is list:
            args = get_args(annotation)
            if args and isinstance(args[0], type) and issubclass(args[0], BaseModel):
                return name, args[0]
    return None


def resolve_row(result: BaseModel, row_index: int | None) -> BaseModel | None:
    """The object an `EvidenceRef`/`ChartSpec` addressing scheme ultimately
    points at: `result` itself when `row_index` is `None` (a scalar-shaped
    result), or the `row_index`'th item of its list-row field otherwise.
    Never raises -- an out-of-range `row_index`, or one given against a
    scalar result with no list-row field, returns `None`."""
    if row_index is None:
        return result
    found = list_row_field_and_type(result)
    if found is None:
        return None
    field_name, _ = found
    rows = getattr(result, field_name)
    if not (0 <= row_index < len(rows)):
        return None
    return rows[row_index]


#: (v14) Fixes the claim `unit` for every field whose meaning is fixed
#: regardless of context. `RankedArea.value` (context-dependent on its
#: parent `RankingResult`/`ComparisonResult.metric`) and
#: `InsightCandidate.value` (carries its own `value_unit` sibling field)
#: are deliberately absent here -- resolved by `resolve_field_unit` below
#: instead, never by a second lookup table.
FIELD_UNITS: dict[str, ClaimUnit] = {
    "price_gbp": "gbp",
    "latest_price": "gbp",
    "new_build_price": "gbp",
    "existing_price": "gbp",
    "growth_gbp": "gbp",
    "growth_pct": "pct",
    "cagr_pct": "cagr_pct",
    "premium_gbp": "gbp",
    "premium_pct": "pct",
    "start_premium_gbp": "gbp",
    "start_premium_pct": "pct",
    "end_premium_gbp": "gbp",
    "end_premium_pct": "pct",
    "premium_gbp_change": "gbp",
    "premium_percentage_point_change": "pct_point",
    "rank": "count",
    "areas_in_scope": "count",
    "areas_ranked": "count",
    "areas_excluded": "count",
    "salience_rank": "count",
}

#: `RankedArea.value`'s unit depends on its parent `RankingResult`/
#: `ComparisonResult.metric` -- this is that resolution, not a second,
#: independent unit table (design §6.3, v14 closing note).
_METRIC_TO_CLAIM_UNIT: dict[str, ClaimUnit] = {
    "price": "gbp",
    "growth_gbp": "gbp",
    "growth_pct": "pct",
    "cagr_pct": "cagr_pct",
    "premium_gbp": "gbp",
    "premium_pct": "pct",
    "premium_gbp_change": "gbp",
    "premium_percentage_point_change": "pct_point",
}


def resolve_field_unit(row: BaseModel, field: str, parent: BaseModel) -> ClaimUnit | None:
    """The unit a `GroundedClaim` citing `field` on `row` must declare.
    `None` if `field` has no fixed or resolvable unit (an unsupported
    citation -- the guardrail treats this as invalid)."""
    if field in FIELD_UNITS:
        return FIELD_UNITS[field]
    if isinstance(row, RankedArea) and field == "value":
        metric = getattr(parent, "metric", None)
        return _METRIC_TO_CLAIM_UNIT.get(metric) if isinstance(metric, str) else None
    if isinstance(row, InsightCandidate) and field == "value":
        return row.value_unit
    return None
