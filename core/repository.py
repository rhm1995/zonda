"""DuckDB-backed repository (TASK-002, design §6.7/§8.6, `ADR-005` v3).

Opens a single embedded, in-process DuckDB connection over the bundled
Parquet snapshot and exposes a small, fixed set of parameterised methods.
This is the *only* module in the codebase that imports `duckdb` or ever
holds a live connection/cursor — every other module reads through the
methods below, never re-parses Excel, and never sees a raw `DataFrame` or
DuckDB object (CON-008/CON-009).

Every method's SQL text is a fixed literal, reviewed once; only bound
parameter values vary at call time (defends THR-007 structurally, not by
runtime filtering) -- see `tests/unit/test_repository.py`'s static check.

**Thread safety (found via TASK-009's live-API testing, Increment 4):** the
OpenAI Agents SDK executes multiple tool calls from the same turn
concurrently, on separate threads. A single `duckdb.DuckDBPyConnection` is
not safe for concurrent `execute()` calls from multiple threads -- confirmed
directly: concurrent queries against the *same* known value intermittently
returned empty results (a real query silently, non-deterministically
returning no rows, not merely a slower response). Every query in this
module is therefore serialised through one `threading.Lock` -- correct and
simple to reason about at this dataset's volume/query latency (sub-
millisecond DuckDB queries over ~76k cells), where lock contention is not a
performance concern; a connection-per-thread pattern would remove the lock
but adds real complexity for no measured benefit here.
"""

from __future__ import annotations

import json
import threading
from datetime import date
from pathlib import Path
from typing import Literal

import duckdb

from core.errors import RepositoryStartupError
from core.models import LocalAuthority, Period, PremiumRow, PricePoint


