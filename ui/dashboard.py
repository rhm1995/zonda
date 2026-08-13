"""Three-tab dashboard shell (TASK-012, design CMP-010, IR-004/CON-006).

Owns layout only -- page config and tab wiring -- and delegates each tab's
content to its own module. Contains no analysis, agent, or OpenAI-related
logic itself (verified by `tests/unit/test_dashboard_shell.py`'s static
check, mirroring `TASK-008`'s import-linter pattern for a different
boundary). Run with:

    streamlit run ui/dashboard.py
"""

from __future__ import annotations

import streamlit as st

from ui import ask_the_data, compare_rank, explore_trends


def main() -> None:
    st.set_page_config(page_title="Housing Market Insights Agent", layout="wide")
    st.title("Housing Market Insights Agent")

    ask_tab, explore_tab, compare_tab = st.tabs(
        ["Ask the data", "Explore trends", "Compare and rank"]
    )
    with ask_tab:
        ask_the_data.render()
    with explore_tab:
        explore_trends.render()
    with compare_tab:
        compare_rank.render()


if __name__ == "__main__":
    main()
