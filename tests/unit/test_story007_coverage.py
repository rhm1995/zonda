"""STORY-007 integration tests: out-of-coverage geography (Scotland/NI) is
correctly explained, with a partial-answer policy for mixed requests
(`ADR-010`). Stubbed-model tests per the design's testing philosophy (§13)
-- `resolve_geography`'s own out_of_coverage detection is already
unit-tested directly in `test_geography.py`/`test_agent_definition.py`;
this file proves the orchestrator/UI correctly carry the *policy*
(partial-answer-with-caveat vs. full decline) through end to end."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from agents import Agent, ToolCallItem, ToolCallOutputItem
from streamlit.testing.v1 import AppTest

from agent.config import Config
from agent.orchestrator import answer_question
from core.models import ConversationSession, DraftAnswer, EvidenceRef, GroundedClaim, PriceLookupResult
from core.repository import Repository

REPO_ROOT = Path(__file__).resolve().parents[2]

AVAILABLE_CONFIG = Config(
    openai_api_key="sk-test", openai_model="gpt-4o-mini", openai_available=True, log_level="INFO"
)
EMPTY_SESSION = ConversationSession()
_STUB_AGENT = Agent(name="stub", instructions="stub")


def test_mixed_coverage_request_answers_fully_for_covered_areas_with_caveats(
    real_repository: Repository,
) -> None:
    """AC1: "Compare Glasgow, Edinburgh, and Manchester..." -- full,
    correctly grounded figures for Manchester, explicit caveats for the
    two uncovered areas, no Scottish figures stated anywhere."""
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
    mixed_draft = DraftAnswer(
        status="answered",
        answer_text="Manchester's existing detached house price was £400,000. Glasgow and Edinburgh "
        "are outside the supplied data.",
        claims=[
            GroundedClaim(
                value=400000, unit="gbp", la_code="E08000003", period_label="Year ending Sep 2025",
                evidence=[EvidenceRef(result_index=0, row_index=None, field="price_gbp")],
            )
        ],
        coverage_caveats=[
            "Glasgow is outside the supplied England & Wales data (HM Land Registry price-paid data).",
            "Edinburgh is outside the supplied England & Wales data (HM Land Registry price-paid data).",
        ],
    )

    def run_agent(agent: Agent, question: str, *, max_turns: int) -> object:
        return SimpleNamespace(final_output=mixed_draft, new_items=[tool_call, tool_output])

    result = answer_question(
        EMPTY_SESSION, "Compare Glasgow, Edinburgh, and Manchester house prices",
        config=AVAILABLE_CONFIG, repository=real_repository, run_agent=run_agent,
    )
    assert result.status == "answered"
    assert "400,000" in result.answer_text
    assert len(result.coverage_caveats) == 2
    assert all("Glasgow" in c or "Edinburgh" in c for c in result.coverage_caveats)
    # No fabricated Scottish figure anywhere -- the only claim is Manchester's.
    assert len(result.claims) == 1
    assert result.claims[0].la_code == "E08000003"


def test_pure_out_of_coverage_request_is_declined_not_answered(real_repository: Repository) -> None:
    """AC2: "Analyse detached-house prices in Scotland..." -- states the
    gap, invents no Scottish patterns."""
    declined_draft = DraftAnswer(
        status="declined",
        answer_text="Scotland is not covered by this data -- HM Land Registry price-paid data "
        "(the source of both ONS releases) covers England & Wales only. I cannot analyse "
        "Scottish detached-house prices.",
        claims=[],
    )

    def run_agent(agent: Agent, question: str, *, max_turns: int) -> object:
        return SimpleNamespace(final_output=declined_draft, new_items=[])

    result = answer_question(
        EMPTY_SESSION, "Analyse detached-house prices in Scotland since 2015 and identify three notable patterns",
        config=AVAILABLE_CONFIG, repository=real_repository, run_agent=run_agent,
    )
    assert result.status == "declined"
    assert "Scotland" in result.answer_text
    assert result.claims == []
    assert result.structured_data == []


def _run_dashboard() -> AppTest:
    app = AppTest.from_file(str(REPO_ROOT / "ui" / "dashboard.py"))
    app.run(timeout=30)
    assert app.exception == []
    return app


def test_ui_renders_coverage_caveats_alongside_the_partial_answer(monkeypatch) -> None:
    import ui.ask_the_data as ask_the_data_module
    from core.models import AgentTurnResult

    monkeypatch.setattr(
        ask_the_data_module, "load_config",
        lambda: Config(openai_api_key="sk-test", openai_model="gpt-4o-mini", openai_available=True, log_level="INFO"),
    )

    def stub_answer_question(session, question):
        return AgentTurnResult(
            status="answered",
            answer_text="Manchester's price was £400,000.",
            coverage_caveats=["Glasgow is outside the supplied England & Wales data."],
        )

    monkeypatch.setattr(ask_the_data_module, "answer_question", stub_answer_question)

    app = _run_dashboard()
    app.text_input(key="ask_the_data_question").set_value("Compare Glasgow and Manchester").run(timeout=30)
    app.button(key="ask_the_data_submit").click().run(timeout=30)
    assert app.exception == []
    assert any("Glasgow is outside" in c.value for c in app.caption)


def test_ui_renders_declined_status_as_info(monkeypatch) -> None:
    import ui.ask_the_data as ask_the_data_module
    from core.models import AgentTurnResult

    monkeypatch.setattr(
        ask_the_data_module, "load_config",
        lambda: Config(openai_api_key="sk-test", openai_model="gpt-4o-mini", openai_available=True, log_level="INFO"),
    )

    def stub_answer_question(session, question):
        return AgentTurnResult(status="declined", answer_text="Scotland is not covered by this data.")

    monkeypatch.setattr(ask_the_data_module, "answer_question", stub_answer_question)

    app = _run_dashboard()
    app.text_input(key="ask_the_data_question").set_value("Analyse Scotland").run(timeout=30)
    app.button(key="ask_the_data_submit").click().run(timeout=30)
    assert app.exception == []
    assert any("Scotland is not covered" in i.value for i in app.info)
