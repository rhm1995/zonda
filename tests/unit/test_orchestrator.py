"""STORY-003 integration tests: `answer_question` driven with a **stubbed**
Agents SDK run (no real API call, no network, no cost) -- per the design's
own testing philosophy (§13): "agent/orchestrator.py driven with a stubbed
Agents SDK model ... no real API calls, no cost, fast enough to run on
every change." """

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from agents import Agent, ToolCallItem, ToolCallOutputItem

import agent.observability as observability
from agent.config import Config
from agent.orchestrator import RETRY_ATTEMPTS, UNAVAILABLE_MESSAGE, answer_question
from core.models import ConversationSession, DraftAnswer, EvidenceRef, GroundedClaim, PriceLookupResult
from core.repository import Repository

AVAILABLE_CONFIG = Config(
    openai_api_key="sk-test", openai_model="gpt-4o-mini", openai_available=True, log_level="INFO"
)
UNAVAILABLE_CONFIG = Config(
    openai_api_key=None, openai_model="gpt-4o-mini", openai_available=False, log_level="INFO"
)
EMPTY_SESSION = ConversationSession()

_STUB_AGENT = Agent(name="stub", instructions="stub")


def _draft_answer_run_result() -> SimpleNamespace:
    price_result = PriceLookupResult(
        la_code="E08000003",
        la_name="Manchester",
        dataset="existing",
        period_label="Year ending Sep 2025",
        price_gbp=400000,
        suppressed=False,
    )
    tool_call = ToolCallItem(
        agent=_STUB_AGENT,
        raw_item={
            "type": "function_call",
            "name": "median_price_lookup",
            "call_id": "call_1",
            "arguments": '{"area": "Manchester", "dataset": "existing", "month": "September", "year": 2025}',
        },
    )
    tool_output = ToolCallOutputItem(
        agent=_STUB_AGENT,
        raw_item={"type": "function_call_output", "call_id": "call_1", "output": "{}"},
        output=price_result,
    )
    draft_answer = DraftAnswer(
        answer_text="The median price of an existing detached house in Manchester in the year "
        "ending September 2025 was £400,000.",
        claims=[
            GroundedClaim(
                value=400000,
                unit="gbp",
                la_code="E08000003",
                period_label="Year ending Sep 2025",
                evidence=[EvidenceRef(result_index=0, row_index=None, field="price_gbp")],
            )
        ],
    )
    return SimpleNamespace(final_output=draft_answer, new_items=[tool_call, tool_output])


def test_missing_key_returns_unavailable_status_without_calling_the_agent() -> None:
    calls: list[Any] = []

    def spying_run_agent(agent: Agent, question: str, *, max_turns: int) -> object:
        calls.append(question)
        raise AssertionError("should never be called when openai_available is False")

    result = answer_question(
        EMPTY_SESSION, "What was the price in Manchester?", config=UNAVAILABLE_CONFIG, run_agent=spying_run_agent
    )
    assert result.status == "unavailable"
    assert calls == []


def test_happy_path_returns_answered_with_structured_data_and_claims(real_repository: Repository) -> None:
    def stub_run_agent(agent: Agent, question: str, *, max_turns: int) -> object:
        return _draft_answer_run_result()

    result = answer_question(
        EMPTY_SESSION,
        "What was the median price of an existing detached house in Manchester in September 2025?",
        config=AVAILABLE_CONFIG,
        repository=real_repository,
        run_agent=stub_run_agent,
    )
    assert result.status == "answered"
    assert "400,000" in result.answer_text
    assert len(result.structured_data) == 1
    assert isinstance(result.structured_data[0], PriceLookupResult)
    assert result.structured_data[0].price_gbp == 400000
    assert len(result.claims) == 1
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].tool_name == "median_price_lookup"
    assert result.tool_calls[0].arguments["area"] == "Manchester"


def test_api_failure_retries_once_then_returns_unavailable(real_repository: Repository) -> None:
    call_count = 0

    def always_failing_run_agent(agent: Agent, question: str, *, max_turns: int) -> object:
        nonlocal call_count
        call_count += 1
        raise ConnectionError("simulated API unavailable")

    result = answer_question(
        EMPTY_SESSION,
        "What was the price in Manchester?",
        config=AVAILABLE_CONFIG,
        repository=real_repository,
        run_agent=always_failing_run_agent,
        sleep=lambda _seconds: None,  # keep the test fast
    )
    assert result.status == "unavailable"
    assert result.answer_text == UNAVAILABLE_MESSAGE
    assert call_count == RETRY_ATTEMPTS  # exactly one retry (2 total attempts)


