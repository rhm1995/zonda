"""STORY-008 integration tests: ambiguous area references trigger a
clarifying question (`status="clarification_needed"`) rather than a guess.
Stubbed-model tests per the design's own testing philosophy (§13) -- the
*mechanism* an ambiguous area produces (`GeographyMatch.status="ambiguous"`,
named candidates) is already unit-tested directly in `test_agent_definition.py`
and `test_geography.py`; this file proves the orchestrator/UI correctly
carry a clarifying turn through end to end."""

from __future__ import annotations

from types import SimpleNamespace
from pathlib import Path

from agents import Agent
from streamlit.testing.v1 import AppTest

from agent.config import Config
from agent.orchestrator import answer_question
from core.models import ConversationSession, DraftAnswer
from core.repository import Repository

REPO_ROOT = Path(__file__).resolve().parents[2]

AVAILABLE_CONFIG = Config(
    openai_api_key="sk-test", openai_model="gpt-4o-mini", openai_available=True, log_level="INFO"
)
EMPTY_SESSION = ConversationSession()
_STUB_AGENT = Agent(name="stub", instructions="stub")


def test_ambiguous_area_produces_clarification_needed_naming_both_candidates(
    real_repository: Repository,
) -> None:
    """AC1: no figure stated for either candidate."""
    clarifying_draft = DraftAnswer(
        status="clarification_needed",
        answer_text="Did you mean Richmond upon Thames (London) or Richmondshire (North Yorkshire)?",
        claims=[],
    )

    def run_agent(agent: Agent, question: str, *, max_turns: int) -> object:
        return SimpleNamespace(final_output=clarifying_draft, new_items=[])

    result = answer_question(
        EMPTY_SESSION, "House prices in Richmond",
        config=AVAILABLE_CONFIG, repository=real_repository, run_agent=run_agent,
    )
    assert result.status == "clarification_needed"
    assert "Richmond upon Thames" in result.answer_text
    assert "Richmondshire" in result.answer_text
    assert result.claims == []
    assert result.structured_data == []


def test_unambiguous_area_is_not_treated_as_a_clarification(real_repository: Repository) -> None:
    """AC3: the guardrail/status machinery doesn't over-fire on a clean case."""
    from core.models import EvidenceRef, GroundedClaim, PriceLookupResult
    from agents import ToolCallItem, ToolCallOutputItem

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
    answered_draft = DraftAnswer(
        status="answered",
        answer_text="The median price was £400,000.",
        claims=[
            GroundedClaim(
                value=400000, unit="gbp", la_code="E08000003", period_label="Year ending Sep 2025",
                evidence=[EvidenceRef(result_index=0, row_index=None, field="price_gbp")],
            )
        ],
    )

    def run_agent(agent: Agent, question: str, *, max_turns: int) -> object:
        return SimpleNamespace(final_output=answered_draft, new_items=[tool_call, tool_output])

    result = answer_question(
        EMPTY_SESSION, "What was the price in Manchester?",
        config=AVAILABLE_CONFIG, repository=real_repository, run_agent=run_agent,
    )
    assert result.status == "answered"


def test_clarified_follow_up_naming_the_specific_candidate_resolves_normally(
    real_repository: Repository,
) -> None:
    """AC2 (the part not dependent on STORY-005's session continuity): once
    the user names the specific candidate directly, it resolves and
    answers normally -- no further clarification loop."""
    from core.models import EvidenceRef, GroundedClaim, PriceLookupResult
    from agents import ToolCallItem, ToolCallOutputItem

    price_result = PriceLookupResult(
        la_code="E09000027", la_name="Richmond upon Thames", dataset="existing",
        period_label="Year ending Sep 2025", price_gbp=750000, suppressed=False,
    )
    tool_call = ToolCallItem(
        agent=_STUB_AGENT,
        raw_item={"type": "function_call", "name": "median_price_lookup", "call_id": "call_1", "arguments": "{}"},
    )
    tool_output = ToolCallOutputItem(
        agent=_STUB_AGENT, raw_item={"type": "function_call_output", "call_id": "call_1", "output": "{}"},
        output=price_result,
    )
    answered_draft = DraftAnswer(
        status="answered",
        answer_text="The median price in Richmond upon Thames was £750,000.",
        claims=[
            GroundedClaim(
                value=750000, unit="gbp", la_code="E09000027", period_label="Year ending Sep 2025",
                evidence=[EvidenceRef(result_index=0, row_index=None, field="price_gbp")],
            )
        ],
    )

    def run_agent(agent: Agent, question: str, *, max_turns: int) -> object:
        return SimpleNamespace(final_output=answered_draft, new_items=[tool_call, tool_output])

    result = answer_question(
        EMPTY_SESSION, "House prices in Richmond upon Thames",
        config=AVAILABLE_CONFIG, repository=real_repository, run_agent=run_agent,
    )
    assert result.status == "answered"
    assert "750,000" in result.answer_text


def _run_dashboard() -> AppTest:
    app = AppTest.from_file(str(REPO_ROOT / "ui" / "dashboard.py"))
    app.run(timeout=30)
    assert app.exception == []
    return app


def test_ui_renders_clarification_needed_distinctly(monkeypatch) -> None:
    import ui.ask_the_data as ask_the_data_module

    monkeypatch.setattr(
        ask_the_data_module, "load_config",
        lambda: Config(openai_api_key="sk-test", openai_model="gpt-4o-mini", openai_available=True, log_level="INFO"),
    )

    def stub_answer_question(session, question):
        from core.models import AgentTurnResult

        return AgentTurnResult(
            status="clarification_needed",
            answer_text="Did you mean Richmond upon Thames or Richmondshire?",
        )

    monkeypatch.setattr(ask_the_data_module, "answer_question", stub_answer_question)

    app = _run_dashboard()
    app.text_input(key="ask_the_data_question").set_value("House prices in Richmond").run(timeout=30)
    app.button(key="ask_the_data_submit").click().run(timeout=30)
    assert app.exception == []
    assert any("Richmond upon Thames" in i.value for i in app.info)
