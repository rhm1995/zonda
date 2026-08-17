"""TASK-002 unit tests: the DuckDB-backed repository, against small,
hand-authored temporary Parquet fixtures (per the stakeholder's explicit
testing-approach instruction) -- never the full bundled dataset."""

from __future__ import annotations

import ast
import json
import re
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from core.errors import RepositoryStartupError
from core.repository import Repository

REPO_ROOT = Path(__file__).resolve().parents[2]


def _write_fixture(
    out_dir: Path,
    *,
    prices: list[dict],
    geography: list[dict],
    build_info_overrides: dict | None = None,
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    prices_frame = pd.DataFrame(prices)
    if not prices_frame.empty:
        prices_frame["price_gbp"] = prices_frame["price_gbp"].astype("Int64")
    prices_frame.to_parquet(out_dir / "detached_house_prices.parquet", index=False)

    geo_frame = pd.DataFrame(geography)
    geo_frame.to_parquet(out_dir / "geography_reference.parquet", index=False)

    distinct_periods = {(p["period_label"]) for p in prices}
    distinct_las = {p["la_code"] for p in geography}
    build_info = {
        "source": {},
        "edition": "fixture",
        "build_timestamp": "2026-01-01T00:00:00+00:00",
        "counts": {
            "local_authorities": len(distinct_las),
            "periods": len(distinct_periods),
            "price_points": len(prices),
        },
    }
    if build_info_overrides:
        build_info["counts"].update(build_info_overrides)
    (out_dir / "BUILD_INFO.json").write_text(json.dumps(build_info))


AREA_A = {"la_code": "E06000001", "la_name": "Area A", "region_country_code": "E12000001", "region_country_name": "Test Region", "aliases": []}
AREA_B = {"la_code": "E06000002", "la_name": "Area B", "region_country_code": "E12000001", "region_country_name": "Test Region", "aliases": ["Beeville"]}

# Deliberately chosen so label-sort != date-sort (design §8.6, v10):
# alphabetically "Dec" < "Mar" < "Sep", but chronologically Sep 2015 is the
# earliest and Dec 2022 the latest.
PERIOD_SEP_2015 = {"label": "Year ending Sep 2015", "end_date": date(2015, 9, 30)}
PERIOD_MAR_2020 = {"label": "Year ending Mar 2020", "end_date": date(2020, 3, 31)}
PERIOD_DEC_2022 = {"label": "Year ending Dec 2022", "end_date": date(2022, 12, 31)}


def _price_row(area: dict, dataset: str, period: dict, price_gbp: int | None, suppressed: bool = False) -> dict:
    return {
        "dataset": dataset,
        "region_country_code": area["region_country_code"],
        "region_country_name": area["region_country_name"],
        "la_code": area["la_code"],
        "la_name": area["la_name"],
        "period_label": period["label"],
        "period_end_date": period["end_date"],
        "price_gbp": price_gbp,
        "suppressed": suppressed,
    }


@pytest.fixture
def basic_fixture(tmp_path: Path) -> Path:
    prices = [
        _price_row(AREA_A, "new_build", PERIOD_SEP_2015, 100000),
        _price_row(AREA_A, "new_build", PERIOD_MAR_2020, 150000),
        _price_row(AREA_A, "new_build", PERIOD_DEC_2022, 200000),
        _price_row(AREA_A, "existing", PERIOD_SEP_2015, 90000),
        _price_row(AREA_A, "existing", PERIOD_MAR_2020, 130000),
        _price_row(AREA_A, "existing", PERIOD_DEC_2022, None, suppressed=True),
        _price_row(AREA_B, "new_build", PERIOD_SEP_2015, 80000),
        _price_row(AREA_B, "new_build", PERIOD_MAR_2020, 95000),
        _price_row(AREA_B, "new_build", PERIOD_DEC_2022, 120000),
        _price_row(AREA_B, "existing", PERIOD_SEP_2015, 70000),
        _price_row(AREA_B, "existing", PERIOD_MAR_2020, 85000),
        _price_row(AREA_B, "existing", PERIOD_DEC_2022, 110000),
    ]
    geography = [AREA_A, AREA_B]
    out_dir = tmp_path / "processed"
    _write_fixture(out_dir, prices=prices, geography=geography)
    return out_dir


def test_opens_once_and_registers_views(basic_fixture: Path) -> None:
    repo = Repository.open(basic_fixture)
    result = repo.get_price_series("E06000001", "new_build", None, None)
    assert len(result) == 3


def test_get_price_series_happy_path_matches_spot_check(basic_fixture: Path) -> None:
    repo = Repository.open(basic_fixture)
    result = repo.get_price_series("E06000001", "new_build", date(2020, 1, 1), date(2023, 1, 1))
    assert [p.period_label for p in result] == ["Year ending Mar 2020", "Year ending Dec 2022"]
    assert result[0].price_gbp == 150000


def test_get_price_series_empty_result_for_unknown_area(basic_fixture: Path) -> None:
    repo = Repository.open(basic_fixture)
    result = repo.get_price_series("E06000099", "new_build", None, None)
    assert result == []


def test_get_price_series_surfaces_suppressed_value_as_none(basic_fixture: Path) -> None:
    repo = Repository.open(basic_fixture)
    result = repo.get_price_series("E06000001", "existing", None, None)
    suppressed_points = [p for p in result if p.suppressed]
    assert len(suppressed_points) == 1
    assert suppressed_points[0].price_gbp is None


def test_range_filter_compares_period_end_date_not_label(basic_fixture: Path) -> None:
    """(v10, AC8) A range spanning periods whose labels would sort
    incorrectly as text must still come back in true chronological order."""
    repo = Repository.open(basic_fixture)
    result = repo.get_price_series("E06000001", "new_build", date(2015, 1, 1), date(2023, 1, 1))
    assert [p.period_label for p in result] == [
        "Year ending Sep 2015",
        "Year ending Mar 2020",
        "Year ending Dec 2022",
    ]
    assert [p.period_end_date for p in result] == sorted(p.period_end_date for p in result)


def test_get_premium_series_joins_both_datasets_in_one_query(basic_fixture: Path) -> None:
    repo = Repository.open(basic_fixture)
    rows = repo.get_premium_series("E06000001", date(2015, 1, 1), date(2023, 1, 1))
    assert len(rows) == 3
    by_label = {r.period_label: r for r in rows}
    row = by_label["Year ending Sep 2015"]
    assert row.new_build_price == 100000
    assert row.existing_price == 90000
    suppressed_row = by_label["Year ending Dec 2022"]
    assert suppressed_row.existing_suppressed is True
    assert suppressed_row.existing_price is None


def test_get_premium_series_empty_result_for_unknown_area(basic_fixture: Path) -> None:
    repo = Repository.open(basic_fixture)
    assert repo.get_premium_series("E06000099", date(2015, 1, 1), date(2023, 1, 1)) == []


def test_get_price_series_multi_uses_one_fixed_query_for_any_area_count(basic_fixture: Path) -> None:
    repo = Repository.open(basic_fixture)
    result = repo.get_price_series_multi(
        ["E06000001", "E06000002"], "new_build", date(2015, 1, 1), date(2023, 1, 1)
    )
    assert {p.la_code for p in result} == {"E06000001", "E06000002"}
    assert len(result) == 6

    single = repo.get_price_series_multi(["E06000001"], "new_build", date(2015, 1, 1), date(2023, 1, 1))
    assert len(single) == 3


def test_get_premium_series_multi_matches_per_area_get_premium_series(basic_fixture: Path) -> None:
    """(v17, REV-012) The batched method must return exactly the rows the
    single-area method would for each requested area -- same join, same
    suppression flags, same values -- just fetched in one round trip."""
    repo = Repository.open(basic_fixture)
    batched = repo.get_premium_series_multi(
        ["E06000001", "E06000002"], date(2015, 1, 1), date(2023, 1, 1)
    )
    assert {r.la_code for r in batched} == {"E06000001", "E06000002"}
    assert len(batched) == 6  # 3 periods x 2 areas

    for code in ["E06000001", "E06000002"]:
        individual = repo.get_premium_series(code, date(2015, 1, 1), date(2023, 1, 1))
        from_batch = [r for r in batched if r.la_code == code]
        assert [r.model_dump() for r in from_batch] == [r.model_dump() for r in individual]


def test_get_premium_series_multi_uses_one_fixed_query_for_any_area_count(basic_fixture: Path) -> None:
    repo = Repository.open(basic_fixture)
    result = repo.get_premium_series_multi(["E06000001"], date(2015, 1, 1), date(2023, 1, 1))
    assert len(result) == 3

    result = repo.get_premium_series_multi([], date(2015, 1, 1), date(2023, 1, 1))
    assert result == []


def test_get_premium_series_multi_orders_by_area_then_date(basic_fixture: Path) -> None:
    """Grouping by `la_code` in `core/tools.py` relies on this ordering
    (design §8.6, same convention as `get_price_series_multi`)."""
    repo = Repository.open(basic_fixture)
    result = repo.get_premium_series_multi(
        ["E06000002", "E06000001"], date(2015, 1, 1), date(2023, 1, 1)
    )
    assert [r.la_code for r in result] == ["E06000001"] * 3 + ["E06000002"] * 3
    la_a_dates = [r.period_end_date for r in result if r.la_code == "E06000001"]
    assert la_a_dates == sorted(la_a_dates)


def test_get_geography_reference_returns_typed_records_with_aliases(basic_fixture: Path) -> None:
    repo = Repository.open(basic_fixture)
    result = repo.get_geography_reference()
    assert {la.la_code for la in result} == {"E06000001", "E06000002"}
    area_b = next(la for la in result if la.la_code == "E06000002")
    assert area_b.aliases == ["Beeville"]


def test_get_period_reference_returns_distinct_periods_ordered_by_date(basic_fixture: Path) -> None:
    repo = Repository.open(basic_fixture)
    result = repo.get_period_reference()
    assert [p.label for p in result] == [
        "Year ending Sep 2015",
        "Year ending Mar 2020",
        "Year ending Dec 2022",
    ]


def test_no_method_returns_a_dataframe_or_cursor(basic_fixture: Path) -> None:
    repo = Repository.open(basic_fixture)
    for value in [
        repo.get_price_series("E06000001", "new_build", None, None),
        repo.get_premium_series("E06000001", date(2015, 1, 1), date(2023, 1, 1)),
        repo.get_geography_reference(),
        repo.get_period_reference(),
    ]:
        assert isinstance(value, list)
        for item in value:
            assert not hasattr(item, "to_pandas")
            assert not hasattr(item, "fetchall")


# -- Startup validation ------------------------------------------------------


def test_startup_fails_when_processed_snapshot_is_missing(tmp_path: Path) -> None:
    with pytest.raises(RepositoryStartupError, match="missing"):
        Repository.open(tmp_path / "does_not_exist")


def test_startup_fails_on_row_count_mismatch(tmp_path: Path) -> None:
    out_dir = tmp_path / "processed"
    _write_fixture(
        out_dir,
        prices=[_price_row(AREA_A, "new_build", PERIOD_SEP_2015, 100000)],
        geography=[AREA_A],
        build_info_overrides={"price_points": 999},  # deliberately wrong
    )
    with pytest.raises(RepositoryStartupError, match="price_points"):
        Repository.open(out_dir)


# -- Static checks (defend THR-007 / CON-008 mechanically) ------------------


def test_repository_module_uses_no_string_built_sql() -> None:
    """No SQL text in core/repository.py is built via f-string, `.format()`,
    or string concatenation with a variable -- every query is a fixed
    literal with bound parameters (acceptance criterion 7)."""
    source_path = REPO_ROOT / "core" / "repository.py"
    source = source_path.read_text()

    assert not re.search(r'f"[^"]*SELECT', source, re.IGNORECASE)
    assert not re.search(r"f'[^']*SELECT", source, re.IGNORECASE)
    assert ".format(" not in source

    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            # A '+' anywhere is suspicious for a query-building module;
            # confirm neither operand is a string literal (which would
            # indicate string-concatenated SQL).
            for operand in (node.left, node.right):
                assert not isinstance(operand, ast.Constant) or not isinstance(operand.value, str)


def test_only_repository_module_imports_duckdb() -> None:
    """Import-linter equivalent: `core/repository.py` is the only module
    that imports `duckdb` anywhere in the codebase's importable packages."""
    offenders = []
    for package in ("core", "data_pipeline", "ui", "agent"):
        package_dir = REPO_ROOT / package
        if not package_dir.exists():
            continue
        for path in package_dir.rglob("*.py"):
            if path == REPO_ROOT / "core" / "repository.py":
                continue
            source = path.read_text()
            tree = ast.parse(source, filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import) and any(a.name == "duckdb" for a in node.names):
                    offenders.append(str(path))
                if isinstance(node, ast.ImportFrom) and node.module == "duckdb":
                    offenders.append(str(path))
    assert offenders == []
