"""Agent Orchestrator (STORY-003, design CMP-011, §8.2).

`answer_question(session, question) -> AgentTurnResult` is the single,
UI-agnostic entry point -- used identically by `ui/ask_the_data.py` and,
from Increment 5, the evaluation harness. It never raises for an expected
failure mode (missing key, unreachable API); those become
`AgentTurnResult.status` values instead, so callers render rather than
catch (design §8.2's own stated contract).

**Scope note:** `session: ConversationSession` is accepted (matching the
contract every future increment's callers rely on) but not yet threaded
into the model call or updated after the turn -- follow-up resolution is
`STORY-005`'s job (Increment 4, `agent/session.py`/`CMP-007`, not built
yet), and this story's own scope explicitly excludes follow-ups. Threading
an unused/half-built mechanism in now would be exactly the kind of
speculative extension point YAGNI warns against.

**Testability:** `run_agent` is an injected seam (defaults to the real
`Runner.run_sync`) so this module's own logic -- retry, status mapping,
`structured_data`/`tool_calls` extraction -- is fully unit-tested with a
stub, per the design's own "stubbed Agents SDK model" testing philosophy
(§13), without a live API key or any network call.
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
from core.models import AgentTurnResult, ConversationSession, DraftAnswer, ToolCallLog
from core.repository import Repository

logger = logging.getLogger(__name__)

MAX_TURNS = 6  # THR-006/NFR-006: bounds runaway tool-call loops
RETRY_ATTEMPTS = 2  # 1 bounded retry (design §8.1) = 2 total attempts
UNAVAILABLE_MESSAGE = (
    "The assistant is currently unavailable. Please try again shortly, or use the "
    "Explore trends / Compare and rank tabs, which don't require it."
)

PROCESSED_DIR = Path(__file__).resolve().parents[1] / "data" / "processed"


class RunAgent(Protocol):
    """The seam `answer_question` calls through -- real implementation is
    `Runner.run_sync`; tests inject a stub returning a hand-built result."""

    def __call__(self, agent: Agent, question: str, *, max_turns: int) -> object: ...


def _default_run_agent(agent: Agent, question: str, *, max_turns: int) -> object:
    return Runner.run_sync(agent, question, max_turns=max_turns)


_repository_singleton: Repository | None = None


def _get_repository() -> Repository:
    global _repository_singleton
    if _repository_singleton is None:
        _repository_singleton = Repository.open(PROCESSED_DIR)
    return _repository_singleton


def _unavailable_result(message: str = UNAVAILABLE_MESSAGE) -> AgentTurnResult:
    return AgentTurnResult(status="unavailable", answer_text=message)


def _tool_call_arguments(raw_item: object) -> dict:
    """Best-effort extraction of a tool call's arguments for observability
    (`ToolCallLog.arguments`) -- never raises; an unparseable/absent
    arguments payload just yields an empty dict rather than failing the
    whole turn over a logging concern."""
    raw_arguments = raw_item.get("arguments") if isinstance(raw_item, dict) else getattr(raw_item, "arguments", None)
    if not isinstance(raw_arguments, str):
        return {}
    try:
        parsed = json.loads(raw_arguments)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _extract_turn_data(run_result: object, elapsed_ms: float) -> tuple[list[BaseModel], list[ToolCallLog]]:
    """`structured_data` (the typed tool-result objects backing the
    answer) and `tool_calls` (observability log), read from the run's
    `new_items` -- the same object the grounding guardrail will validate
    claims against once `TASK-010` lands (Increment 4)."""
    structured_data: list[BaseModel] = []
    tool_calls: list[ToolCallLog] = []
    for item in getattr(run_result, "new_items", []):
        if isinstance(item, ToolCallOutputItem) and isinstance(item.output, BaseModel):
            structured_data.append(item.output)
        elif isinstance(item, ToolCallItem):
            tool_calls.append(
                ToolCallLog(
                    tool_name=item.tool_name or "unknown",
                    arguments=_tool_call_arguments(item.raw_item),
                    # Per-tool-call timing isn't exposed at this level of the SDK's
                    # result object; this story approximates with the whole turn's
                    # elapsed time, refined if/when finer-grained timing is needed.
                    latency_ms=elapsed_ms,
                )
            )
    return structured_data, tool_calls


def _build_turn_result(run_result: object, elapsed_ms: float) -> AgentTurnResult:
    final_output = getattr(run_result, "final_output", None)
    if not isinstance(final_output, DraftAnswer):
        logger.warning("Agent run did not produce a DraftAnswer; final_output=%r", final_output)
        return _unavailable_result()

    structured_data, tool_calls = _extract_turn_data(run_result, elapsed_ms)
    return AgentTurnResult(
        status="answered",
        answer_text=final_output.answer_text,
        structured_data=structured_data,
        claims=final_output.claims,
        tool_calls=tool_calls,
        chart_spec=final_output.chart_spec,
    )


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
    the defaults."""
    del session  # accepted per the design contract; not yet used (see module docstring)

    resolved_config = config or load_config()
    if not resolved_config.openai_available:
        return _unavailable_result()

    resolved_repository = repository or _get_repository()
    resolved_run_agent = run_agent or _default_run_agent
    agent = build_agent(resolved_repository, resolved_config.openai_model)

    last_error: Exception | None = None
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        started_at = time.perf_counter()
        try:
            run_result = resolved_run_agent(agent, question, max_turns=MAX_TURNS)
        except Exception as exc:  # translate any SDK/network failure into a typed result
            last_error = exc
            logger.warning("Agent run attempt %d/%d failed: %s", attempt, RETRY_ATTEMPTS, exc)
            if attempt < RETRY_ATTEMPTS:
                sleep(0.5 * attempt)  # small fixed backoff between the two bounded attempts
            continue
        elapsed_ms = (time.perf_counter() - started_at) * 1000
        return _build_turn_result(run_result, elapsed_ms)

    logger.error("Agent unavailable after %d attempts: %s", RETRY_ATTEMPTS, last_error)
    return _unavailable_result()