def test_api_recovers_on_the_retry_attempt(real_repository: Repository) -> None:
    call_count = 0

    def fails_once_then_succeeds(agent: Agent, question: str, *, max_turns: int) -> object:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise ConnectionError("transient failure")
        return _draft_answer_run_result()

    result = answer_question(
        EMPTY_SESSION,
        "What was the price in Manchester?",
        config=AVAILABLE_CONFIG,
        repository=real_repository,
        run_agent=fails_once_then_succeeds,
        sleep=lambda _seconds: None,
    )
    assert result.status == "answered"
    assert call_count == 2


def test_unexpected_final_output_type_fails_safe_to_unavailable(real_repository: Repository) -> None:
    def malformed_run_agent(agent: Agent, question: str, *, max_turns: int) -> object:
        return SimpleNamespace(final_output="not a DraftAnswer", new_items=[])

    result = answer_question(
        EMPTY_SESSION,
        "What was the price in Manchester?",
        config=AVAILABLE_CONFIG,
        repository=real_repository,
        run_agent=malformed_run_agent,
    )
    assert result.status == "unavailable"


def test_structured_data_is_reconstructed_from_a_real_dict_tool_output(real_repository: Repository) -> None:
    """Regression test for a bug SPIKE-001's live run surfaced: the real
    Agents SDK sets `ToolCallOutputItem.output` to the tool function's raw
    return value -- a plain `dict` per `median_price_lookup_impl`'s own
    documented contract -- never a `BaseModel` instance. The earlier
    hand-built stub in `_draft_answer_run_result` used a real
    `PriceLookupResult` for `.output`, which passed but didn't match
    reality; this test uses a plain dict, as the live SDK actually does."""
    tool_call = ToolCallItem(
        agent=_STUB_AGENT,
        raw_item={
            "type": "function_call",
            "name": "median_price_lookup",
            "call_id": "call_real_1",
            "arguments": '{"area": "Manchester", "dataset": "existing", "month": "September", "year": 2025}',
        },
    )
    tool_output = ToolCallOutputItem(
        agent=_STUB_AGENT,
        raw_item={"type": "function_call_output", "call_id": "call_real_1", "output": "..."},
        output={  # a plain dict, as the real SDK actually returns -- not a BaseModel
            "status": "ok",
            "la_code": "E08000003",
            "la_name": "Manchester",
            "dataset": "existing",
            "period_label": "Year ending Sep 2025",
            "price_gbp": 400000,
            "suppressed": False,
        },
    )
    draft_answer = DraftAnswer(answer_text="The median price was £400,000.")
    run_result = SimpleNamespace(final_output=draft_answer, new_items=[tool_call, tool_output])

    result = answer_question(
        EMPTY_SESSION,
        "What was the median price of an existing detached house in Manchester in September 2025?",
        config=AVAILABLE_CONFIG,
        repository=real_repository,
        run_agent=lambda *a, **k: run_result,
    )
    assert result.status == "answered"
    assert len(result.structured_data) == 1
    assert isinstance(result.structured_data[0], PriceLookupResult)
    assert result.structured_data[0].price_gbp == 400000
    assert result.structured_data[0].la_name == "Manchester"


def test_ungrounded_draft_triggers_one_repair_and_releases_the_repaired_answer(
    real_repository: Repository,
) -> None:
    """TASK-010 AC2 (repair branch), verified end to end through the
    orchestrator, not just `check_grounding` in isolation."""
    price_result = PriceLookupResult(
        la_code="E08000003", la_name="Manchester", dataset="existing",
        period_label="Year ending Sep 2025", price_gbp=400000, suppressed=False,
    )
    tool_call = ToolCallItem(
        agent=_STUB_AGENT,
        raw_item={"type": "function_call", "name": "median_price_lookup", "call_id": "call_1", "arguments": "{}"},
    )
    tool_output = ToolCallOutputItem(
        agent=_STUB_AGENT, raw_item={"type": "function_call_output", "call_id": "call_1", "output": "{}"},
        output=price_result,
    )
    # A growth-percentage figure with no matching field anywhere in
    # structured_data -- autofill_missing_claims cannot resolve this (only
    # median_price_lookup ran; there is no growth/percentage field to
    # match), so it must genuinely reach the repair path, not just the
    # cheaper autofill one `test_autofill_*` in test_guardrails.py already covers.
    ungrounded_draft = DraftAnswer(answer_text="The market grew by 43.5% overall.", claims=[])
    grounded_draft = DraftAnswer(
        answer_text="The median price was £400,000.",
        claims=[
            GroundedClaim(
                value=400000, unit="gbp", la_code="E08000003", period_label="Year ending Sep 2025",
                evidence=[EvidenceRef(result_index=0, row_index=None, field="price_gbp")],
            )
        ],
    )
    calls: list[str] = []

    def run_agent(agent: Agent, question: str, *, max_turns: int) -> object:
        calls.append(question)
        draft = grounded_draft if len(calls) > 1 else ungrounded_draft
        return SimpleNamespace(final_output=draft, new_items=[tool_call, tool_output])

    result = answer_question(
        EMPTY_SESSION, "What was the price in Manchester?",
        config=AVAILABLE_CONFIG, repository=real_repository, run_agent=run_agent,
    )
    assert len(calls) == 2  # original + exactly one repair
    assert result.status == "answered"
    assert result.answer_text == "The median price was £400,000."
    assert len(result.claims) == 1


