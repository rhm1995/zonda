"""Structured (JSON-lines) observability logging (`TASK-015`, design §12).

One JSON object per line, one line per event, written through a dedicated
logger (`"housing_market_insights"`) so application code never needs its
own `print`/ad-hoc logging statements for this. Every event carries
`session_id`/`turn_number` (design's own stated correlation key) so a
reviewer can `grep`/filter one turn's complete trace -- every tool call,
every OpenAI call, and any guardrail trigger -- out of the log file.

**What is never logged**: the OpenAI API key or any other credential (this
module's callers never have access to one -- `agent/config.py`'s `Config`
is never passed here); more of the raw user question than a short,
explicitly bounded excerpt (`_MAX_TEXT_LENGTH`); a full `answer_text`
(same bound, when logged at all). `log_event` itself has no awareness of
*which* fields are sensitive -- callers are responsible for only passing
safe fields (`agent/orchestrator.py`'s call sites are the enforcement
point, covered by `tests/unit/test_observability.py`'s secret-scan test).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

LOGGER_NAME = "housing_market_insights"
logger = logging.getLogger(LOGGER_NAME)

#: A tool argument or question excerpt longer than this is truncated before
#: logging -- "no more raw user question text than needed for debugging"
#: (design §12) is read as "enough to identify the turn, not the full text."
_MAX_TEXT_LENGTH = 200

_configured = False


class JsonLinesFormatter(logging.Formatter):
    """Renders each `LogRecord` as one JSON object -- `record.event` (a
    plain dict, attached via `logging.Logger.info(..., extra={"event": ...})`)
    if present, else a minimal fallback so a stray non-structured log call
    on this logger still produces valid JSON rather than breaking the
    file's one-JSON-object-per-line contract."""

    def format(self, record: logging.LogRecord) -> str:
        event = getattr(record, "event", None)
        if isinstance(event, dict):
            payload = event
        else:
            payload = {"event": "log", "message": record.getMessage()}
        payload = {"timestamp": datetime.now(timezone.utc).isoformat(), "level": record.levelname, **payload}
        return json.dumps(payload, default=str)


def configure_logging(log_file: Path | None, level: str = "INFO") -> None:
    """Attaches one JSON-lines handler to this module's logger -- a
    `FileHandler` at `log_file` (parent directory created if needed), or a
    `StreamHandler` (stderr) if `log_file` is `None`. Idempotent: real
    entry points (`ui/dashboard.py`, `eval/run_eval.py`) call this once at
    startup; calling it again (e.g. Streamlit's own script-rerun model) is
    a safe no-op rather than a duplicate/leaked handler. Tests that never
    call this get no file/stream side effect at all -- `agent/config.py`'s
    `load_config` deliberately does not call this itself (see its own
    docstring), so building a `Config` in a test never touches the
    filesystem or global logging state."""
    global _configured
    if _configured:
        return
    handler: logging.Handler
    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(log_file)
    else:
        handler = logging.StreamHandler()
    handler.setFormatter(JsonLinesFormatter())
    logger.addHandler(handler)
    logger.setLevel(level)
    logger.propagate = False  # JSON-lines output only -- never duplicated onto the root logger's own handler
    _configured = True


def _truncate(text: str | None) -> str | None:
    if text is None:
        return None
    return text if len(text) <= _MAX_TEXT_LENGTH else text[:_MAX_TEXT_LENGTH] + "…"


def log_event(event_type: str, session_id: str, turn_number: int, **fields: Any) -> None:
    """The one function every call site uses -- `event_type` names what
    happened (`"tool_call"`, `"openai_call"`, `"guardrail_trigger"`,
    `"turn_completed"`); `fields` is whatever that event type's own
    call site (`agent/orchestrator.py`) provides. Never raises -- an
    observability call must never be the reason a turn fails."""
    try:
        logger.info(
            "",
            extra={"event": {"event": event_type, "session_id": session_id, "turn_number": turn_number, **fields}},
        )
    except Exception:  # logging must never break the turn it's observing
        logger.warning("Failed to log event %r", event_type, exc_info=True)
