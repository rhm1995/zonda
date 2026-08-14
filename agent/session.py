"""Conversation Session (`CMP-007`, `STORY-005`, design §5/§7.2, `ADR-008` v15).

`record_turn` -- the only place `ConversationSession` is ever updated --
writes both halves of session state from the same turn atomically: the
bounded verbatim `recent_messages` window, and compact structured state
(`last_area_codes`/`last_metric`/...) reflecting only the most recent turn,
never an accumulating history. The two can never drift out of sync,
because both are derived here, in one call, from the same `AgentTurnResult`
(design's own stated risk for this component).

`record_turn` treats `ConversationSession` as an immutable value object --
it never mutates its input, only returns a new instance. `answer_question`'s
contract is fixed at `-> AgentTurnResult` (design §8.2), with no second
return value for an updated session, so `agent/orchestrator.py` applies
the new instance's fields onto the caller's held `ConversationSession`
object in place -- see its own docstring for why.

**(TASK-015)** `session_id` is carried forward unchanged across every
`record_turn` call; `turn_number` is incremented by exactly one -- both
exist purely for structured-log correlation (design §12), not for any
follow-up/grounding logic.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from core.models import AgentTurnResult, ConversationSession, Period, RecentMessage
from core.period import resolve_period
from core.repository import Repository

#: (v15, ADR-008) 2-4 exchanges = up to 8 `RecentMessage` entries (one user
#: + one assistant message per exchange) -- bounded so per-turn token cost
#: stays roughly flat regardless of session length (RSK-001/NFR-006).
MAX_RECENT_MESSAGES = 8


def _extract_la_codes(structured_data: list[BaseModel]) -> list[str]:
    """Every distinct `la_code` this turn's results touched -- a scalar
    result's own `la_code`, or a list-valued result's rows'."""
    codes: list[str] = []
    for result in structured_data:
        la_code = getattr(result, "la_code", None)
        if isinstance(la_code, str) and la_code not in codes:
            codes.append(la_code)
        for list_attr in ("rows", "areas", "points", "candidates"):
            rows = getattr(result, list_attr, None)
            if not isinstance(rows, list):
                continue
            for row in rows:
                row_code = getattr(row, "la_code", None)
                if isinstance(row_code, str) and row_code not in codes:
                    codes.append(row_code)
    return codes


def _extract_metric(structured_data: list[BaseModel]) -> str | None:
    for result in structured_data:
        metric = getattr(result, "metric", None)
        if isinstance(metric, str):
            return metric
    return None


def _extract_dwelling_status(structured_data: list[BaseModel]) -> Literal["new_build", "existing", "both"] | None:
    datasets = {value for r in structured_data if isinstance(value := getattr(r, "dataset", None), str)}
    if not datasets:
        return None
    if len(datasets) > 1:
        return "both"
    (only,) = datasets
    return "new_build" if only == "new_build" else "existing"


def _extract_period_labels(structured_data: list[BaseModel]) -> tuple[str | None, str | None]:
    """The first result carrying period information wins -- `structured_data`
    order is the tool-call order for this turn, so this is the most recent
    (and, for a single-focus turn, the only) one. Handles all three shapes
    already in the schema: two explicit endpoint labels, one label, or a
    combined "X to Y" range string."""
    for result in structured_data:
        start = getattr(result, "period_start_label", None)
        end = getattr(result, "period_end_label", None)
        if start and end:
            return start, end
        label = getattr(result, "period_label", None)
        if label:
            return label, label
        label_or_range = getattr(result, "period_label_or_range", None)
        if label_or_range:
            if " to " in label_or_range:
                start_part, end_part = label_or_range.split(" to ", 1)
                return start_part, end_part
            return label_or_range, label_or_range
    return None, None


def _resolve_period_label(repository: Repository, label: str | None) -> Period | None:
    if label is None:
        return None
    return resolve_period(repository, label).period


def _extract_result_reference(structured_data: list[BaseModel]) -> str | None:
    """A short label for what kind of result this turn produced -- e.g.
    "ranking:premium_percentage_point_change:top5" -- feeding the
    orchestrator's recap string. `None` if nothing was actually resolved
    this turn (a clarification/decline, or an omitted structured_data)."""
    if not structured_data:
        return None
    primary = structured_data[-1]
    metric = getattr(primary, "metric", None)
    direction = getattr(primary, "direction", None)
    rows = getattr(primary, "rows", None)
    if isinstance(metric, str) and isinstance(direction, str) and isinstance(rows, list):
        return f"ranking:{metric}:{direction}{len(rows)}"
    return type(primary).__name__


def record_turn(
    session: ConversationSession, question: str, result: AgentTurnResult, repository: Repository
) -> ConversationSession:
    """Never mutates `session` -- returns a new instance. `last_*` fields
    reflect only *this* turn's `result.structured_data` (empty/`None` if
    this turn resolved nothing, e.g. a clarification) -- "reflecting only
    the most recent turn, never an accumulating history" is read literally:
    a turn that established nothing new clears these, rather than silently
    keeping stale state from two turns ago. `recent_messages` always
    grows by one exchange regardless, since even a clarifying question is
    real conversational context a later turn's phrasing may depend on."""
    new_messages = [
        *session.recent_messages,
        RecentMessage(role="user", text=question),
        RecentMessage(role="assistant", text=result.answer_text),
    ]
    start_label, end_label = _extract_period_labels(result.structured_data)

    return ConversationSession(
        session_id=session.session_id,
        turn_number=session.turn_number + 1,
        recent_messages=new_messages[-MAX_RECENT_MESSAGES:],
        last_area_codes=_extract_la_codes(result.structured_data),
        # Not derivable from AgentTurnResult -- no mechanism in this
        # increment distinguishes "scoped to one region" from "all areas"
        # structurally. Documented limitation, not silently guessed.
        last_region_scope=None,
        last_start_period=_resolve_period_label(repository, start_label),
        last_end_period=_resolve_period_label(repository, end_label),
        last_metric=_extract_metric(result.structured_data),
        last_dwelling_status=_extract_dwelling_status(result.structured_data),
        last_result_reference=_extract_result_reference(result.structured_data),
    )
