"""Period Resolver (CMP-018, `TASK-019`, design §5/§7.4a, `ADR-016`).

Mirrors `core/geography.py`'s role for the time dimension: maps a natural-
language period expression to a typed `Period`/`PeriodMatch`, so the agent
never has to reconstruct an exact, undocumented ONS label string (e.g.
`"Year ending Sep 2025"`) from free text itself, and never silently guesses
or clamps a period that doesn't exist in the data.

Used exclusively by the "Ask the data" tab (`ADR-012`, the same scoping
`TASK-011` uses for geography): "Explore trends" and "Compare and rank"
populate their period selectors directly from `get_period_reference()`, a
closed list, so free-text period resolution never arises there.

Every relative expression ("since X", "last five years", "last decade") is
anchored to the dataset's actual latest available period (read fresh via
`repository.get_period_reference()`), never real-world "today" -- a wrong
anchor would silently misdate every relative query.
"""

from __future__ import annotations

import re
from datetime import date

from core.models import Period, PeriodMatch
from core.repository import Repository

#: The dataset's fixed quarterly rolling-year-ending convention (design
#: §6.4) -- maps common spellings (full name, abbreviation, or numeral) to
#: a calendar month number. Only Mar/Jun/Sep/Dec are ever valid *resolved*
#: months, but every calendar month is accepted here so an off-convention
#: month (e.g. "January 2020") fails informatively (`out_of_range`, with
#: nearest suggestions) rather than being rejected as unparseable.
_MONTH_TO_NUMBER: dict[str, int] = {
    "jan": 1, "january": 1, "1": 1, "01": 1,
    "feb": 2, "february": 2, "2": 2, "02": 2,
    "mar": 3, "march": 3, "3": 3, "03": 3,
    "apr": 4, "april": 4, "4": 4, "04": 4,
    "may": 5, "5": 5, "05": 5,
    "jun": 6, "june": 6, "6": 6, "06": 6,
    "jul": 7, "july": 7, "7": 7, "07": 7,
    "aug": 8, "august": 8, "8": 8, "08": 8,
    "sep": 9, "sept": 9, "september": 9, "9": 9, "09": 9,
    "oct": 10, "october": 10, "10": 10,
    "nov": 11, "november": 11, "11": 11,
    "dec": 12, "december": 12, "12": 12,
}
#: This dataset's own edition-naming convention (design §6.4): a bare year
#: is assumed to mean "year ending September" of that year, since that is
#: the convention the dataset's own release naming already uses.
_DEFAULT_BARE_YEAR_MONTH = 9

_NUMBER_WORDS: dict[str, int] = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
}

_MONTH_YEAR_RE = re.compile(r"^([A-Za-z]+|\d{1,2})\s+(\d{4})$")
#: Recognises the dataset's own exact label format ("Year ending Sep 2025")
#: -- needed so a tool downstream of `resolve_period` can accept the exact
#: label the model already saw in a prior resolution's result, not just a
#: fresh "Month Year" expression (`TASK-009`; the agent's own tool-calling
#: loop has no other way to pass a resolved `Period` between two separate
#: tool calls except by having the model echo text back).
_ONS_LABEL_RE = re.compile(r"^Year ending ([A-Za-z]+)\s+(\d{4})$", re.IGNORECASE)
_BARE_YEAR_RE = re.compile(r"^(\d{4})$")
_SINCE_RE = re.compile(r"^since\s+(.+)$", re.IGNORECASE)
_LAST_N_YEARS_RE = re.compile(r"^last\s+(\w+)\s+years?$", re.IGNORECASE)
_LAST_DECADE_RE = re.compile(r"^last\s+decade$", re.IGNORECASE)

_SUGGESTION_COUNT = 3


def _nearest_periods(periods: list[Period], target: date, count: int = _SUGGESTION_COUNT) -> list[Period]:
    return sorted(periods, key=lambda p: abs((p.end_date - target).days))[:count]


def _period_by_label(periods: list[Period], label: str) -> Period | None:
    for period in periods:
        if period.label == label:
            return period
    return None


def _resolve_month_year(
    query_text: str, periods: list[Period], month_text: str, year_text: str
) -> PeriodMatch:
    month_key = month_text.strip().casefold()
    month_number = _MONTH_TO_NUMBER.get(month_key)
    if month_number is None:
        return PeriodMatch(query_text=query_text, status="not_found")

    year = int(year_text)
    label = f"Year ending {date(1900, month_number, 1).strftime('%b')} {year}"
    resolved = _period_by_label(periods, label)
    if resolved is not None:
        return PeriodMatch(query_text=query_text, status="resolved", period=resolved)

    # Parses fine but doesn't exist in the dataset (off-convention month, or
    # a real quarter-end outside the dataset's actual date range) -- an
    # honest out_of_range with nearest suggestions, never a fabricated period.
    approx_target = date(year, month_number, 1)
    return PeriodMatch(
        query_text=query_text,
        status="out_of_range",
        suggestions=_nearest_periods(periods, approx_target),
    )


