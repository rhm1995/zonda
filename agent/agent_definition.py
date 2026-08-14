"""Agent definition (`STORY-003`, `TASK-009`, design `CMP-005`/`CMP-006`).

Wraps `core`'s full deterministic tool library as Agents SDK
`function_tool`s -- the "dual-consumer" point of `ADR-001`/`ADR-011`: the
same implementations already proven by `EPIC-02`'s unit tests and by
"Explore trends"/"Compare and rank" are reused, not reimplemented, for the
chat path.

**Free-text resolution happens inside each wrapper, not before it**: every
`area` argument is resolved via `core.geography.resolve_geography`; every
period argument is resolved via `core.period.resolve_period` -- both are
also exposed as their own standalone tools (`resolve_geography`,
`resolve_period`) so the model can pre-check an ambiguous/out-of-coverage/
out-of-range input and phrase a clarifying question before attempting an
analysis call, mirroring design §7.4/§7.4a. A wrapper never forwards a raw
area/period string to its underlying `core` function -- only an already-
resolved `la_code`/`Period` ever crosses that boundary (`TASK-009` AC3/AC4).

Every wrapper returns a structured, non-throwing dict -- `{"status": "ok",
...}` on success, or `{"status": <error>, "message": ...}` on a resolution
or validation failure -- so a tool call never raises past the Agents SDK
boundary (design's "structured, non-throwing tool result" discipline).
Every result dict uses `model_dump(mode="json")`, not the default python
mode, so `date` fields serialise safely regardless of how the SDK's own
wire encoding works.
"""

from __future__ import annotations

from typing import Literal

from agents import Agent, FunctionTool, function_tool

from core.errors import CoreError
from core.geography import resolve_geography as _resolve_geography
from core.models import DraftAnswer, Period
from core.period import resolve_period as _resolve_period
from core.repository import Repository
from core.tools import (
    LEVEL_METRICS,
    RANGE_METRICS,
    compare_areas,
    growth_metrics,
    median_price_lookup,
    new_build_premium,
    premium_series,
    premium_trend,
    price_trend,
    rank_areas,
    scan_for_patterns,
)

