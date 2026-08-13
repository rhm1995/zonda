"""Agent definition (STORY-003, design CMP-005/CMP-006 -- single-tool
scope).

Wraps exactly one deterministic function -- `median_price_lookup`
(`TASK-003`) -- as an Agents SDK tool, so the conversational path is proven
end to end before `TASK-009` (Increment 4) extends the tool registry to
the full analysis library. The tool never computes a figure itself; it
only resolves the model's free-text area/period into the typed arguments
`core.tools.median_price_lookup` already expects, then calls it.

**Scope note (basic period handling):** full free-text period resolution
("since 2015", a bare year, "last five years") is `TASK-019`'s job
(Increment 4, `CMP-018`/`core/period.py`). This story only needs a month
and a calendar year -- the tool below accepts those two fields directly
(an LLM reliably extracts them from a sentence like "...in September
2025") and maps them onto the dataset's fixed quarterly convention itself,
rather than asking the model to reconstruct an exact ONS label string
(`"Year ending Sep 2025"`) unassisted. A month outside the dataset's four
quarter-end months returns a typed "not found" result with the allowed
months named, never a guess.
"""

from __future__ import annotations

from typing import Literal

from agents import Agent, FunctionTool, function_tool

from core.errors import PeriodOutOfRangeError
from core.geography import resolve_geography
from core.models import DraftAnswer, Period
from core.repository import Repository
from core.tools import median_price_lookup

SYSTEM_INSTRUCTIONS = """\
You are a housing-market analysis assistant for UK detached-house prices \
(newly built and existing dwellings), England and Wales local authorities \
only, ONS "year ending September 2025" edition (HM Land Registry \
price-paid data).

Rules:
- Use the median_price_lookup tool for every factual price question. \
Never state a price figure you did not just obtain from a tool call this \
turn.
- If the tool reports the area or period could not be resolved, say so \
plainly in your answer -- never guess or substitute a different area.
- Every numeric price you state in answer_text must have a corresponding \
entry in claims, citing which tool result and field it came from.
- Do not speculate about *why* a price is what it is, or state a reason \
for a missing/suppressed value that the tool result did not itself state.
"""

#: The dataset's fixed quarterly rolling-year-ending convention (design
#: §6.4) -- the only months a period can end on. Maps common spellings
#: (full name, abbreviation, or numeral -- confirmed necessary by SPIKE-001:
#: a live model call chose "09" over "September" for the same question)
#: to the ONS label's 3-letter form.
_MONTH_ALIASES: dict[str, str] = {
    "mar": "Mar", "march": "Mar", "3": "Mar", "03": "Mar",
    "jun": "Jun", "june": "Jun", "6": "Jun", "06": "Jun",
    "sep": "Sep", "sept": "Sep", "september": "Sep", "9": "Sep", "09": "Sep",
    "dec": "Dec", "december": "Dec", "12": "Dec",
}


def _resolve_basic_period(repository: Repository, month: str, year: int) -> Period | None:
    """Exact quarter-end match only -- no "nearest available" suggestion
    logic (that's `TASK-019`'s job); returns `None` on anything else."""
    month_abbr = _MONTH_ALIASES.get(month.strip().casefold())
    if month_abbr is None:
        return None
    label = f"Year ending {month_abbr} {year}"
    for period in repository.get_period_reference():
        if period.label == label:
            return period
    return None


def median_price_lookup_impl(
    repository: Repository,
    area: str,
    dataset: Literal["new_build", "existing"],
    month: str,
    year: int,
) -> dict:
    """The tool's actual logic, kept as a plain function so it's directly
    unit-testable without going through the Agents SDK's `function_tool`
    call machinery. Always returns a JSON-serialisable dict: `{"status":
    "ok", **PriceLookupResult fields}` on success, or `{"status": <error>,
    "message": ...}` on a resolution failure -- never raises, so a tool
    call never crashes the run (design's "structured, non-throwing tool
    result" discipline, extended fully to all eight tools by `TASK-009`)."""
    geography_match = resolve_geography(repository, area)
    if geography_match.status != "matched":
        return {
            "status": geography_match.status,
            "message": geography_match.coverage_note
            or f"Could not resolve {area!r} to a covered local authority.",
        }

    period = _resolve_basic_period(repository, month, year)
    if period is None:
        return {
            "status": "period_not_found",
            "message": (
                f"{month} {year} is not a supported period in this dataset. "
                "Supported months (quarter-ends): March, June, September, December."
            ),
        }

    la_code = geography_match.matches[0].la_code
    try:
        result = median_price_lookup(repository, la_code, dataset, period)
    except PeriodOutOfRangeError as exc:
        return {"status": "period_not_found", "message": str(exc)}

    return {"status": "ok", **result.model_dump()}


def build_median_price_lookup_tool(repository: Repository) -> FunctionTool:
    """Wraps `median_price_lookup_impl` as an Agents SDK `function_tool`,
    binding it to one `Repository` instance via closure (the tool's public
    signature, seen by the model, only ever carries plain, LLM-friendly
    argument types)."""

    @function_tool(
        name_override="median_price_lookup",
        description_override=(
            "Look up the median price of a detached house (newly built or existing) for one "
            "England/Wales local authority and one quarter-end period (month + year). Always "
            "call this before stating any price figure. `month` accepts a name ('September'), "
            "abbreviation ('Sep'), or number (9 or '09') -- only March, June, September, and "
            "December are valid quarter-end months."
        ),
    )
    def median_price_lookup_tool(
        area: str,
        dataset: Literal["new_build", "existing"],
        month: str,
        year: int,
    ) -> dict:
        return median_price_lookup_impl(repository, area, dataset, month, year)

    return median_price_lookup_tool


def build_agent(repository: Repository, model: str) -> Agent:
    """`STORY-003`'s single-tool `Agent` -- `output_type=DraftAnswer` from
    this story onward (`ADR-009` v14), even though `TASK-010`'s structural
    validation of `claims` doesn't land until Increment 4, per that
    ticket's own implementation note (avoids a rework gap)."""
    return Agent(
        name="Housing Market Insights Agent",
        instructions=SYSTEM_INSTRUCTIONS,
        model=model,
        tools=[build_median_price_lookup_tool(repository)],
        output_type=DraftAnswer,
    )
