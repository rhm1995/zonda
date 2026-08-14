"""Startup-time configuration and secrets loading (TASK-013, design CMP-012,
`ADR-007` v4).

Only this module reads environment variables (design §9's configuration
boundary) -- every other module receives an already-validated `Config`
object. A missing `OPENAI_API_KEY` is a non-fatal condition the app must
still start under cleanly (`NFR-011`/`BR-003`): "Explore trends" and
"Compare and rank" never touch this module's `openai_available` flag at
all, and "Ask the data" (Increment 3+) uses it to show a clear unavailable
state instead of crashing.

Per the stakeholder's `ADR-007` (v4) directive, model validity is never
checked by pattern-matching the model name against a deny-list of
disallowed substrings -- it is established by attempting to use the
resolved model and failing loudly, specifically, and without a silent
fallback on any error.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

from dotenv import load_dotenv

#: Sourced from SPIKE-001 (ADR-007, v4): the one tested default OpenAI
#: model, confirmed for key access, function calling, structured outputs,
#: and CON-002 compliance (below "GPT-5.5", non-"Pro"). SPIKE-001 ran
#: 2026-08-13 against a live key: key access confirmed (models.retrieve),
#: function calling confirmed (real median_price_lookup call with correctly
#: extracted arguments), structured outputs confirmed (valid DraftAnswer
#: returned, including a properly evidenced GroundedClaim), and CON-002
#: compliance holds by inspection (gpt-4o-mini is neither "GPT-5.5"-or-above
#: nor "Pro" tier). The spike also surfaced and fixed two real bugs (see
#: agent/agent_definition.py's _MONTH_ALIASES and
#: agent/orchestrator.py's _reconstruct_structured_result) that only
#: manifested against the real API, not the stubbed test suite.
TESTED_DEFAULT_MODEL = "gpt-4o-mini"


class ConfigError(Exception):
    """Raised when configuration is present but invalid -- e.g. an
    `OPENAI_MODEL` override that is unavailable or inaccessible under the
    supplied key (FR-018). Never raised merely because `OPENAI_API_KEY` is
    absent (NFR-011) -- that is a normal, supported startup state."""


class ModelChecker(Protocol):
    """The fail-fast availability check's shape -- injected so
    `load_config` never needs a real network call in tests (Dependency
    Inversion; see `tests/unit/test_config.py`'s mocked-failure cases)."""

    def __call__(self, model: str, api_key: str) -> None: ...


def _verify_model_available(model: str, api_key: str) -> None:
    """Attempts a lightweight call using the resolved model. Raises
    `ConfigError`, naming the offending model, on any failure -- no
    substring/name-pattern matching against known-disallowed values."""
    import openai

    try:
        client = openai.OpenAI(api_key=api_key)
        client.models.retrieve(model)
    except Exception as exc:  # translate any provider error into one ConfigError
        raise ConfigError(
            f"OPENAI_MODEL={model!r} is unavailable or inaccessible under the "
            f"supplied OPENAI_API_KEY: {exc}"
        ) from exc


@dataclass(frozen=True)
class Config:
    """Immutable, already-validated configuration handed to every other
    module that needs it -- no module besides this one reads `os.environ`."""

    openai_api_key: str | None
    openai_model: str
    openai_available: bool
    log_level: str
    #: (TASK-015) `None` (the default, when `LOG_FILE` is unset) means
    #: `agent.observability.configure_logging` attaches a stderr handler,
    #: never a file -- deliberately: this repository's working directory
    #: must never gain a file neither `load_config` nor any test asked for
    #: (defaulting to a real file here would create one on every test run
    #: that happens to build a `Config`, `tests/unit/test_config.py` alone
    #: six times over). A reviewer who wants a persistent log file sets
    #: `LOG_FILE` explicitly (documented in `README.md`/`.env.example`).
    log_file: Path | None = None


def load_config(
    env_file: Path | None = None,
    *,
    model_checker: Callable[[str, str], None] = _verify_model_available,
) -> Config:
    """Loads `OPENAI_API_KEY`/`OPENAI_MODEL`/`LOG_LEVEL`/`LOG_FILE` from the
    environment (and `.env` if present), resolves the model to use, and
    fails fast on an explicitly-overridden model that doesn't work.

    The tested default is not re-verified against the API on every start --
    that would spend API credits on every single startup for no
    correctness benefit (NFR-006) -- only an explicit `OPENAI_MODEL`
    override, which SPIKE-001 has never seen, is checked here.

    **(TASK-015)** Deliberately does *not* call `agent.observability.
    configure_logging` itself -- doing so here would attach a real file/
    stream handler on every one of this module's many test call sites
    (`tests/unit/test_config.py` alone calls `load_config` six times),
    which would either pollute the repository's working directory with a
    real log file during every test run or require every such test to know
    about and disable logging explicitly. Attaching the handler is instead
    each real entry point's own explicit, one-time startup responsibility
    (`ui/dashboard.py`, `eval/run_eval.py`) -- `load_config` only resolves
    *what* `log_file`/`log_level` should be, never attaches anything itself.
    """
    load_dotenv(dotenv_path=env_file, override=False)

    api_key = os.environ.get("OPENAI_API_KEY") or None
    model_override = os.environ.get("OPENAI_MODEL") or None
    log_level = os.environ.get("LOG_LEVEL", "INFO")
    log_file_override = os.environ.get("LOG_FILE") or None
    log_file = Path(log_file_override) if log_file_override else None
    logging.getLogger().setLevel(log_level)

    if not api_key:
        # NFR-011: a missing key is non-fatal -- startup must still succeed.
        return Config(
            openai_api_key=None,
            openai_model=model_override or TESTED_DEFAULT_MODEL,
            openai_available=False,
            log_level=log_level,
            log_file=log_file,
        )

    resolved_model = model_override or TESTED_DEFAULT_MODEL
    if model_override:
        model_checker(model_override, api_key)  # raises ConfigError, no fallback

    return Config(
        openai_api_key=api_key,
        openai_model=resolved_model,
        openai_available=True,
        log_level=log_level,
        log_file=log_file,
    )