SYSTEM_INSTRUCTIONS = """\
You are a housing-market analysis assistant for UK detached-house prices \
(newly built and existing dwellings), England and Wales local authorities \
only, ONS "year ending September 2025" edition (HM Land Registry \
price-paid data).

Every response has a `status`:
- "answered" -- you have a substantive answer (fully or partly grounded in \
a tool call this turn).
- "clarification_needed" -- you are asking the user a clarifying question \
instead of answering (e.g. an ambiguous area) -- answer_text should be \
only the question, with no figure stated and no claims.
- "declined" -- you cannot answer at all (e.g. the entire request is \
outside the supplied data's coverage) -- explain why plainly.
Never use "answered" for a response that is actually a clarifying \
question or a decline.

Rules:
- Use a tool for every factual question. Never state a price, growth, \
premium, ranking, or pattern figure you did not just obtain from a tool \
call this turn.
- For a question asking how a single area's price has changed over a \
period ("how has X changed", "trend since Y"), call growth_metrics to get \
the actual growth £/%/CAGR figure to state -- price_trend alone gives you \
the per-period series but no growth figure, and you must never compute one \
yourself from its raw points. Call price_trend as well only when the whole \
series (e.g. for a chart) is also needed, not instead of growth_metrics.
- If an area name is ambiguous or resolves to more than one place \
(status="ambiguous" from a tool), respond with status="clarification_needed" \
and ask the user which one they meant, naming every candidate the tool \
returned -- never guess, and never proceed with the analysis until they \
answer.
- If an area is outside the supplied data's coverage (status="out_of_coverage" \
-- Scotland and Northern Ireland are not covered), say so plainly and \
explain why (HM Land Registry price-paid data covers England & Wales \
only). If the question also names covered areas, still answer fully for \
those (status="answered") and add one entry per excluded area/region to \
coverage_caveats stating plainly that it is outside the supplied data -- \
never a blanket refusal, and never a fabricated figure for the excluded \
area. If *every* named area is out of coverage, use status="declined" \
instead -- there is nothing left to answer, and answer_text alone \
(coverage_caveats is for a *partial* answer's exclusions, not a full \
decline's whole reason).
- This dataset covers detached houses only (newly built and existing). If \
asked about any other dwelling type (semi-detached, terraced, flats/ \
maisonettes, or any type not named "detached"), use status="declined" and \
say plainly that this data only covers detached houses -- never substitute \
a detached-house figure and present it as if it were that other dwelling \
type's price; a wrong-but-plausible-looking figure for the wrong dwelling \
type is exactly the kind of confidently-wrong answer you must not give.
- If a period expression is ambiguous, out of range, or unparseable \
(status="out_of_range"/"not_found" from resolve_period), respond with \
status="clarification_needed" and offer the nearest available periods the \
tool returned in `suggestions` -- the same conservative path as an \
ambiguous area, never a guess at a period that doesn't exist in the data.
- If a tool result includes an assumption_note/assumption_notes field \
(e.g. a bare year inferred as "year ending September"), copy that exact \
text into period_assumptions -- never apply an assumption silently.
- For a question about a trend, ranking, or comparison, set chart_spec to \
help the user see the answer, not just read it: chart_type "line" for a \
single area's series over time (x_field the period field, e.g. \
"period_label"; y_fields the value field, e.g. "price_gbp"), "bar" for a \
single-metric ranking/comparison (x_field "la_name", y_fields ["value"]), \
"grouped_bar" for comparing more than one metric/series at once. \
source_result_index is the position of the relevant result in this \
turn's tool outputs (0 for the first tool call this turn, 1 for the \
second, ...). Omit chart_spec entirely for a single-figure lookup with \
nothing to chart, or for a clarification_needed/declined response.
- If a tool reports a figure as suppressed/unavailable, say it is \
unavailable -- never state 0, and never invent a reason (e.g. "small \
sample size") unless the tool result itself stated one.
- Every numeric price/growth/premium/ranking/pattern figure you state in \
answer_text must have a corresponding entry in claims, citing which tool \
result and field it came from. For a figure inside a list-valued result \
(e.g. a row of rank_areas'/compare_areas'/scan_for_patterns' output), set \
row_index to that row's position and field to its exact field name (e.g. \
"value"). For a top-level figure (e.g. growth_metrics' growth_gbp), set \
row_index to null. Set period_label only to the exact label/range text the \
tool result itself gave -- never a label you constructed yourself.
- Do not speculate about *why* a figure is what it is, or state a cause \
for a missing/suppressed value that the tool result did not itself state.
- A question may reference an earlier turn ("those areas", "the same \
metric", "what about them instead"). A "[Context from your previous \
turn...]" note, when present, gives you the resolved facts; the messages \
above it give you the exact phrasing. If the question clearly refers back \
but there is no earlier turn or no "[Context...]" note to resolve it from, \
say plainly that you have no relevant prior context for that, rather than \
guessing what it might mean.
- "England"/"Wales"/"Britain"/"the UK" are not local authorities and will \
not resolve as an area -- for a nationwide or regional question, use \
rank_areas/compare_areas/scan_for_patterns with scope omitted (covers \
every local authority) rather than trying to look up a country/region as \
if it were one area.
- rank_areas/compare_areas/scan_for_patterns take *either* `period` (for a \
level metric: price, premium_pct, premium_gbp) *or* `period_start`+\
`period_end` (for a change metric: growth_pct, growth_gbp, cagr_pct, \
premium_percentage_point_change, premium_gbp_change) -- never both.
- For a broad, open-ended question ("analyse...", "identify notable \
patterns...", no single specific figure asked for), call scan_for_patterns \
once, then select exactly 3 candidates from *different* categories (never \
two from the same category) and narrate each purely descriptively -- what \
the figure is, never why it is that way. Give each one its own claim \
citing that candidate's own "value" field (row_index = that candidate's \
position in the candidates list, field="value"; leave period_label null \
-- scan_for_patterns' candidates don't carry a single period/range field \
of their own to cite exactly). Never invent an \
observation, category, or figure the tool's candidates didn't already \
contain. If the candidate set has fewer than 3 usable entries for the \
requested scope, say so plainly and narrate what is available rather than \
padding with an invented one.
"""

_ANALYSIS_ERROR_STATUS = "invalid_arguments"


# -- Shared free-text resolution helpers -------------------------------------


