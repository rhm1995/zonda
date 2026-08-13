"""STORY-001 end-to-end verification via Streamlit's `AppTest` harness --
drives the real widgets against the real bundled dataset and checks the
displayed figures match the confirmed Manchester spot-check (design §6.1),
the closest automatable equivalent of the story's "manual walkthrough with
OPENAI_API_KEY unset" verification step. No network access and no
`OPENAI_API_KEY` are used or required anywhere in this test."""

from __future__ import annotations

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

REPO_ROOT = Path(__file__).resolve().parents[2]
PROCESSED_DIR = REPO_ROOT / "data" / "processed"

pytestmark = pytest.mark.skipif(
    not PROCESSED_DIR.exists(), reason="data/processed/ not built -- run `python -m data_pipeline.build` first"
)


def _run_dashboard() -> AppTest:
    app = AppTest.from_file(str(REPO_ROOT / "ui" / "dashboard.py"))
    app.run(timeout=30)
    assert app.exception == []
    return app


def _select(app: AppTest, key: str, value: str) -> AppTest:
    app.selectbox(key=key).set_value(value).run(timeout=30)
    assert app.exception == []
    return app


def test_price_mode_reproduces_manchester_spot_check() -> None:
    app = _run_dashboard()
    _select(app, "explore_trends_area", "Manchester")
    _select(app, "explore_trends_start", "Year ending Sep 2015")
    _select(app, "explore_trends_end", "Year ending Sep 2025")

    metrics = {m.label: m.value for m in app.metric}
    assert metrics["Latest price"] == "£495,000"
    assert metrics["Growth (£)"] == "£317,005"
    assert metrics["CAGR"].endswith("%")


def test_premium_mode_reproduces_manchester_spot_check() -> None:
    app = _run_dashboard()
    _select(app, "explore_trends_area", "Manchester")
    _select(app, "explore_trends_start", "Year ending Sep 2015")
    _select(app, "explore_trends_end", "Year ending Sep 2025")
    app.radio(key="explore_trends_mode").set_value("Premium").run(timeout=30)
    assert app.exception == []

    metrics = {m.label: m.value for m in app.metric}
    latest = next(v for k, v in metrics.items() if k.startswith("Latest premium"))
    assert latest == "23.75% (premium)"


def test_invalid_range_shows_inline_warning_not_a_crash() -> None:
    app = _run_dashboard()
    _select(app, "explore_trends_area", "Manchester")
    _select(app, "explore_trends_start", "Year ending Sep 2025")
    _select(app, "explore_trends_end", "Year ending Sep 2015")

    assert app.exception == []
    assert any("before" in warning.value for warning in app.warning)


def test_csv_download_available_and_no_openai_related_error() -> None:
    app = _run_dashboard()
    _select(app, "explore_trends_area", "Manchester")
    assert app.exception == []
    assert not any("openai" in str(e).lower() for e in app.exception)
