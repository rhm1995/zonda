"""TASK-015 unit tests: `agent/observability.py`'s JSON-lines formatter,
idempotent handler configuration, and event logging -- all offline, no API
key, no network. Uses `tmp_path` throughout so no test ever touches the
real repository's own working directory (`agent/config.py`'s own stated
rationale for defaulting `Config.log_file` to `None`, not a real path)."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

import agent.observability as observability
from agent.observability import JsonLinesFormatter, configure_logging, log_event


@pytest.fixture(autouse=True)
def _reset_observability_state() -> None:
    """`configure_logging` is deliberately idempotent for real use (module
    docstring) -- each test needs a clean slate to actually exercise it,
    not observe a previous test's already-attached handler."""
    observability._configured = False
    observability.logger.handlers.clear()
    yield
    observability._configured = False
    observability.logger.handlers.clear()


def test_configure_logging_writes_valid_json_lines(tmp_path: Path) -> None:
    log_file = tmp_path / "app.jsonl"
    configure_logging(log_file, "INFO")
    log_event("tool_call", "session-1", 1, tool_name="median_price_lookup", arguments={"area": "E08000003"})

    lines = log_file.read_text().splitlines()
    assert len(lines) == 1
    parsed = json.loads(lines[0])  # raises if not valid JSON
    assert parsed["event"] == "tool_call"
    assert parsed["session_id"] == "session-1"
    assert parsed["turn_number"] == 1
    assert parsed["tool_name"] == "median_price_lookup"
    assert "timestamp" in parsed and "level" in parsed


def test_configure_logging_creates_parent_directory(tmp_path: Path) -> None:
    log_file = tmp_path / "nested" / "dir" / "app.jsonl"
    configure_logging(log_file, "INFO")
    log_event("turn_completed", "s", 1, status="answered")
    assert log_file.exists()


def test_configure_logging_is_idempotent(tmp_path: Path) -> None:
    first_file = tmp_path / "first.jsonl"
    second_file = tmp_path / "second.jsonl"
    configure_logging(first_file, "INFO")
    configure_logging(second_file, "INFO")  # must be a no-op -- same handler, same target
    log_event("turn_completed", "s", 1, status="answered")

    assert first_file.exists()
    assert not second_file.exists()
    file_handlers = [h for h in observability.logger.handlers if isinstance(h, logging.FileHandler)]
    assert len(file_handlers) == 1  # not a second one attached for second_file


def test_configure_logging_with_no_file_writes_to_a_stream_handler(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(None, "INFO")
    assert isinstance(observability.logger.handlers[0], logging.StreamHandler)
    assert not isinstance(observability.logger.handlers[0], logging.FileHandler)


def test_multiple_events_correlate_by_session_and_turn(tmp_path: Path) -> None:
    log_file = tmp_path / "app.jsonl"
    configure_logging(log_file, "INFO")
    log_event("openai_call", "session-abc", 2, model="gpt-4o-mini", total_tokens=100)
    log_event("tool_call", "session-abc", 2, tool_name="rank_areas", arguments={})
    log_event("turn_completed", "session-abc", 2, status="answered")

    events = [json.loads(line) for line in log_file.read_text().splitlines()]
    assert {e["session_id"] for e in events} == {"session-abc"}
    assert {e["turn_number"] for e in events} == {2}
    assert [e["event"] for e in events] == ["openai_call", "tool_call", "turn_completed"]


def test_log_event_never_raises_on_a_bad_field(tmp_path: Path) -> None:
    """An observability call must never be the reason a turn fails."""
    configure_logging(tmp_path / "app.jsonl", "INFO")

    class Unserializable:
        pass

    log_event("tool_call", "s", 1, weird=Unserializable())  # must not raise


def test_json_lines_formatter_handles_a_non_structured_log_record() -> None:
    """A stray plain `logger.info("text")` call on this logger (not via
    `log_event`) still produces valid JSON, not a broken line in the file."""
    formatter = JsonLinesFormatter()
    record = logging.LogRecord(
        name="x", level=logging.INFO, pathname="x", lineno=1, msg="plain message", args=(), exc_info=None
    )
    parsed = json.loads(formatter.format(record))
    assert parsed["event"] == "log"
    assert parsed["message"] == "plain message"