def _area_error(match) -> dict:
    error: dict = {
        "status": match.status,
        "message": match.coverage_note or f"Could not resolve area {match.query_text!r} ({match.status}).",
    }
    if match.status == "ambiguous":
        error["candidates"] = [la.la_name for la in match.matches]
    return error


def _resolve_area(repository: Repository, area_text: str) -> tuple[str, None] | tuple[None, dict]:
    """`(la_code, None)` on a clean match; `(None, error_dict)` otherwise --
    never raises, and never forwards the raw `area_text` to a `core`
    function (`TASK-009` AC3)."""
    match = _resolve_geography(repository, area_text)
    if match.status != "matched":
        return None, _area_error(match)
    return match.matches[0].la_code, None


def _resolve_area_scope(repository: Repository, area_texts: list[str] | None) -> tuple[list[str] | Literal["all"], list[str]]:
    """`None`/omitted means the full covered scope. Each named area is
    resolved independently; an entry that doesn't resolve is *dropped, not
    fabricated* and reported in the returned warnings list (mirrors
    `core.tools`' own never-silently-drop-without-a-count discipline, one
    layer up at the free-text-resolution boundary)."""
    if area_texts is None:
        return "all", []
    resolved: list[str] = []
    warnings: list[str] = []
    for text in area_texts:
        la_code, error = _resolve_area(repository, text)
        if la_code is None:
            warnings.append(f"{text!r}: {error['message']}")  # type: ignore[index]
        else:
            resolved.append(la_code)
    return resolved, warnings


def _period_error(match) -> dict:
    error: dict = {
        "status": match.status,
        "message": f"Could not resolve period {match.query_text!r} ({match.status}).",
    }
    if match.suggestions:
        error["suggestions"] = [p.label for p in match.suggestions]
    return error


def _resolve_single_period(repository: Repository, period_text: str) -> tuple[Period, str | None, None] | tuple[None, None, dict]:
    """`(period, assumption_note, None)` on a single-period resolution;
    `(None, None, error_dict)` otherwise -- including when `period_text`
    resolved to a *range* (e.g. "since 2015") where a single period was
    needed, since that is exactly the kind of raw/unresolved value that
    must never reach a `core` function directly (`TASK-009` AC4)."""
    match = _resolve_period(repository, period_text)
    if match.period is not None:
        return match.period, match.assumption_note, None
    if match.period_range is not None:
        return None, None, {
            "status": "not_found",
            "message": (
                f"{period_text!r} resolved to a range, not a single period. Use the resolve_period tool "
                "first, then pass a single month/year (e.g. 'September 2025') here."
            ),
        }
    return None, None, _period_error(match)


def _resolve_period_range(
    repository: Repository, period_start_text: str, period_end_text: str
) -> tuple[tuple[Period, Period], list[str], None] | tuple[None, None, dict]:
    """Two independently-resolved single periods -- not `resolve_period`'s
    own "since X"/"last N years" range syntax, which names one endpoint
    implicitly. A tool needing an explicit range takes two arguments
    instead, matching the underlying `core` function's own two-`Period`
    signature exactly."""
    start, start_note, start_error = _resolve_single_period(repository, period_start_text)
    if start_error is not None:
        return None, None, start_error
    end, end_note, end_error = _resolve_single_period(repository, period_end_text)
    if end_error is not None:
        return None, None, end_error
    assert start is not None and end is not None
    notes = [note for note in (start_note, end_note) if note]
    return (start, end), notes, None


def _core_error(exc: CoreError) -> dict:
    return {"status": _ANALYSIS_ERROR_STATUS, "message": str(exc)}


def _ok(result) -> dict:
    return {"status": "ok", **result.model_dump(mode="json")}


# -- resolve_geography / resolve_period (also standalone tools) -------------


def resolve_geography_impl(repository: Repository, query_text: str) -> dict:
    return _resolve_geography(repository, query_text).model_dump(mode="json")


def resolve_period_impl(repository: Repository, query_text: str) -> dict:
    return _resolve_period(repository, query_text).model_dump(mode="json")


# -- median_price_lookup ------------------------------------------------------


def median_price_lookup_impl(repository: Repository, area: str, dataset: Literal["new_build", "existing"], period: str) -> dict:
    la_code, area_error = _resolve_area(repository, area)
    if area_error is not None:
        return area_error
    resolved_period, assumption_note, period_error = _resolve_single_period(repository, period)
    if period_error is not None:
        return period_error
    assert la_code is not None and resolved_period is not None
    try:
        result = median_price_lookup(repository, la_code, dataset, resolved_period)
    except CoreError as exc:
        return _core_error(exc)
    payload = _ok(result)
    if assumption_note:
        payload["assumption_note"] = assumption_note
    return payload


