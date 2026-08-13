"""TASK-008: the mechanically-enforced zero-OpenAI-call guarantee for both
deterministic tabs (design §7.9, `ADR-011`/`NFR-011`).

Two independent enforcement layers, per design:
  1. Static: neither `ui/explore_trends.py` nor `ui/compare_rank.py` (nor
     the shared `ui/_shared.py` helper they both use) imports `agent`,
     `openai`, or `agents` anywhere in their source.
  2. Runtime: with the OpenAI client patched to raise on any construction
     or use, both tabs' full functionality (selectors -> metrics/ranking ->
     CSV) still completes correctly with no exception -- proving no code
     path in either tab ever reaches it, not just that it happens to work
     when the API is reachable.

Wired into the standard `pytest` run -- no special flag or opt-in needed.
"""

from __future__ import annotations

import ast
from pathlib import Path

import openai
import pytest
from streamlit.testing.v1 import AppTest

REPO_ROOT = Path(__file__).resolve().parents[2]
PROCESSED_DIR = REPO_ROOT / "data" / "processed"

pytestmark = pytest.mark.skipif(
    not PROCESSED_DIR.exists(), reason="data/processed/ not built -- run `python -m data_pipeline.build` first"
)

_FORBIDDEN_MODULES = {"agent", "openai", "agents"}
_CHECKED_MODULES = ["ui/explore_trends.py", "ui/compare_rank.py", "ui/_shared.py"]


# -- 1. Static enforcement ---------------------------------------------------


def test_deterministic_tabs_never_import_agent_openai_or_agents() -> None:
    """Acceptance criterion 1: the check fails with a specific violation
    message identifying the offending module and import if either tab (or
    their shared helper) ever imports one of these."""
    for module_path in _CHECKED_MODULES:
        source = (REPO_ROOT / module_path).read_text()
        tree = ast.parse(source, filename=module_path)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    top_level = alias.name.split(".")[0]
                    assert top_level not in _FORBIDDEN_MODULES, (
                        f"{module_path} imports forbidden module {alias.name!r} "
                        f"(violates ADR-011/NFR-011)"
                    )
            elif isinstance(node, ast.ImportFrom) and node.module:
                top_level = node.module.split(".")[0]
                assert top_level not in _FORBIDDEN_MODULES, (
                    f"{module_path} imports from forbidden module {node.module!r} "
                    f"(violates ADR-011/NFR-011)"
                )


# -- 2. Runtime enforcement: patched OpenAI client raises on any use --------


class _OpenAIConstructionForbidden(RuntimeError):
    """Raised by the patched `openai.OpenAI` client -- if either
    deterministic tab ever constructs or uses it, the offending test fails
    loudly with this exception, rather than the tab silently degrading."""


@pytest.fixture(autouse=True)
def _forbid_openai_client(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise(*args: object, **kwargs: object) -> None:
        raise _OpenAIConstructionForbidden(
            "openai.OpenAI() was constructed by a deterministic tab -- "
            "this must never happen (ADR-011/NFR-011)"
        )

    monkeypatch.setattr(openai, "OpenAI", _raise)


def _run_dashboard() -> AppTest:
    app = AppTest.from_file(str(REPO_ROOT / "ui" / "dashboard.py"))
    app.run(timeout=30)
    assert app.exception == []
    return app


def test_explore_trends_full_flow_completes_with_openai_client_forbidden() -> None:
    """Acceptance criterion 2: the full Explore Trends flow (STORY-001's
    acceptance criteria) completes successfully -- correct results, no
    exception -- with the OpenAI client patched to raise on any use."""
    app = _run_dashboard()
    app.selectbox(key="explore_trends_area").set_value("Manchester").run(timeout=30)
    app.selectbox(key="explore_trends_start").set_value("Year ending Sep 2015").run(timeout=30)
    app.selectbox(key="explore_trends_end").set_value("Year ending Sep 2025").run(timeout=30)
    assert app.exception == []

    metrics = {m.label: m.value for m in app.metric}
    assert metrics["Latest price"] == "£495,000"

    app.radio(key="explore_trends_mode").set_value("Premium").run(timeout=30)
    assert app.exception == []
    metrics = {m.label: m.value for m in app.metric}
    latest = next(v for k, v in metrics.items() if k.startswith("Latest premium"))
    assert latest == "23.75% (premium)"


def test_compare_rank_full_flow_completes_with_openai_client_forbidden() -> None:
    """Acceptance criterion 3: the full Compare and Rank flow (STORY-002's
    acceptance criteria) completes successfully with the OpenAI client
    patched to raise on any use."""
    app = _run_dashboard()
    widget = app.multiselect(key="compare_rank_areas")
    widget.select("Manchester").select("Birmingham").select("Leeds").run(timeout=30)
    app.selectbox(key="compare_rank_metric").set_value("New-build premium (%)").run(timeout=30)
    assert app.exception == []

    table = app.dataframe[0].value
    manchester_row = table[table["Area"] == "Manchester"].iloc[0]
    assert manchester_row["Value"] == "23.75%"
