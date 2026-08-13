"""STORY-003 integration tests: `answer_question` driven with a **stubbed**
Agents SDK run (no real API call, no network, no cost) -- per the design's
own testing philosophy (§13): "agent/orchestrator.py driven with a stubbed
Agents SDK model ... no real API calls, no cost, fast enough to run on
every change." """

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from agents import Agent, ToolCallItem, ToolCallOutputItem

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