def _resolve_bare_year(query_text: str, periods: list[Period], year_text: str) -> PeriodMatch:
    year = int(year_text)
    label = f"Year ending {date(1900, _DEFAULT_BARE_YEAR_MONTH, 1).strftime('%b')} {year}"
    assumption_note = (
        f"{year!r} was interpreted as the year ending September {year} "
        "(this dataset's own period convention), since no month was given."
    )
    resolved = _period_by_label(periods, label)
    if resolved is not None:
        return PeriodMatch(
            query_text=query_text,
            status="resolved_with_assumption",
            period=resolved,
            assumption_note=assumption_note,
        )
    approx_target = date(year, _DEFAULT_BARE_YEAR_MONTH, 1)
    return PeriodMatch(
        query_text=query_text,
        status="out_of_range",
        suggestions=_nearest_periods(periods, approx_target),
    )


def _resolve_single_expression(query_text: str, periods: list[Period]) -> PeriodMatch:
    """Resolves a single-period expression (an ONS label, month+year, or
    bare year) -- the building block `_resolve_since` reuses for its start
    expression."""
    stripped = query_text.strip()
    ons_label = _ONS_LABEL_RE.match(stripped)
    if ons_label:
        return _resolve_month_year(query_text, periods, ons_label.group(1), ons_label.group(2))
    month_year = _MONTH_YEAR_RE.match(stripped)
    if month_year:
        return _resolve_month_year(query_text, periods, month_year.group(1), month_year.group(2))
    bare_year = _BARE_YEAR_RE.match(stripped)
    if bare_year:
        return _resolve_bare_year(query_text, periods, bare_year.group(1))
    return PeriodMatch(query_text=query_text, status="not_found")


def _resolve_since(query_text: str, periods: list[Period], start_text: str, latest: Period) -> PeriodMatch:
    start_match = _resolve_single_expression(start_text, periods)
    if start_match.period is None:
        # The start expression itself didn't resolve (unparseable or out of
        # range) -- no valid range can be built from it.
        return PeriodMatch(
            query_text=query_text, status=start_match.status, suggestions=start_match.suggestions
        )
    return PeriodMatch(
        query_text=query_text,
        status="range_resolved",
        period_range=(start_match.period, latest),
        assumption_note=start_match.assumption_note,
    )


def _resolve_last_n_years(query_text: str, periods: list[Period], years: int, latest: Period) -> PeriodMatch:
    target_year = latest.end_date.year - years
    try:
        target_date = latest.end_date.replace(year=target_year)
    except ValueError:  # pragma: no cover -- Feb 29 anchor; not reachable for this dataset's Mar/Jun/Sep/Dec ends
        target_date = latest.end_date.replace(year=target_year, day=28)
    start = _period_by_label(periods, f"Year ending {latest.end_date.strftime('%b')} {target_year}")
    if start is None:
        # No exact quarter-end N years back (e.g. requested span reaches
        # before the dataset began) -- nearest available, never fabricated.
        candidates = _nearest_periods(periods, target_date, count=1)
        if not candidates:
            return PeriodMatch(query_text=query_text, status="out_of_range")
        start = candidates[0]
    return PeriodMatch(query_text=query_text, status="range_resolved", period_range=(start, latest))


def resolve_period(repository: Repository, text: str) -> PeriodMatch:
    """Never raises -- an unparseable or out-of-range expression returns a
    typed `not_found`/`out_of_range` status instead (`ADR-016`)."""
    query_text = text
    normalized = text.strip()
    if not normalized:
        return PeriodMatch(query_text=query_text, status="not_found")

    periods = repository.get_period_reference()
    if not periods:  # pragma: no cover -- an empty dataset is a startup-time failure elsewhere
        return PeriodMatch(query_text=query_text, status="not_found")
    latest = periods[-1]

    since_match = _SINCE_RE.match(normalized)
    if since_match:
        return _resolve_since(query_text, periods, since_match.group(1), latest)

    if _LAST_DECADE_RE.match(normalized):
        return _resolve_last_n_years(query_text, periods, 10, latest)

    last_n_match = _LAST_N_YEARS_RE.match(normalized)
    if last_n_match:
        n_text = last_n_match.group(1).casefold()
        years = _NUMBER_WORDS.get(n_text) or (int(n_text) if n_text.isdigit() else None)
        if years is None:
            return PeriodMatch(query_text=query_text, status="not_found")
        return _resolve_last_n_years(query_text, periods, years, latest)

    return _resolve_single_expression(query_text, periods)
