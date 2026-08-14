""""Ask the data" tab (`STORY-003`/`STORY-004`, design `CMP-010`/`011` wiring).

The only tab module permitted to import `agent` (design §9's dependency-
direction rule) -- "Explore trends"/"Compare and rank" never do (ADR-011).
`Config.openai_available` is checked here, before ever calling
`answer_question`, so an unconfigured key shows a clear message instead of
attempting (and failing) a call (`BR-003`).

**(STORY-004)** Renders the turn's `structured_data` as a table (and, when
`chart_spec` validates, a chart) via `TASK-018`'s `render_table`/
`render_chart` (`CMP-017`) -- the same objects already verified by the
grounding guardrail, never a second query or recomputation (`FR-023`). An
expandable section shows the underlying tool call(s), arguments, and any
`period_assumptions` (`FR-024`).

**(STORY-005)** One `ConversationSession` is created per Streamlit session
(`st.session_state`, never persisted across an app restart -- `AMB-006`)
and reused across submissions, so a follow-up question resolves against
the previous turn's results. `answer_question` updates it in place; a
"New conversation" button lets the user deliberately clear it.

**(TASK-015)** `configure_logging` is called here, not `ui/dashboard.py`
(which may only import `streamlit`/`ui.*`, design §9) -- the observability
setup belongs on the same boundary as the only module permitted to import
`agent` at all. Safe to call on every Streamlit script rerun: idempotent
after the first call (`agent/observability.py`'s own docstring).
"""

from __future__ import annotations

import streamlit as st

from agent.config import load_config
from agent.observability import configure_logging
from agent.orchestrator import answer_question
from core.models import AgentTurnResult, ConversationSession
from ui.charts import render_chart, render_table

EXAMPLE_PROMPT = "What was the median price of an existing detached house in Manchester in September 2025?"

_SESSION_STATE_KEY = "ask_the_data_session"


def _get_session() -> ConversationSession:
    if _SESSION_STATE_KEY not in st.session_state:
        st.session_state[_SESSION_STATE_KEY] = ConversationSession()
    session = st.session_state[_SESSION_STATE_KEY]
    assert isinstance(session, ConversationSession)
    return session


def render() -> None:
    st.header("Ask the data")

    config = load_config()
    configure_logging(config.log_file, config.log_level)
    if not config.openai_available:
        st.warning(
            "This tab needs an OpenAI API key to answer questions. Set `OPENAI_API_KEY` "
            "(see README.md) and restart the app. In the meantime, **Explore trends** and "
            "**Compare and rank** both work fully with no key."
        )
        return

    session = _get_session()

    header_col, reset_col = st.columns([5, 1])
    with header_col:
        st.caption("Try an example question:")
    with reset_col:
        if session.recent_messages and st.button("New conversation", key="ask_the_data_reset"):
            st.session_state[_SESSION_STATE_KEY] = ConversationSession()
            session = st.session_state[_SESSION_STATE_KEY]

    if st.button(EXAMPLE_PROMPT, key="ask_the_data_example_button"):
        st.session_state["ask_the_data_question"] = EXAMPLE_PROMPT

    question = st.text_input(
        "Your question", key="ask_the_data_question", placeholder=EXAMPLE_PROMPT
    )
    submitted = st.button("Ask", key="ask_the_data_submit")

    if not submitted or not question.strip():
        return

    with st.spinner("Thinking..."):
        result = answer_question(session, question)

    _render_result(result)


def _render_result(result: AgentTurnResult) -> None:
    """(`STORY-008`/`STORY-007`) `clarification_needed`/`declined` are
    rendered plainly, with no expandable detail section -- neither carries
    a grounded figure to show calculation details for."""
    if result.status == "unavailable":
        st.error(result.answer_text)
        return
    if result.status == "clarification_needed":
        st.info(f"❓ {result.answer_text}")
        return
    if result.status == "declined":
        st.info(result.answer_text)
        return

    st.markdown(result.answer_text)

    for caveat in result.coverage_caveats:
        st.caption(f"⚠️ {caveat}")

    if result.chart_spec is not None and result.structured_data:
        figure = render_chart(result.structured_data, result.chart_spec)
        if figure is not None:
            st.plotly_chart(figure, width="stretch")

    if result.structured_data:
        with st.expander("Show calculation details", expanded=False):
            for assumption in result.period_assumptions:
                st.caption(f"ℹ️ {assumption}")
            for index, item in enumerate(result.structured_data):
                st.markdown(f"**{type(item).__name__}**")
                if index < len(result.tool_calls):
                    call = result.tool_calls[index]
                    st.caption(f"via `{call.tool_name}({call.arguments})`")
                rows = render_table(item)
                if rows:
                    st.dataframe(rows, width="stretch")