def test_grounding_failure_surviving_repair_releases_a_templated_fallback(
    real_repository: Repository,
) -> None:
    """TASK-010 AC2 (fallback branch): neither the original draft nor the
    repair passes -- the unverified draft text must never reach the user."""
    price_result = PriceLookupResult(
        la_code="E08000003", la_name="Manchester", dataset="existing",
        period_label="Year ending Sep 2025", price_gbp=400000, suppressed=False,
    )
    tool_call = ToolCallItem(
        agent=_STUB_AGENT,
        raw_item={"type": "function_call", "name": "median_price_lookup", "call_id": "call_1", "arguments": "{}"},
    )
    tool_output = ToolCallOutputItem(
        agent=_STUB_AGENT, raw_item={"type": "function_call_output", "call_id": "call_1", "output": "{}"},
        output=price_result,
    )
    fabricated_draft = DraftAnswer(answer_text="The median price was £999,999,999.", claims=[])

    def always_ungrounded_run_agent(agent: Agent, question: str, *, max_turns: int) -> object:
        return SimpleNamespace(final_output=fabricated_draft, new_items=[tool_call, tool_output])

    result = answer_question(
        EMPTY_SESSION, "What was the price in Manchester?",
        config=AVAILABLE_CONFIG, repository=real_repository, run_agent=always_ungrounded_run_agent,
    )
    assert result.status == "answered"  # a safe fallback answer, not "unavailable"
    assert "999,999,999" not in result.answer_text
    assert "£999,999,999" not in result.answer_text
    assert len(result.structured_data) == 1  # the real tool output is still surfaced


def test_repair_attempt_raising_still_falls_back_to_templated_answer(real_repository: Repository) -> None:
    price_result = PriceLookupResult(
        la_code="E08000003", la_name="Manchester", dataset="existing",
        period_label="Year ending Sep 2025", price_gbp=400000, suppressed=False,
    )
    tool_call = ToolCallItem(
        agent=_STUB_AGENT,
        raw_item={"type": "function_call", "name": "median_price_lookup", "call_id": "call_1", "arguments": "{}"},
    )
    tool_output = ToolCallOutputItem(
        agent=_STUB_AGENT, raw_item={"type": "function_call_output", "call_id": "call_1", "output": "{}"},
        output=price_result,
    )
    # Not autofill-resolvable (no matching field for this figure) -- see
    # the comment in test_ungrounded_draft_triggers_one_repair... above.
    ungrounded_draft = DraftAnswer(answer_text="Growth was 43.5%.", claims=[])
    calls = {"count": 0}

    def flaky_run_agent(agent: Agent, question: str, *, max_turns: int) -> object:
        calls["count"] += 1
        if calls["count"] == 1:
            return SimpleNamespace(final_output=ungrounded_draft, new_items=[tool_call, tool_output])
        raise ConnectionError("repair call failed")

    result = answer_question(
        EMPTY_SESSION, "What was the price in Manchester?",
        config=AVAILABLE_CONFIG, repository=real_repository, run_agent=flaky_run_agent,
    )
    assert result.status == "answered"
    assert calls["count"] == 2


def test_never_raises_for_expected_failure_modes(real_repository: Repository) -> None:
    """answer_question's own contract (design §8.2): never raises for
    missing key or API failure -- both surface as `status`, not an
    exception."""
    for config in (UNAVAILABLE_CONFIG, AVAILABLE_CONFIG):
        try:
            answer_question(
                EMPTY_SESSION,
                "irrelevant",
                config=config,
                repository=real_repository,
                run_agent=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")),
                sleep=lambda _s: None,
            )
        except Exception as exc:  # pragma: no cover - the assertion below is what matters
            pytest.fail(f"answer_question raised {exc!r} instead of returning a status")


