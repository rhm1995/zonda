"""TASK-012 verification: exactly three correctly-labelled tabs render
without an unhandled exception (acceptance criteria 1-2), and
`ui/dashboard.py` contains no analysis/agent/OpenAI logic of its own
(acceptance criterion 3) -- a lightweight, dependency-free equivalent of an
import-linter rule, in the same spirit as `TASK-002`'s `duckdb`-boundary
check and the `TASK-008` zero-API-call rule this pattern anticipates."""

from __future__ import annotations

import ast
from pathlib import Path

from streamlit.testing.v1 import AppTest

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_exactly_three_tabs_with_expected_labels() -> None:
    app = AppTest.from_file(str(REPO_ROOT / "ui" / "dashboard.py"))
    app.run(timeout=30)
    assert app.exception == []
    assert [tab.label for tab in app.tabs] == ["Ask the data", "Explore trends", "Compare and rank"]


def test_dashboard_module_has_no_business_logic_imports() -> None:
    """`ui/dashboard.py` may only import `streamlit` and the three tab
    modules -- no `core`, `agent`, or third-party analysis/OpenAI import of
    its own (design §9: it "owns layout only")."""
    source = (REPO_ROOT / "ui" / "dashboard.py").read_text()
    tree = ast.parse(source)
    allowed_modules = {"streamlit", "ui", "__future__"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name.split(".")[0] in allowed_modules, alias.name
        elif isinstance(node, ast.ImportFrom) and node.module:
            assert node.module.split(".")[0] in allowed_modules, node.module


# Note: the static "explore_trends.py/compare_rank.py never import
# agent/openai/agents" check, and the runtime patched-client check, both
# live in tests/unit/test_zero_api_guarantee.py -- that is TASK-008's
# dedicated, explicit ownership of the zero-API-call guarantee, not
# TASK-012's (which only governs dashboard.py's own import surface, above).
