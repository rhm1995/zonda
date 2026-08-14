"""Agent Orchestrator (STORY-003, design CMP-011, §8.2).

`answer_question(session, question) -> AgentTurnResult` is the single,
UI-agnostic entry point -- used identically by `ui/ask_the_data.py` and,
from Increment 5, the evaluation harness. It never raises for an expected
failure mode (missing key, unreachable API); those become
`AgentTurnResult.status` values instead, so callers render rather than
catch (design §8.2's own stated contract).

**Follow-ups (`STORY-005`, Increment 4):** a first turn (empty
`session.recent_messages`) is sent as a plain string, unchanged from
`STORY-003` -- every existing stubbed test still passes a bare `str`. A
follow-up turn is sent as the verbatim recent-message window plus a
compact structured recap glued onto the final message (`_build_agent_input`),
and every successful turn's result is folded back into `session` via
`agent.session.record_turn` before returning -- see `_apply_session_update`'s
docstring for why that mutates the caller's held object in place rather
than being a second return value.

**Testability:** `run_agent` is an injected seam (defaults to the real
`Runner.run_sync`) so this module's own logic -- retry, status mapping,
`structured_data`/`tool_calls` extraction -- is fully unit-tested with a
stub, per the design's own "stubbed Agents SDK model" testing philosophy
(§13), without a live API key or any network call.

**Grounding (`TASK-010`, Increment 4):** every successful run's `DraftAnswer`
is checked by `agent.guardrails.check_grounding` before release. A failure
gets exactly one repair re-run (a corrected prompt built from the specific
check failure, `build_repair_instruction`); if the repair still doesn't
pass, the turn falls back to `build_templated_fallback`'s tool-output-only
answer rather than releasing an unverified draft (`AC2`). This never
downgrades the turn to `status="unavailable"` -- a grounding failure is not
an API failure, and a safe, honest fallback answer is still an answer.

**Observability (`TASK-015`, Increment 6, design §12):** every tool call,
every `Runner.run_sync` invocation (the original attempt and, separately, a
repair re-run), and every grounding-guardrail trigger emits one structured
`agent.observability.log_event` call, correlated by `session.session_id`/
this turn's `turn_number` -- never the API key, never more than a short,
bounded excerpt of free text. Logging is attached once, explicitly, by each
real entry point (`ui/ask_the_data.py`, `eval/run_eval.py`) -- this module
only calls `log_event`, never `configure_logging` itself.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Callable, Protocol

from agents import Agent, Runner, ToolCallItem, ToolCallOutputItem
from pydantic import BaseModel

from agent.agent_definition import build_agent
from agent.config import Config, load_config
from agent.guardrails import (
    GroundingCheckResult,
    autofill_missing_claims,
    build_repair_instruction,
    build_templated_fallback,
    check_grounding,
)
from agent.observability import log_event
from agent.session import record_turn
from core.models import (
    AgentTurnResult,
    ComparisonResult,
    ConversationSession,
    DraftAnswer,
    GrowthMetricsResult,
    PatternScanResult,
    PremiumResult,
    PremiumSeriesResult,
    PremiumTrendResult,
    PriceLookupResult,
    RankingResult,
    ToolCallLog,
    TrendResult,
)
from core.repository import Repository

logger = logging.getLogger(__name__)

#: Maps a tool's name to the typed schema its successful ("status": "ok")
#: dict result should be reconstructed into for `AgentTurnResult.structured_data`.
#: SPIKE-001 found that the SDK's `ToolCallOutputItem.output` is the tool
#: function's raw JSON-serialisable return value (a `dict`, per
#: `median_price_lookup_impl`'s own documented contract) -- never a
#: `BaseModel` instance -- so it must be reconstructed here, not merely
#: type-checked. `TASK-009` (Increment 4) extends this table alongside the
#: tool registry. `resolve_geography`/`resolve_period` are deliberately
#: absent -- they're free-text resolution steps, not evidence sources a
#: claim should cite, and their own `status` field is never `"ok"` (it's
#: matched/ambiguous/out_of_coverage/... or resolved/range_resolved/...),
#: so `_reconstruct_structured_result`'s status gate already excludes them.
_TOOL_RESULT_SCHEMAS: dict[str, type[BaseModel]] = {
    "median_price_lookup": PriceLookupResult,
    "price_trend": TrendResult,
    "growth_metrics": GrowthMetricsResult,
    "new_build_premium": PremiumResult,
    "premium_trend": PremiumTrendResult,
    "premium_series": PremiumSeriesResult,
    "rank_areas": RankingResult,
    "compare_areas": ComparisonResult,
    "scan_for_patterns": PatternScanResult,
}

#: THR-006/NFR-006: bounds runaway tool-call loops -- still a hard cap, not
#: "unlimited," but raised from the design's illustrative "e.g. 6" after
#: TASK-009's live-API testing: a genuinely legitimate multi-step ranking
#: question (resolve_period, one or two resolve_geography/analysis calls,
#: occasionally one self-corrected wrong turn) reliably needed 8-12 turns
#: to complete correctly, and 6 cut off well-formed, still-progressing runs.
MAX_TURNS = 15
RETRY_ATTEMPTS = 2  # 1 bounded retry (design §8.1) = 2 total attempts
UNAVAILABLE_MESSAGE = (
    "The assistant is currently unavailable. Please try again shortly, or use the "
    "Explore trends / Compare and rank tabs, which don't require it."
)

PROCESSED_DIR = Path(__file__).resolve().parents[1] / "data" / "processed"


#: Either a plain question (first turn) or an Agents-SDK input-item list
#: (a follow-up turn, `STORY-005`) -- see `_build_agent_input`.
AgentInput = str | list[dict]


class RunAgent(Protocol):
    """The seam `answer_question` calls through -- real implementation is
    `Runner.run_sync`; tests inject a stub returning a hand-built result."""

    def __call__(self, agent: Agent, agent_input: AgentInput, *, max_turns: int) -> object: ...


def _default_run_agent(agent: Agent, agent_input: AgentInput, *, max_turns: int) -> object:
    # `agent_input`'s list-of-dicts shape (role/content) matches the SDK's
    # `EasyInputMessageParam` TypedDict exactly at runtime; mypy can't
    # verify a broad `list[dict]` against the SDK's large structural union
    # without this module also importing and reconstructing those exact
    # TypedDicts for no behavioural benefit.
    return Runner.run_sync(agent, agent_input, max_turns=max_turns)  # type: ignore[arg-type]


def _build_recap(session: ConversationSession) -> str | None:
    """A compact, structured restatement of what the previous turn
    established -- glued onto the latest message so the model reliably
    sees the resolved facts a follow-up like "those areas" depends on,
    even if it doesn't fully attend to the earlier turns in
    `recent_messages`. `None` if the previous turn established nothing
    (e.g. it was a clarification)."""
    facts: list[str] = []
    if session.last_area_codes:
        facts.append(f"areas={session.last_area_codes}")
    if session.last_metric:
        facts.append(f"metric={session.last_metric!r}")
    if session.last_dwelling_status:
        facts.append(f"dataset={session.last_dwelling_status!r}")
    if session.last_start_period:
        facts.append(f"period_start={session.last_start_period.label!r}")
    if session.last_end_period:
        facts.append(f"period_end={session.last_end_period.label!r}")
    if session.last_result_reference:
        facts.append(f"last_result={session.last_result_reference!r}")
    if not facts:
        return None
    return "[Context from your previous turn -- use only if this question refers back to it: " + ", ".join(facts) + "]"


def _build_agent_input(session: ConversationSession, question: str) -> AgentInput:
    """(`STORY-005`) A first turn (`session.recent_messages` empty) is a
    plain string -- unchanged from `STORY-003`'s behaviour and every
    existing stubbed test's signature. A follow-up turn is the verbatim
    recent-message window (the linguistic-context rationale `ADR-008`
    v15 gives for keeping it at all) plus `_build_recap`'s compact
    structured facts, glued onto the final user message."""
    if not session.recent_messages:
        return question
    recap = _build_recap(session)
    final_text = f"{recap}\n\n{question}" if recap else question
    history = [{"role": message.role, "content": message.text} for message in session.recent_messages]
    return [*history, {"role": "user", "content": final_text}]


def _append_repair_instruction(agent_input: AgentInput, instruction: str) -> AgentInput:
    """Appends a repair correction to whichever shape `agent_input` is --
    onto the bare string for a first turn, or onto the last (current-turn)
    message's content for a follow-up, never onto an earlier turn's
    verbatim history."""
    if isinstance(agent_input, str):
        return f"{agent_input}\n\n{instruction}"
    if not agent_input:
        return instruction
    *earlier, last = agent_input
    updated_last = {**last, "content": f"{last['content']}\n\n{instruction}"}
    return [*earlier, updated_last]


def _apply_session_update(session: ConversationSession, updated: ConversationSession) -> None:
    """`ConversationSession` is otherwise an immutable value object --
    `record_turn` never mutates its input, only returns a new instance.
    But `answer_question`'s contract is fixed at `-> AgentTurnResult`
    (design §8.2), with no second return value for an updated session.
    The caller (`ui/ask_the_data.py`) holds one long-lived
    `ConversationSession` instance across turns via `st.session_state`;
    updating its fields in place here is what lets that same held
    reference reflect the new turn without a signature change."""
    for field_name in ConversationSession.model_fields:
        setattr(session, field_name, getattr(updated, field_name))


_repository_singleton: Repository | None = None


def _get_repository() -> Repository:
    global _repository_singleton
    if _repository_singleton is None:
        _repository_singleton = Repository.open(PROCESSED_DIR)
    return _repository_singleton


def _unavailable_result(message: str = UNAVAILABLE_MESSAGE) -> AgentTurnResult:
    return AgentTurnResult(status="unavailable", answer_text=message)


def _raw_item_get(raw_item: object, key: str) -> object | None:
    """`raw_item` is a plain `dict` in hand-built test stubs but a typed SDK
    object (e.g. `ResponseFunctionToolCall`) in real runs -- reads `key`
    from either shape, never raises."""
    return raw_item.get(key) if isinstance(raw_item, dict) else getattr(raw_item, key, None)


def _tool_call_arguments(raw_item: object) -> dict:
    """Best-effort extraction of a tool call's arguments for observability
    (`ToolCallLog.arguments`) -- never raises; an unparseable/absent
    arguments payload just yields an empty dict rather than failing the
    whole turn over a logging concern."""
    raw_arguments = _raw_item_get(raw_item, "arguments")
    if not isinstance(raw_arguments, str):
        return {}
    try:
        parsed = json.loads(raw_arguments)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _reconstruct_structured_result(item: ToolCallOutputItem, call_id_to_tool_name: dict[str, str]) -> BaseModel | None:
    """Recovers a typed result for `structured_data` from a tool call's
    output. `ToolCallOutputItem.output` is the tool function's raw return
    value -- confirmed by `SPIKE-001` against a live run to be a plain
    `dict` (per `median_price_lookup_impl`'s documented "always a JSON-
    serialisable dict" contract), not a `BaseModel` instance, so success
    results must be reconstructed via `_TOOL_RESULT_SCHEMAS`, keyed by the
    call's tool name. Returns `None` (never raises) for error-status
    outputs, unregistered tools, or a result that doesn't match its
    schema -- structured_data is best-effort observability, not something
    a malformed entry should take down the whole turn over."""
    if isinstance(item.output, BaseModel):  # already typed (e.g. a future tool, or a test stub)
        return item.output
    if not isinstance(item.output, dict) or item.output.get("status") != "ok":
        return None
    call_id = _raw_item_get(item.raw_item, "call_id")
    tool_name = call_id_to_tool_name.get(call_id) if isinstance(call_id, str) else None
    schema = _TOOL_RESULT_SCHEMAS.get(tool_name) if tool_name else None
    if schema is None:
        return None
    fields = {key: value for key, value in item.output.items() if key != "status"}
    try:
        return schema.model_validate(fields)
    except Exception as exc:  # pydantic ValidationError or similar -- log and skip, don't fail the turn
        logger.warning("Could not reconstruct %s from tool output %r: %s", schema.__name__, item.output, exc)
        return None


def _tool_result_summary(item: ToolCallOutputItem) -> str:
    """A short, bounded description of a tool call's outcome for the
    observability log (`TASK-015`) -- never the full result payload (that's
    already `structured_data`'s job, and tool arguments/results here are
    never secrets, but still kept short per design §12's "no more than
    needed for debugging")."""
    output = item.output
    if isinstance(output, dict):
        status = output.get("status", "unknown")
        return status if status == "ok" else f"{status}: {output.get('message', '')}"[:120]
    return str(output)[:120]


def _extract_turn_data(
    run_result: object, elapsed_ms: float, session_id: str, turn_number: int
) -> tuple[list[BaseModel], list[ToolCallLog]]:
    """`structured_data` (the typed tool-result objects backing the
    answer) and `tool_calls` (observability log), read from the run's
    `new_items` -- the same object the grounding guardrail validates
    claims against (`TASK-010`). (`TASK-015`) Also emits one structured
    `tool_call` log event per call, combining the call's name/arguments
    with its own output's status once both are seen."""
    structured_data: list[BaseModel] = []
    tool_calls: list[ToolCallLog] = []
    call_id_to_tool_name: dict[str, str] = {}
    for item in getattr(run_result, "new_items", []):
        if isinstance(item, ToolCallItem):
            call_id = _raw_item_get(item.raw_item, "call_id")
            tool_name = item.tool_name or "unknown"
            if isinstance(call_id, str):
                call_id_to_tool_name[call_id] = tool_name
            tool_calls.append(
                ToolCallLog(
                    tool_name=tool_name,
                    arguments=_tool_call_arguments(item.raw_item),
                    # Per-tool-call timing isn't exposed at this level of the SDK's
                    # result object; this story approximates with the whole turn's
                    # elapsed time, refined if/when finer-grained timing is needed.
                    latency_ms=elapsed_ms,
                )
            )
        elif isinstance(item, ToolCallOutputItem):
            structured = _reconstruct_structured_result(item, call_id_to_tool_name)
            if structured is not None:
                structured_data.append(structured)
            output_call_id = _raw_item_get(item.raw_item, "call_id")
            output_tool_name = call_id_to_tool_name.get(output_call_id) if isinstance(output_call_id, str) else None
            matching_call = (
                next((c for c in tool_calls if c.tool_name == output_tool_name), None) if output_tool_name else None
            )
            log_event(
                "tool_call", session_id, turn_number,
                tool_name=output_tool_name or "unknown",
                arguments=matching_call.arguments if matching_call else {},
                result=_tool_result_summary(item),
                latency_ms=round(elapsed_ms, 1),
            )
    return structured_data, tool_calls


def _log_openai_call(run_result: object, model: str, session_id: str, turn_number: int, elapsed_ms: float) -> None:
    """(`TASK-015`) One event per `Runner.run_sync` invocation (the
    original attempt and, separately, a repair re-run each get their own
    event) -- `raw_responses` is the Agents SDK's own list of individual
    model round-trips within that one call; `usage.total_tokens` summed
    across them is this call's total token spend, and its length is the
    "turn count" design §12 asks for at this granularity. Hand-built test
    stubs (`SimpleNamespace`) have no `raw_responses` attribute -- this
    degrades to `total_tokens=0`/`sdk_turns=0` rather than raising."""
    raw_responses = getattr(run_result, "raw_responses", [])
    total_tokens = sum(getattr(getattr(response, "usage", None), "total_tokens", 0) or 0 for response in raw_responses)
    log_event(
        "openai_call", session_id, turn_number,
        model=model, total_tokens=total_tokens, sdk_turns=len(raw_responses), latency_ms=round(elapsed_ms, 1),
    )


def _answered_result(draft: DraftAnswer, structured_data: list[BaseModel], tool_calls: list[ToolCallLog]) -> AgentTurnResult:
    """(`STORY-008`) `draft.status` -- the model's own stated turn intent --
    passes straight through; `AgentTurnResult.status`'s `Literal` is a
    strict superset of `DraftAnswer.status`'s (it adds `"unavailable"`,
    never settable by the model), so this is always a valid assignment."""
    return AgentTurnResult(
        status=draft.status,
        answer_text=draft.answer_text,
        structured_data=structured_data,
        claims=draft.claims,
        tool_calls=tool_calls,
        coverage_caveats=draft.coverage_caveats,
        chart_spec=draft.chart_spec,
        period_assumptions=draft.period_assumptions,
    )


def _fallback_result(structured_data: list[BaseModel], tool_calls: list[ToolCallLog]) -> AgentTurnResult:
    """`TASK-010` AC2: released when neither the original draft nor one
    repair attempt passed the grounding check -- a purely mechanical,
    tool-output-only answer, never the unverified draft."""
    return AgentTurnResult(
        status="answered",
        answer_text=build_templated_fallback(structured_data),
        structured_data=structured_data,
        tool_calls=tool_calls,
    )


def _attempt_repair(
    run_agent: Callable[..., object],
    agent: Agent,
    agent_input: AgentInput,
    check: GroundingCheckResult,
    model: str,
    session_id: str,
    turn_number: int,
) -> tuple[DraftAnswer, list[BaseModel], list[ToolCallLog], GroundingCheckResult] | None:
    """One bounded repair re-run with a corrective instruction appended.
    `None` if the repair call itself fails or doesn't produce a
    `DraftAnswer` -- the caller falls back to the templated answer using
    the *original* turn's data in that case, never escalating a grounding
    repair failure into `status="unavailable"`."""
    repair_input = _append_repair_instruction(agent_input, build_repair_instruction(check))
    started_at = time.perf_counter()
    try:
        repaired_run_result = run_agent(agent, repair_input, max_turns=MAX_TURNS)
    except Exception as exc:
        logger.warning("Grounding repair attempt raised: %s", exc)
        return None
    elapsed_ms = (time.perf_counter() - started_at) * 1000
    _log_openai_call(repaired_run_result, model, session_id, turn_number, elapsed_ms)

    repaired_output = getattr(repaired_run_result, "final_output", None)
    if not isinstance(repaired_output, DraftAnswer):
        logger.warning("Grounding repair attempt did not produce a DraftAnswer; final_output=%r", repaired_output)
        return None

    repaired_structured_data, repaired_tool_calls = _extract_turn_data(
        repaired_run_result, elapsed_ms, session_id, turn_number
    )
    repaired_output = autofill_missing_claims(repaired_output, repaired_structured_data)
    repaired_check = check_grounding(repaired_output, repaired_structured_data)
    return repaired_output, repaired_structured_data, repaired_tool_calls, repaired_check


def _finalize_turn(
    run_agent: Callable[..., object],
    agent: Agent,
    agent_input: AgentInput,
    run_result: object,
    elapsed_ms: float,
    model: str,
    session_id: str,
    turn_number: int,
) -> AgentTurnResult:
    _log_openai_call(run_result, model, session_id, turn_number, elapsed_ms)
    final_output = getattr(run_result, "final_output", None)
    if not isinstance(final_output, DraftAnswer):
        logger.warning("Agent run did not produce a DraftAnswer; final_output=%r", final_output)
        return _unavailable_result()

    structured_data, tool_calls = _extract_turn_data(run_result, elapsed_ms, session_id, turn_number)
    final_output = autofill_missing_claims(final_output, structured_data)
    check = check_grounding(final_output, structured_data)
    if check.valid:
        return _answered_result(final_output, structured_data, tool_calls)

    logger.warning("Grounding check failed for this turn (%s); attempting one repair", check.reasons)
    log_event("guardrail_trigger", session_id, turn_number, trigger="repair_attempted", reasons=check.reasons)
    repaired = _attempt_repair(run_agent, agent, agent_input, check, model, session_id, turn_number)
    if repaired is None:
        return _fallback_result(structured_data, tool_calls)

    repaired_output, repaired_structured_data, repaired_tool_calls, repaired_check = repaired
    if repaired_check.valid:
        return _answered_result(repaired_output, repaired_structured_data, repaired_tool_calls)

    logger.error("Grounding repair attempt still failed (%s); releasing templated fallback", repaired_check.reasons)
    log_event(
        "guardrail_trigger", session_id, turn_number,
        trigger="fallback_released", reasons=repaired_check.reasons,
    )
    return _fallback_result(repaired_structured_data, repaired_tool_calls)


def answer_question(
    session: ConversationSession,
    question: str,
    *,
    config: Config | None = None,
    repository: Repository | None = None,
    run_agent: Callable[..., object] | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> AgentTurnResult:
    """Never raises for an expected failure mode -- see module docstring.
    `config`/`repository`/`run_agent`/`sleep` are injectable for tests;
    every real caller (`ui/ask_the_data.py`, the future eval harness) uses
    the defaults. (`STORY-005`) On any non-`"unavailable"` result, `session`
    is updated in place via `record_turn` -- see `_apply_session_update`."""
    resolved_config = config or load_config()
    if not resolved_config.openai_available:
        return _unavailable_result()

    resolved_repository = repository or _get_repository()
    resolved_run_agent = run_agent or _default_run_agent
    agent = build_agent(resolved_repository, resolved_config.openai_model)
    agent_input = _build_agent_input(session, question)

    # (TASK-015) session_id is stable for the whole session; turn_number is
    # this turn's provisional ordinal -- record_turn below is the only place
    # that actually commits it to `session`, but tool_call/openai_call/
    # guardrail_trigger events emitted *during* this turn need it now, before
    # that happens.
    session_id = session.session_id
    turn_number = session.turn_number + 1

    last_error: Exception | None = None
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        started_at = time.perf_counter()
        try:
            run_result = resolved_run_agent(agent, agent_input, max_turns=MAX_TURNS)
        except Exception as exc:  # translate any SDK/network failure into a typed result
            last_error = exc
            logger.warning("Agent run attempt %d/%d failed: %s", attempt, RETRY_ATTEMPTS, exc)
            if attempt < RETRY_ATTEMPTS:
                sleep(0.5 * attempt)  # small fixed backoff between the two bounded attempts
            continue
        elapsed_ms = (time.perf_counter() - started_at) * 1000
        result = _finalize_turn(
            resolved_run_agent, agent, agent_input, run_result, elapsed_ms,
            resolved_config.openai_model, session_id, turn_number,
        )
        if result.status != "unavailable":
            updated_session = record_turn(session, question, result, resolved_repository)
            _apply_session_update(session, updated_session)
            log_event(
                "turn_completed", session_id, turn_number,
                status=result.status,
                resolved_area_codes=updated_session.last_area_codes,
                resolved_period_start=updated_session.last_start_period.label if updated_session.last_start_period else None,
                resolved_period_end=updated_session.last_end_period.label if updated_session.last_end_period else None,
                tool_call_count=len(result.tool_calls),
            )
        else:
            log_event("turn_completed", session_id, turn_number, status=result.status, tool_call_count=0)
        return result

    logger.error("Agent unavailable after %d attempts: %s", RETRY_ATTEMPTS, last_error)
    log_event("turn_completed", session_id, turn_number, status="unavailable", tool_call_count=0)
    return _unavailable_result()