# -- TASK-015: structured observability logging -------------------------------


def _fresh_log_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Resets `agent.observability`'s idempotency guard/handlers and
    attaches a fresh `FileHandler` at a `tmp_path` location -- never the
    real repository's own working directory (`_reset_observability_state`
    in `tests/unit/test_observability.py` does the same reset for that
    module's own direct tests)."""
    monkeypatch.setattr(observability, "_configured", False)
    observability.logger.handlers.clear()
    log_file = tmp_path / "app.jsonl"
    observability.configure_logging(log_file, "INFO")
    return log_file


def _read_events(log_file: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in log_file.read_text().splitlines()]


def test_a_successful_turn_logs_correlated_tool_call_openai_call_and_turn_completed_events(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, real_repository: Repository
) -> None:
    log_file = _fresh_log_file(tmp_path, monkeypatch)
    session = ConversationSession()

    def stub_run_agent(agent: Agent, question: str, *, max_turns: int) -> object:
        return _draft_answer_run_result()

    answer_question(
        session,
        "What was the median price of an existing detached house in Manchester in September 2025?",
        config=AVAILABLE_CONFIG,
        repository=real_repository,
        run_agent=stub_run_agent,
    )

    events = _read_events(log_file)
    event_types = [e["event"] for e in events]
    assert "openai_call" in event_types
    assert "tool_call" in event_types
    assert "turn_completed" in event_types
    # Every event from this one turn shares the same session_id/turn_number.
    assert {e["session_id"] for e in events} == {session.session_id}
    assert {e["turn_number"] for e in events} == {1}
    tool_call_event = next(e for e in events if e["event"] == "tool_call")
    assert tool_call_event["tool_name"] == "median_price_lookup"


def test_a_grounding_repair_logs_a_guardrail_trigger_event(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, real_repository: Repository
) -> None:
    log_file = _fresh_log_file(tmp_path, monkeypatch)

    tool_call = ToolCallItem(
        agent=_STUB_AGENT,
        raw_item={"type": "function_call", "name": "growth_metrics", "call_id": "call_1", "arguments": "{}"},
    )
    tool_output = ToolCallOutputItem(
        agent=_STUB_AGENT,
        raw_item={"type": "function_call_output", "call_id": "call_1", "output": "{}"},
        output={
            "la_code": "E08000003", "la_name": "Manchester", "dataset": "existing",
            "period_start_label": "Year ending Sep 2015", "period_end_label": "Year ending Sep 2025",
            "latest_price": 400000, "latest_price_period": "Year ending Sep 2025",
            "growth_gbp": 180000.0, "growth_pct": 81.8, "cagr_pct": 6.2, "suppressed_periods": [],
        },
    )
    ungrounded_draft = DraftAnswer(answer_text="The market grew by 43.5% overall.", claims=[])

    def always_ungrounded_run_agent(agent: Agent, question: str, *, max_turns: int) -> object:
        return SimpleNamespace(final_output=ungrounded_draft, new_items=[tool_call, tool_output])

    answer_question(
        ConversationSession(), "How has Manchester changed?",
        config=AVAILABLE_CONFIG, repository=real_repository, run_agent=always_ungrounded_run_agent,
    )

    guardrail_events = [e for e in _read_events(log_file) if e["event"] == "guardrail_trigger"]
    assert guardrail_events  # at least the "repair_attempted" trigger fired
    assert guardrail_events[0]["trigger"] == "repair_attempted"


def test_no_logged_event_ever_contains_the_api_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, real_repository: Repository
) -> None:
    """`TASK-015` AC2 -- no credential appears anywhere in the logs."""
    log_file = _fresh_log_file(tmp_path, monkeypatch)

    secret_config = Config(
        openai_api_key="sk-super-secret-value-should-never-appear",
        openai_model="gpt-4o-mini", openai_available=True, log_level="INFO",
    )

    def stub_run_agent(agent: Agent, question: str, *, max_turns: int) -> object:
        return _draft_answer_run_result()

    answer_question(
        ConversationSession(),
        "What was the median price of an existing detached house in Manchester in September 2025?",
        config=secret_config, repository=real_repository, run_agent=stub_run_agent,
    )

    assert "sk-super-secret-value-should-never-appear" not in log_file.read_text()
