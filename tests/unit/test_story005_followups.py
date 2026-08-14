"""STORY-005 integration tests: `answer_question` threads `ConversationSession`
into the model call and updates it after each turn, so a follow-up like
"those areas" resolves against the previous turn's results. Stubbed-model
tests per the design's testing philosophy (§13); `agent/session.py`'s own
extraction logic is unit-tested directly in `test_session.py`."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from agents import Agent, ToolCallItem, ToolCallOutputItem
from streamlit.testing.v1 import AppTest

from agent.config import Config
from agent.orchestrator import answer_question
from core.models import (
    AgentTurnResult,
    ConversationSession,
    DraftAnswer,
    EvidenceRef,
    GroundedClaim,
    RankedArea,
    RankingCoverageSummary,
    RankingResult,
    RecentMessage,
)
from core.repository import Repository

REPO_ROOT = Path(__file__).resolve().parents[2]
AVAILABLE_CONFIG = Config(
    openai_api_key="sk-test", openai_model="gpt-4o-mini", openai_available=True, log_level="INFO"
)
_STUB_AGENT = Agent(name="stub", instructions="stub")


def _ranking_run_result() -> SimpleNamespace:
    ranking = RankingResult(
        metric="premium_percentage_point_change",
        period_label_or_range="Year ending Sep 2015 to Year ending Sep 2025",
        direction="top",
        rows=[
            RankedArea(rank=1, la_code="E08000003", la_name="Manchester", value=42.8, suppressed=False),
            RankedArea(rank=2, la_code="E07000102", la_name="Three Rivers", value=39.0, suppressed=False),
        ],
        coverage=RankingCoverageSummary(areas_in_scope=318, areas_ranked=2, areas_excluded=316),
    )
    tool_call = ToolCallItem(
        agent=_STUB_AGENT,
        raw_item={"type": "function_call", "name": "rank_areas", "call_id": "call_1", "arguments": "{}"},
    )
    tool_output = ToolCallOutputItem(
        agent=_STUB_AGENT, raw_item={"type": "function_call_output", "call_id": "call_1", "output": "{}"},
        output={"status": "ok", **ranking.model_dump(mode="json")},
    )
    draft = DraftAnswer(
        answer_text="Manchester and Three Rivers saw the largest premium increases.",
        claims=[
            GroundedClaim(value=42.8, unit="pct_point", la_code="E08000003", evidence=[EvidenceRef(result_index=0, row_index=0, field="value")]),
            GroundedClaim(value=39.0, unit="pct_point", la_code="E07000102", evidence=[EvidenceRef(result_index=0, row_index=1, field="value")]),
        ],
    )
    return SimpleNamespace(final_output=draft, new_items=[tool_call, tool_output])


def test_first_turn_input_is_still_a_plain_string(real_repository: Repository) -> None:
    """No regression for STORY-003's original single-question path."""
    captured: list[object] = []

    def run_agent(agent, agent_input, *, max_turns):
        captured.append(agent_input)
        return _ranking_run_result()

    answer_question(
        ConversationSession(), "Which areas saw the largest premium increase?",
        config=AVAILABLE_CONFIG, repository=real_repository, run_agent=run_agent,
    )
    assert captured[0] == "Which areas saw the largest premium increase?"


def test_session_is_updated_in_place_after_a_successful_turn(real_repository: Repository) -> None:
    session = ConversationSession()

    def run_agent(agent, agent_input, *, max_turns):
        return _ranking_run_result()

    answer_question(
        session, "Which areas saw the largest premium increase?",
        config=AVAILABLE_CONFIG, repository=real_repository, run_agent=run_agent,
    )
    # The caller's original object reflects the update -- no second return value needed.
    assert session.last_area_codes == ["E08000003", "E07000102"]
    assert session.last_metric == "premium_percentage_point_change"
    assert len(session.recent_messages) == 2


def test_follow_up_turn_carries_recap_and_verbatim_history(real_repository: Repository) -> None:
    """AC1 (recap resolves "those areas" to concrete facts) and AC4 (the
    verbatim prior exchange is available for phrasing the model itself
    must resolve, e.g. a pronoun) both land in the same input."""
    session = ConversationSession()

    def first_run_agent(agent, agent_input, *, max_turns):
        return _ranking_run_result()

    answer_question(
        session, "Which areas saw the largest premium increase?",
        config=AVAILABLE_CONFIG, repository=real_repository, run_agent=first_run_agent,
    )
    assert session.last_area_codes == ["E08000003", "E07000102"]

    captured: list[object] = []

    def second_run_agent(agent, agent_input, *, max_turns):
        captured.append(agent_input)
        return _ranking_run_result()

    answer_question(
        session, "Which of those areas changed the most in the last five years?",
        config=AVAILABLE_CONFIG, repository=real_repository, run_agent=second_run_agent,
    )
    agent_input = captured[0]
    assert isinstance(agent_input, list)
    # Verbatim history from the first turn is present (AC4).
    joined = " ".join(item["content"] for item in agent_input)
    assert "Manchester and Three Rivers saw the largest premium increases." in joined
    # The structured recap resolves "those areas" to concrete facts (AC1).
    assert "E08000003" in joined and "E07000102" in joined
    assert "premium_percentage_point_change" in joined
    # The new question itself is still present, in the final message.
    assert agent_input[-1]["role"] == "user"
    assert "Which of those areas changed the most" in agent_input[-1]["content"]


