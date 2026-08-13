"""STORY-003 UI tests: `ui/ask_the_data.py`, driven via Streamlit's
`AppTest` harness. The "no key" path exercises the real `load_config()`
(no `OPENAI_API_KEY` is set anywhere in this test run); the "available"
path monkeypatches `load_config`/`answer_question` at the point
`ui.ask_the_data` imported them, so no real API call is ever made here
either -- `answer_question`'s own behaviour is already covered by
`test_orchestrator.py`'s stubbed-runner tests; this file only proves the
UI wiring around it."""

from __future__ import annotations

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

import ui.ask_the_data as ask_the_data_module
from agent.config import Config
from core.models import AgentTurnResult

REPO_ROOT = Path(__file__).resolve().parents[2]

UNAVAILABLE_CONFIG = Config(openai_api_key=None, openai_model="gpt-4o-mini", openai_available=False, log_level="INFO")
AVAILABLE_CONFIG = Config(openai_api_key="sk-test", openai_model="gpt-4o-mini", openai_available=True, log_level="INFO")


def _run_dashboard() -> AppTest:
    app = AppTest.from_file(str(REPO_ROOT / "ui" / "dashboard.py"))
    app.run(timeout=30)
    assert app.exception == []
    return app


def test_no_key_shows_clear_unavailable_message_not_a_crash(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ask_the_data_module, "load_config", lambda: UNAVAILABLE_CONFIG)
    app = _run_dashboard()
    assert app.exception == []
    assert any("OpenAI API key" in w.value for w in app.warning)
    # No question input/submit button should even be offered.
    assert not app.text_input
    assert not app.button


def test_example_prompt_is_visible_when_available(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ask_the_data_module, "load_config", lambda: AVAILABLE_CONFIG)
    app = _run_dashboard()
    assert app.exception == []
    button_labels = [b.label for b in app.button]
    assert ask_the_data_module.EXAMPLE_PROMPT in button_labels


def test_clicking_example_prompt_populates_the_question_field(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ask_the_data_module, "load_config", lambda: AVAILABLE_CONFIG)
    app = _run_dashboard()
    app.button(key="ask_the_data_example_button").click().run(timeout=30)
    assert app.exception == []
    assert app.text_input(key="ask_the_data_question").value == ask_the_data_module.EXAMPLE_PROMPT


def test_submitting_a_question_renders_the_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ask_the_data_module, "load_config", lambda: AVAILABLE_CONFIG)

    def stub_answer_question(session, question):
        return AgentTurnResult(status="answered", answer_text=f"Stubbed answer for: {question}")

    monkeypatch.setattr(ask_the_data_module, "answer_question", stub_answer_question)

    app = _run_dashboard()
    app.text_input(key="ask_the_data_question").set_value("What was the price in Manchester?").run(timeout=30)
    app.button(key="ask_the_data_submit").click().run(timeout=30)
    assert app.exception == []
    assert any("Stubbed answer for" in m.value for m in app.markdown)


def test_unavailable_status_from_answer_question_renders_as_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ask_the_data_module, "load_config", lambda: AVAILABLE_CONFIG)

    def stub_unavailable(session, question):
        return AgentTurnResult(status="unavailable", answer_text="The assistant is currently unavailable.")

    monkeypatch.setattr(ask_the_data_module, "answer_question", stub_unavailable)

    app = _run_dashboard()
    app.text_input(key="ask_the_data_question").set_value("anything").run(timeout=30)
    app.button(key="ask_the_data_submit").click().run(timeout=30)
    assert app.exception == []
    assert any("unavailable" in e.value for e in app.error)