# -- price_trend / growth_metrics --------------------------------------------


def price_trend_impl(
    repository: Repository, area: str, dataset: Literal["new_build", "existing"], period_start: str, period_end: str
) -> dict:
    la_code, area_error = _resolve_area(repository, area)
    if area_error is not None:
        return area_error
    period_range, notes, period_error = _resolve_period_range(repository, period_start, period_end)
    if period_error is not None:
        return period_error
    assert la_code is not None and period_range is not None
    try:
        result = price_trend(repository, la_code, dataset, *period_range)
    except CoreError as exc:
        return _core_error(exc)
    payload = _ok(result)
    if notes:
        payload["assumption_notes"] = notes
    return payload


def growth_metrics_impl(
    repository: Repository, area: str, dataset: Literal["new_build", "existing"], period_start: str, period_end: str
) -> dict:
    la_code, area_error = _resolve_area(repository, area)
    if area_error is not None:
        return area_error
    period_range, notes, period_error = _resolve_period_range(repository, period_start, period_end)
    if period_error is not None:
        return period_error
    assert la_code is not None and period_range is not None
    try:
        result = growth_metrics(repository, la_code, dataset, *period_range)
    except CoreError as exc:
        return _core_error(exc)
    payload = _ok(result)
    if notes:
        payload["assumption_notes"] = notes
    return payload


# -- new_build_premium / premium_trend / premium_series ----------------------


def new_build_premium_impl(repository: Repository, area: str, period: str) -> dict:
    la_code, area_error = _resolve_area(repository, area)
    if area_error is not None:
        return area_error
    resolved_period, assumption_note, period_error = _resolve_single_period(repository, period)
    if period_error is not None:
        return period_error
    assert la_code is not None and resolved_period is not None
    try:
        result = new_build_premium(repository, la_code, resolved_period)
    except CoreError as exc:
        return _core_error(exc)
    payload = _ok(result)
    if assumption_note:
        payload["assumption_note"] = assumption_note
    return payload


def premium_trend_impl(repository: Repository, area: str, period_start: str, period_end: str) -> dict:
    la_code, area_error = _resolve_area(repository, area)
    if area_error is not None:
        return area_error
    period_range, notes, period_error = _resolve_period_range(repository, period_start, period_end)
    if period_error is not None:
        return period_error
    assert la_code is not None and period_range is not None
    try:
        result = premium_trend(repository, la_code, *period_range)
    except CoreError as exc:
        return _core_error(exc)
    payload = _ok(result)
    if notes:
        payload["assumption_notes"] = notes
    return payload


def premium_series_impl(repository: Repository, area: str, period_start: str, period_end: str) -> dict:
    la_code, area_error = _resolve_area(repository, area)
    if area_error is not None:
        return area_error
    period_range, notes, period_error = _resolve_period_range(repository, period_start, period_end)
    if period_error is not None:
        return period_error
    assert la_code is not None and period_range is not None
    try:
        result = premium_series(repository, la_code, *period_range)
    except CoreError as exc:
        return _core_error(exc)
    payload = _ok(result)
    if notes:
        payload["assumption_notes"] = notes
    return payload


# -- rank_areas / compare_areas -----------------------------------------------

_RankingMetricArg = Literal[
    "price", "growth_pct", "growth_gbp", "cagr_pct",
    "premium_pct", "premium_gbp", "premium_percentage_point_change", "premium_gbp_change",
]