def test_follow_up_with_no_relevant_prior_context_has_no_recap(real_repository: Repository) -> None:
    """AC2: a fresh session (right after app start, or a prior turn that
    resolved nothing) has no structured facts to recap -- the model is
    instructed (SYSTEM_INSTRUCTIONS) to say so rather than guess."""
    from agent.orchestrator import _build_agent_input

    fresh_session = ConversationSession()
    assert _build_agent_input(fresh_session, "Which of those areas changed the most?") == (
        "Which of those areas changed the most?"
    )  # no recent_messages at all -- a plain string, same as any first turn


def test_clarification_turn_still_updates_recent_messages_but_not_last_fields(
    real_repository: Repository,
) -> None:
    session = ConversationSession()

    def run_agent(agent, agent_input, *, max_turns):
        from types import SimpleNamespace as NS

        return NS(final_output=DraftAnswer(status="clarification_needed", answer_text="Did you mean X or Y?"), new_items=[])

    answer_question(
        session, "House prices in Richmond",
        config=AVAILABLE_CONFIG, repository=real_repository, run_agent=run_agent,
    )
    assert len(session.recent_messages) == 2
    assert session.last_area_codes == []


def _run_dashboard() -> AppTest:
    app = AppTest.from_file(str(REPO_ROOT / "ui" / "dashboard.py"))
    app.run(timeout=30)
    assert app.exception == []
    return app


def test_ui_session_persists_across_submissions_within_one_browser_session(monkeypatch) -> None:
    """The real gap this story closes: STORY-003's UI created a *fresh*
    ConversationSession() on every submit, so a follow-up could never
    actually work end to end even though the orchestrator supported it."""
    import ui.ask_the_data as ask_the_data_module

    monkeypatch.setattr(ask_the_data_module, "load_config", lambda: AVAILABLE_CONFIG)
    seen_sessions: list[int] = []

    def stub_answer_question(session, question):
        seen_sessions.append(id(session))
        session.last_area_codes = ["E08000003"]
        session.recent_messages = [
            *session.recent_messages,
            RecentMessage(role="user", text=question),
            RecentMessage(role="assistant", text="answer"),
        ]
        return AgentTurnResult(status="answered", answer_text=f"Answer to: {question}")

    monkeypatch.setattr(ask_the_data_module, "answer_question", stub_answer_question)

    app = _run_dashboard()
    app.text_input(key="ask_the_data_question").set_value("Q1").run(timeout=30)
    app.button(key="ask_the_data_submit").click().run(timeout=30)
    app.text_input(key="ask_the_data_question").set_value("Q2").run(timeout=30)
    app.button(key="ask_the_data_submit").click().run(timeout=30)

    assert app.exception == []
    assert len(seen_sessions) == 2
    assert seen_sessions[0] == seen_sessions[1]  # the same session object both times


def test_ui_new_conversation_button_resets_the_session(monkeypatch) -> None:
    import ui.ask_the_data as ask_the_data_module

    monkeypatch.setattr(ask_the_data_module, "load_config", lambda: AVAILABLE_CONFIG)

    def stub_answer_question(session, question):
        session.last_area_codes = ["E08000003"]
        session.recent_messages = [
            *session.recent_messages,
            RecentMessage(role="user", text=question),
            RecentMessage(role="assistant", text="answer"),
        ]
        return AgentTurnResult(status="answered", answer_text="answer")

    monkeypatch.setattr(ask_the_data_module, "answer_question", stub_answer_question)

    app = _run_dashboard()
    assert not any(b.label == "New conversation" for b in app.button)  # not shown with no history yet
    app.text_input(key="ask_the_data_question").set_value("Q1").run(timeout=30)
    app.button(key="ask_the_data_submit").click().run(timeout=30)
    # The button's visibility is decided at the top of the script, before
    # that same run's answer_question call mutates the session -- it only
    # appears on the *next* script execution, same as any Streamlit widget
    # reacting to state a previous run just changed.
    app.run(timeout=30)
    assert any(b.label == "New conversation" for b in app.button)

    app.button(key="ask_the_data_reset").click().run(timeout=30)
    assert st_state_session_is_empty(app)


def st_state_session_is_empty(app: AppTest) -> bool:
    session = app.session_state["ask_the_data_session"]
    return session.recent_messages == [] and session.last_area_codes == []
