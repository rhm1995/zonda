"""Pure, deterministic formulas shared by every caller of the analysis core
(TASK-003/TASK-004, design ADR-001's "one tested implementation" principle).

No I/O, no repository access -- these functions operate only on numbers
already fetched by `core/tools.py`. This is what makes them trivially
unit-testable and what guarantees the UI, the CSV export, and (from
Increment 3) the agent's tool calls can never compute the same metric two
different ways.
"""

from __future__ import annotations

from datetime import date
from typing import Literal

DAYS_PER_YEAR = 365.25  # exact elapsed-time convention for CAGR (ASM-010)

#: (v13, ADR-018) The single canonical wording for a suppressed value,
#: reused verbatim by every UI/agent surface that narrates one. Neither
#: bundled ONS workbook states a reason alongside its suppression marker
#: ("[x]") -- no surface may state or imply one.
SUPPRESSION_MESSAGE = "ONS does not report a value for this area and period."


def growth_gbp(start_price: int, end_price: int) -> float:
    """ASM-010: absolute growth (£) = price(end) - price(start)."""
    return end_price - start_price


def growth_pct(start_price: int, end_price: int) -> float:
    """ASM-010: percentage growth = (price(end) - price(start)) / price(start) * 100."""
    return (end_price - start_price) / start_price * 100


def years_between(start_date: date, end_date: date) -> float:
    """Exact elapsed time between two period end dates, in years -- not a
    rounded integer year count (ASM-010)."""
    return (end_date - start_date).days / DAYS_PER_YEAR


def cagr_pct(start_price: int, end_price: int, years: float) -> float:
    """ASM-010: CAGR = (price(end) / price(start)) ^ (1 / years) - 1,
    expressed as a percentage."""
    return ((end_price / start_price) ** (1 / years) - 1) * 100


def premium_pct(new_build_price: int, existing_price: int) -> float:
    """ASM-003: premium % = (new_build - existing) / existing * 100."""
    return (new_build_price - existing_price) / existing_price * 100


def premium_gbp(new_build_price: int, existing_price: int) -> int:
    """ASM-003: premium £ = new_build - existing."""
    return new_build_price - existing_price


def premium_label(value: float | None) -> Literal["premium", "discount"] | None:
    """(v8/v9) A negative premium is labelled "discount" -- a presentation
    rule derived from the existing signed value, never a separately stored
    or independently computed figure (FR-044)."""
    if value is None:
        return None
    return "discount" if value < 0 else "premium"