def _resolve_period_or_range(
    repository: Repository,
    metric: str,
    period: str | None,
    period_start: str | None,
    period_end: str | None,
) -> tuple[Period | tuple[Period, Period], list[str], None] | tuple[None, None, dict]:
    """Dispatches on which shape `metric` actually needs (`core.tools`'
    `LEVEL_METRICS`/`RANGE_METRICS` -- the same classification `rank_areas`/
    `compare_areas` themselves use, not a second, independently-maintained
    list). **Tolerates a model that supplies both `period` and
    `period_start`/`period_end` in the same call** -- uses whichever the
    metric actually needs and ignores the other, rather than hard-rejecting
    the call over a redundant argument the metric doesn't need anyway.
    Confirmed necessary by `TASK-009`'s live-API testing: a model
    over-supplying every argument it has a plausible value for, rather than
    correctly omitting the ones that don't apply, turned out to be the
    common case, not an edge case."""
    needs_range = metric in RANGE_METRICS
    if needs_range:
        if period_start is not None and period_end is not None:
            return _resolve_period_range(repository, period_start, period_end)
        return None, None, {
            "status": _ANALYSIS_ERROR_STATUS,
            "message": f"metric {metric!r} is a change metric and requires period_start and period_end.",
        }
    if period is not None:
        resolved, note, error = _resolve_single_period(repository, period)
        if error is not None:
            return None, None, error
        assert resolved is not None
        return resolved, [note] if note else [], None
    if period_start is not None and period_end is not None:
        # A range given for a level metric -- rather than an opaque
        # rejection, honestly interpret it as "at the end of that range"
        # (a reasonable reading of "at this point in time").
        resolved, note, error = _resolve_single_period(repository, period_end)
        if error is not None:
            return None, None, error
        assert resolved is not None
        return resolved, [note] if note else [], None
    return None, None, {
        "status": _ANALYSIS_ERROR_STATUS,
        "message": f"metric {metric!r} is a level metric and requires `period`.",
    }


def rank_areas_impl(
    repository: Repository,
    metric: _RankingMetricArg,
    top_n: int,
    direction: Literal["top", "bottom"],
    dataset: Literal["new_build", "existing"] = "new_build",
    scope: list[str] | None = None,
    period: str | None = None,
    period_start: str | None = None,
    period_end: str | None = None,
) -> dict:
    period_or_range, notes, period_error = _resolve_period_or_range(repository, metric, period, period_start, period_end)
    if period_error is not None:
        return period_error
    resolved_scope, scope_warnings = _resolve_area_scope(repository, scope)
    assert period_or_range is not None
    try:
        result = rank_areas(repository, metric, period_or_range, resolved_scope, top_n, direction, dataset)
    except CoreError as exc:
        return _core_error(exc)
    payload = _ok(result)
    if notes:
        payload["assumption_notes"] = notes
    if scope_warnings:
        payload["scope_warnings"] = scope_warnings
    return payload


def compare_areas_impl(
    repository: Repository,
    areas: list[str],
    metric: _RankingMetricArg,
    dataset: Literal["new_build", "existing"] = "new_build",
    period: str | None = None,
    period_start: str | None = None,
    period_end: str | None = None,
) -> dict:
    period_or_range, notes, period_error = _resolve_period_or_range(repository, metric, period, period_start, period_end)
    if period_error is not None:
        return period_error
    resolved_areas, area_warnings = _resolve_area_scope(repository, areas)
    if resolved_areas == "all" or not resolved_areas:
        return {
            "status": "not_found",
            "message": "None of the requested areas could be resolved.",
            "scope_warnings": area_warnings,
        }
    assert period_or_range is not None
    try:
        result = compare_areas(repository, resolved_areas, metric, period_or_range, dataset)
    except CoreError as exc:
        return _core_error(exc)
    payload = _ok(result)
    if notes:
        payload["assumption_notes"] = notes
    if area_warnings:
        payload["scope_warnings"] = area_warnings
    return payload


# -- scan_for_patterns ---------------------------------------------------------


def scan_for_patterns_impl(
    repository: Repository,
    period_start: str,
    period_end: str,
    dataset: Literal["new_build", "existing"] = "new_build",
    scope: list[str] | None = None,
    max_per_category: int = 1,
    max_candidates: int = 8,
) -> dict:
    period_range, notes, period_error = _resolve_period_range(repository, period_start, period_end)
    if period_error is not None:
        return period_error
    resolved_scope, scope_warnings = _resolve_area_scope(repository, scope)
    assert period_range is not None
    try:
        result = scan_for_patterns(repository, resolved_scope, period_range, max_per_category, max_candidates, dataset)
    except CoreError as exc:
        return _core_error(exc)
    payload = _ok(result)
    if notes:
        payload["assumption_notes"] = notes
    if scope_warnings:
        payload["scope_warnings"] = scope_warnings
    return payload


