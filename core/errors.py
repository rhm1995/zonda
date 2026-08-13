"""Typed error taxonomy for the deterministic analysis core (core/tools.py,
core/repository.py).

Per design §8.3, tool functions raise these typed errors for genuinely
invalid calls (a bad range, an unknown period) rather than generic
exceptions, so callers (UI code, and later the agent's tool wrappers) can
translate them into structured, non-throwing responses instead of letting a
bare exception surface to the user. They are distinct from *suppressed data*
(price_gbp is None, suppressed=True), which is never an error condition —
see core/metrics.py's SUPPRESSION_MESSAGE.
"""

from __future__ import annotations


class CoreError(Exception):
    """Base class for the deterministic core's typed error taxonomy."""


class InvalidRangeError(CoreError):
    """Raised when period_start is not strictly before period_end."""


class PeriodOutOfRangeError(CoreError):
    """Raised when a requested period lies outside the dataset's known range."""


class RepositoryStartupError(CoreError):
    """Raised when the DuckDB repository cannot start against a valid,
    checked processed snapshot (missing files, or a row/column-count
    mismatch against BUILD_INFO.json) — the repository must fail loudly
    here rather than start with silently empty or partial data."""