class Repository:
    """Owns the DuckDB connection and every parameterised query the rest of
    the system reads through. Construct once at app startup (`Repository.open`)
    and reuse for the process lifetime. Safe for concurrent use from
    multiple threads (queries are serialised via an internal lock -- see
    module docstring)."""

    def __init__(self, connection: duckdb.DuckDBPyConnection) -> None:
        self._connection = connection
        self._lock = threading.Lock()

    @classmethod
    def open(cls, processed_dir: Path) -> "Repository":
        """Opens the DuckDB connection, registers the read-only views, and
        validates the snapshot against `BUILD_INFO.json`'s recorded counts.
        Raises `RepositoryStartupError` rather than starting with silently
        empty or partial data."""
        prices_path = processed_dir / "detached_house_prices.parquet"
        geography_path = processed_dir / "geography_reference.parquet"
        build_info_path = processed_dir / "BUILD_INFO.json"

        for path in (prices_path, geography_path, build_info_path):
            if not path.exists():
                raise RepositoryStartupError(
                    f"Processed snapshot is missing required file: {path}"
                )

        try:
            build_info = json.loads(build_info_path.read_text())
        except json.JSONDecodeError as exc:
            raise RepositoryStartupError(f"BUILD_INFO.json is not valid JSON: {exc}") from exc

        connection = duckdb.connect(":memory:")
        # View registration uses DuckDB's `read_parquet`/`create_view` Python
        # API (not a SQL string) -- DDL statements can't be parameterised via
        # `?` bind parameters, and these paths are fixed at startup by this
        # module, never influenced by a caller-supplied value.
        connection.read_parquet(str(prices_path)).create_view("price_points")
        connection.read_parquet(str(geography_path)).create_view("geography_reference")

        repository = cls(connection)
        repository._validate_against_build_info(build_info, build_info_path)
        return repository

    def _validate_against_build_info(self, build_info: dict, source: Path) -> None:
        expected_counts = build_info.get("counts", {})
        checks: list[tuple[str, str, int | None]] = [
            (
                "price_points",
                "SELECT COUNT(*) FROM price_points",
                expected_counts.get("price_points"),
            ),
            (
                "local_authorities",
                "SELECT COUNT(DISTINCT la_code) FROM geography_reference",
                expected_counts.get("local_authorities"),
            ),
            (
                "periods",
                "SELECT COUNT(DISTINCT period_label) FROM price_points",
                expected_counts.get("periods"),
            ),
        ]
        for name, sql, expected in checks:
            if expected is None:
                raise RepositoryStartupError(
                    f"{source} does not record an expected '{name}' count"
                )
            with self._lock:
                fetched = self._connection.execute(sql).fetchone()
            if fetched is None:
                raise RepositoryStartupError(f"Count query for '{name}' returned no row")
            (actual,) = fetched
            if actual != expected:
                raise RepositoryStartupError(
                    f"Processed snapshot's {name} count ({actual}) does not "
                    f"match {source}'s recorded count ({expected}) -- refusing "
                    f"to start against a snapshot that may not match its build record"
                )

    # -- Repository methods (design §8.6) -----------------------------------

    def get_price_series(
        self,
        la_code: str,
        dataset: Literal["new_build", "existing"],
        period_start: date | None,
        period_end: date | None,
    ) -> list[PricePoint]:
        """Powers `median_price_lookup`, `price_trend`, `growth_metrics`.
        Range filtering compares `period_end_date` (a real date), never
        `period_label` (which does not sort chronologically as text --
        design §8.6, v10)."""
        sql = """
            SELECT * FROM price_points
            WHERE la_code = ?
              AND dataset = ?
              AND (? IS NULL OR period_end_date >= ?)
              AND (? IS NULL OR period_end_date <= ?)
            ORDER BY period_end_date
        """
        with self._lock:
            rows = self._connection.execute(
                sql,
                [la_code, dataset, period_start, period_start, period_end, period_end],
            ).fetchall()
            columns = [d[0] for d in self._connection.description]
        return [PricePoint(**dict(zip(columns, row))) for row in rows]

    def get_premium_series(
        self, la_code: str, period_start: date, period_end: date
    ) -> list[PremiumRow]:
        """Single parameterised query joining new-build and existing rows
        for the same area/period -- no application-side merge (design
        §6.7). Powers `new_build_premium`, `premium_trend`, `premium_series`."""
        sql = """
            SELECT
                nb.la_code AS la_code,
                nb.la_name AS la_name,
                nb.period_label AS period_label,
                nb.period_end_date AS period_end_date,
                nb.price_gbp AS new_build_price,
                nb.suppressed AS new_build_suppressed,
                ex.price_gbp AS existing_price,
                ex.suppressed AS existing_suppressed
            FROM price_points nb
            JOIN price_points ex
              ON nb.la_code = ex.la_code AND nb.period_label = ex.period_label
            WHERE nb.dataset = 'new_build' AND ex.dataset = 'existing'
              AND nb.la_code = ?
              AND nb.period_end_date BETWEEN ? AND ?
            ORDER BY nb.period_end_date
        """
        with self._lock:
            rows = self._connection.execute(sql, [la_code, period_start, period_end]).fetchall()
            columns = [d[0] for d in self._connection.description]
        return [PremiumRow(**dict(zip(columns, row))) for row in rows]

    def get_price_series_multi(
        self,
        la_codes: list[str],
        dataset: Literal["new_build", "existing"],
        period_start: date,
        period_end: date,
    ) -> list[PricePoint]:
        """Fixed query text regardless of how many area codes are passed --
        `= ANY(?)`, never a dynamically built `IN (...)` clause. Powers
        `rank_areas`/`compare_areas`/`scan_for_patterns` (Increment 2+)."""
        sql = """
            SELECT * FROM price_points
            WHERE la_code = ANY(?)
              AND dataset = ?
              AND period_end_date BETWEEN ? AND ?
            ORDER BY la_code, period_end_date
        """
        with self._lock:
            rows = self._connection.execute(
                sql, [la_codes, dataset, period_start, period_end]
            ).fetchall()
            columns = [d[0] for d in self._connection.description]
        return [PricePoint(**dict(zip(columns, row))) for row in rows]

    def get_geography_reference(self) -> list[LocalAuthority]:
        """Powers selector population (`ui/explore_trends.py`,
        `ui/compare_rank.py`) and the alias table behind the geography
        resolver (Increment 4)."""
        sql = "SELECT la_code, la_name, region_country_code, region_country_name, aliases FROM geography_reference ORDER BY la_name"
        with self._lock:
            rows = self._connection.execute(sql).fetchall()
            columns = [d[0] for d in self._connection.description]
        return [LocalAuthority(**dict(zip(columns, row))) for row in rows]

    def get_period_reference(self) -> list[Period]:
        """The single source of truth for "which periods exist" -- powers
        period-selector population and (Increment 4) the period resolver's
        latest-period anchor and nearest-period suggestions."""
        sql = "SELECT DISTINCT period_label, period_end_date FROM price_points ORDER BY period_end_date"
        with self._lock:
            rows = self._connection.execute(sql).fetchall()
        return [Period(label=label, end_date=end_date) for label, end_date in rows]