# -- Agent / tool-registry assembly ------------------------------------------
#
# One `build_*_tool(repository)` per wrapper, each binding its `_impl`
# function to one `Repository` instance via closure -- the model only ever
# sees plain, LLM-friendly argument types (`str`/`int`/`Literal`/
# `list[str]`), never a `Repository` or any other internal object.


def build_median_price_lookup_tool(repository: Repository) -> FunctionTool:
    @function_tool(
        name_override="median_price_lookup",
        description_override=(
            "Look up the median price of a detached house (newly built or existing) for one "
            "England/Wales local authority and one period. `period` accepts a month+year "
            "('September 2025'), an ONS label ('Year ending Sep 2025'), or a bare year "
            "('2015', interpreted as year-ending-September). Always call this before stating "
            "any single-period price figure."
        ),
    )
    def median_price_lookup_tool(area: str, dataset: Literal["new_build", "existing"], period: str) -> dict:
        return median_price_lookup_impl(repository, area, dataset, period)

    return median_price_lookup_tool


def build_price_trend_tool(repository: Repository) -> FunctionTool:
    @function_tool(
        name_override="price_trend",
        description_override=(
            "The full per-period price series for one area/dataset between two periods -- for "
            "charting/showing the whole trend. Does NOT compute a growth or CAGR figure -- call "
            "growth_metrics as well (same area/dataset/period range) whenever the question needs an "
            "actual growth percentage, £ change, or CAGR stated, not just the series itself."
        ),
    )
    def price_trend_tool(
        area: str, dataset: Literal["new_build", "existing"], period_start: str, period_end: str
    ) -> dict:
        return price_trend_impl(repository, area, dataset, period_start, period_end)

    return price_trend_tool


def build_growth_metrics_tool(repository: Repository) -> FunctionTool:
    @function_tool(
        name_override="growth_metrics",
        description_override=(
            "Latest price, absolute (£) and percentage growth, and CAGR for one area/dataset "
            "between two periods. Use this for any growth/CAGR question about a single area."
        ),
    )
    def growth_metrics_tool(
        area: str, dataset: Literal["new_build", "existing"], period_start: str, period_end: str
    ) -> dict:
        return growth_metrics_impl(repository, area, dataset, period_start, period_end)

    return growth_metrics_tool


def build_new_build_premium_tool(repository: Repository) -> FunctionTool:
    @function_tool(
        name_override="new_build_premium",
        description_override=(
            "The new-build premium (percentage and £, new-build vs existing) for one area at one "
            "period. Use for 'what is the premium right now' questions."
        ),
    )
    def new_build_premium_tool(area: str, period: str) -> dict:
        return new_build_premium_impl(repository, area, period)

    return new_build_premium_tool


def build_premium_trend_tool(repository: Repository) -> FunctionTool:
    @function_tool(
        name_override="premium_trend",
        description_override=(
            "How one area's new-build premium changed between two periods (both endpoints plus the "
            "change). Use for 'how has the premium changed' questions about a single area."
        ),
    )
    def premium_trend_tool(area: str, period_start: str, period_end: str) -> dict:
        return premium_trend_impl(repository, area, period_start, period_end)

    return premium_trend_tool


def build_premium_series_tool(repository: Repository) -> FunctionTool:
    @function_tool(
        name_override="premium_series",
        description_override=(
            "The full per-period new-build premium series for one area between two periods "
            "(every period, not just the endpoints)."
        ),
    )
    def premium_series_tool(area: str, period_start: str, period_end: str) -> dict:
        return premium_series_impl(repository, area, period_start, period_end)

    return premium_series_tool


def build_rank_areas_tool(repository: Repository) -> FunctionTool:
    @function_tool(
        name_override="rank_areas",
        description_override=(
            "Rank areas by a metric and return the top/bottom N -- a single call does the full "
            "fetch/compute/rank internally, never row-by-row. `metric` is one of: price, "
            "growth_pct, growth_gbp, cagr_pct, premium_pct, premium_gbp, "
            "premium_percentage_point_change, premium_gbp_change. Level metrics (price, "
            "premium_pct, premium_gbp) take `period`; the rest are change metrics and take "
            "`period_start`+`period_end`. Omit `scope` to rank across all covered areas."
        ),
    )
    def rank_areas_tool(
        metric: _RankingMetricArg,
        top_n: int,
        direction: Literal["top", "bottom"],
        dataset: Literal["new_build", "existing"] = "new_build",
        scope: list[str] | None = None,
        period: str | None = None,
        period_start: str | None = None,
        period_end: str | None = None,
    ) -> dict:
        return rank_areas_impl(repository, metric, top_n, direction, dataset, scope, period, period_start, period_end)

    return rank_areas_tool


