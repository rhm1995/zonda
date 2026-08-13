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
#: and CON-002 compliance (below "GPT-5.5", non-"Pro"). SPIKE-001 has not
#: yet run (it is not on Increment 1's critical path -- backlog §5,
#: Increment 3 entry criteria); this is a documented placeholder pending
#: that confirmation, per this task's own stated open question. Update this
#: constant, and only this constant, once SPIKE-001 reports its finding.
TESTED_DEFAULT_MODEL = "gpt-4o-mini"  # placeholder pending SPIKE-001 (see docstring)


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


def load_config(
    env_file: Path | None = None,
    *,
    model_checker: Callable[[str, str], None] = _verify_model_available,
) -> Config:
    """Loads `OPENAI_API_KEY`/`OPENAI_MODEL`/`LOG_LEVEL` from the
    environment (and `.env` if present), resolves the model to use, and
    fails fast on an explicitly-overridden model that doesn't work.

    The tested default is not re-verified against the API on every start --
    that would spend API credits on every single startup for no
    correctness benefit (NFR-006) -- only an explicit `OPENAI_MODEL`
    override, which SPIKE-001 has never seen, is checked here.
    """
    load_dotenv(dotenv_path=env_file, override=False)

    api_key = os.environ.get("OPENAI_API_KEY") or None
    model_override = os.environ.get("OPENAI_MODEL") or None
    log_level = os.environ.get("LOG_LEVEL", "INFO")
    logging.getLogger().setLevel(log_level)

    if not api_key:
        # NFR-011: a missing key is non-fatal -- startup must still succeed.
        return Config(
            openai_api_key=None,
            openai_model=model_override or TESTED_DEFAULT_MODEL,
            openai_available=False,
            log_level=log_level,
        )

    resolved_model = model_override or TESTED_DEFAULT_MODEL
    if model_override:
        model_checker(model_override, api_key)  # raises ConfigError, no fallback

    return Config(
        openai_api_key=api_key,
        openai_model=resolved_model,
        openai_available=True,
        log_level=log_level,
    )
