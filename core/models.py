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
§6.3/§8.2). `PatternScanResult`/`InsightCandidate`/`PeriodMatch` (full
period resolution) arrive with the Increment 4 tickets that own them —
deliberately not stubbed here ahead of time (YAGNI).

Field shapes follow design §6.3 exactly, including which values may be
`None` (suppressed/missing) — see ADR-018: a `None`/`suppressed` value is
never rendered or exported as zero.
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

Dataset = Literal["new_build", "existing"]


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
    unit: Literal["gbp", "pct", "pct_point", "count", "cagr_pct"]
    la_code: str | None = None
    period_label: str | None = None
    evidence: list[EvidenceRef] = Field(default_factory=list)


class DraftAnswer(BaseModel):
    """(v14) The Agent's `output_type` -- `answer_text` plus its own
    citation list, so `CMP-008` (Increment 4) validates structure rather
    than re-deriving it by regex over prose."""

    answer_text: str
    claims: list[GroundedClaim] = Field(default_factory=list)
    chart_spec: ChartSpec | None = None


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
    through `answer_question` correctly."""

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