def build_compare_areas_tool(repository: Repository) -> FunctionTool:
    @function_tool(
        name_override="compare_areas",
        description_override=(
            "Directly compare a specific, named set of areas by one metric -- every requested area "
            "gets a row (suppressed ones flagged, never dropped). Use for 'compare X, Y, and Z' "
            "questions, as opposed to rank_areas' 'top N across a scope'. Same metric/period "
            "argument shape as rank_areas."
        ),
    )
    def compare_areas_tool(
        areas: list[str],
        metric: _RankingMetricArg,
        dataset: Literal["new_build", "existing"] = "new_build",
        period: str | None = None,
        period_start: str | None = None,
        period_end: str | None = None,
    ) -> dict:
        return compare_areas_impl(repository, areas, metric, dataset, period, period_start, period_end)

    return compare_areas_tool


def build_scan_for_patterns_tool(repository: Repository) -> FunctionTool:
    @function_tool(
        name_override="scan_for_patterns",
        description_override=(
            "A bounded set of deterministic, evidence-linked observations (growth leader/laggard, "
            "regional distribution, premium expansion/contraction, regional divergence, "
            "period-on-period movement, coverage gaps) across a scope and period range -- for "
            "open-ended 'find notable patterns' questions. Never returns a cause, only a figure "
            "and its evidence. Omit `scope` to scan all covered areas."
        ),
    )
    def scan_for_patterns_tool(
        period_start: str,
        period_end: str,
        dataset: Literal["new_build", "existing"] = "new_build",
        scope: list[str] | None = None,
        max_per_category: int = 1,
        max_candidates: int = 8,
    ) -> dict:
        return scan_for_patterns_impl(repository, period_start, period_end, dataset, scope, max_per_category, max_candidates)

    return scan_for_patterns_tool


def build_resolve_geography_tool(repository: Repository) -> FunctionTool:
    @function_tool(
        name_override="resolve_geography",
        description_override=(
            "Resolve a free-text place name to a local authority before running an analysis. "
            "Returns status=matched/ambiguous/out_of_coverage/not_found. Call this first for any "
            "area name you are not already certain resolves cleanly (e.g. a possibly-ambiguous or "
            "possibly-out-of-coverage name)."
        ),
    )
    def resolve_geography_tool(query_text: str) -> dict:
        return resolve_geography_impl(repository, query_text)

    return resolve_geography_tool


def build_resolve_period_tool(repository: Repository) -> FunctionTool:
    @function_tool(
        name_override="resolve_period",
        description_override=(
            "Resolve a free-text time expression (a month+year, a bare year, 'since X', "
            "'last five years', 'last decade') to the dataset's actual period(s). Returns "
            "status=resolved/resolved_with_assumption/range_resolved/out_of_range/not_found. Call "
            "this first for any relative or bare-year period expression."
        ),
    )
    def resolve_period_tool(query_text: str) -> dict:
        return resolve_period_impl(repository, query_text)

    return resolve_period_tool


def build_agent(repository: Repository, model: str) -> Agent:
    """`TASK-009`'s full-registry `Agent` -- every `EPIC-02` analysis
    function plus both free-text resolvers, wrapped as tools; `output_type`
    stays `DraftAnswer` (`ADR-009` v14, unchanged since `STORY-003`)."""
    return Agent(
        name="Housing Market Insights Agent",
        instructions=SYSTEM_INSTRUCTIONS,
        model=model,
        tools=[
            build_median_price_lookup_tool(repository),
            build_price_trend_tool(repository),
            build_growth_metrics_tool(repository),
            build_new_build_premium_tool(repository),
            build_premium_trend_tool(repository),
            build_premium_series_tool(repository),
            build_rank_areas_tool(repository),
            build_compare_areas_tool(repository),
            build_scan_for_patterns_tool(repository),
            build_resolve_geography_tool(repository),
            build_resolve_period_tool(repository),
        ],
        output_type=DraftAnswer,
    )
