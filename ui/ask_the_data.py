""""Ask the data" tab (STORY-003, design CMP-010/011 wiring).

The only tab module permitted to import `agent` (design §9's dependency-
direction rule) -- "Explore trends"/"Compare and rank" never do (ADR-011).
`Config.openai_available` is checked here, before ever calling
`answer_question`, so an unconfigured key shows a clear message instead of
attempting (and failing) a call (`BR-003`).

**Scope note:** single-question, single-tool scope (`median_price_lookup`
only) -- comparison/trend/ranking/premium questions, follow-ups, and the
full `FR-023`/`FR-024` table/chart/expandable-detail rendering (`CMP-017`)
arrive with `STORY-004` onward (Increment 4). This story renders the
answer plus a basic expandable view of the tool result(s) backing it.
"""

from __future__ import annotations

import streamlit as st

from agent.config import load_config
from agent.orchestrator import answer_question
from core.models import AgentTurnResult, ConversationSession

EXAMPLE_PROMPT = "What was the median price of an existing detached house in Manchester in September 2025?"


def render() -> None:
    st.header("Ask the data")

    config = load_config()
    if not config.openai_available:
        st.warning(
            "This tab needs an OpenAI API key to answer questions. Set `OPENAI_API_KEY` "
            "(see README.md) and restart the app. In the meantime, **Explore trends** and "
            "**Compare and rank** both work fully with no key."
        )
        return

    st.caption("Try an example question:")
    if st.button(EXAMPLE_PROMPT, key="ask_the_data_example_button"):
        st.session_state["ask_the_data_question"] = EXAMPLE_PROMPT

    question = st.text_input(
        "Your question", key="ask_the_data_question", placeholder=EXAMPLE_PROMPT
    )
    submitted = st.button("Ask", key="ask_the_data_submit")

    if not submitted or not question.strip():
        return

    with st.spinner("Thinking..."):
        result = answer_question(ConversationSession(), question)

    _render_result(result)


def _render_result(result: AgentTurnResult) -> None:
    if result.status == "unavailable":
        st.error(result.answer_text)
        return
    if result.status == "declined":
        st.info(result.answer_text)
        return

    st.markdown(result.answer_text)

    for caveat in result.coverage_caveats:
        st.caption(f"⚠️ {caveat}")
    for assumption in result.period_assumptions:
        st.caption(f"ℹ️ {assumption}")

    if result.structured_data:
        with st.expander("Show calculation details"):
            for index, item in enumerate(result.structured_data):
                st.json(item.model_dump(mode="json"))
                if result.tool_calls and index < len(result.tool_calls):
                    call = result.tool_calls[index]
                    st.caption(f"via `{call.tool_name}({call.arguments})`")
