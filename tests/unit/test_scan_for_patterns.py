"""TASK-006 unit tests: `scan_for_patterns`, against a small hand-authored
fixture with known, hand-computed per-category figures -- same pattern as
`test_rank_areas.py`'s fixture tests."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from core.errors import InvalidRangeError
from core.models import InsightCandidate, Period
from core.repository import Repository
from core.tools import MAX_CANDIDATES, MAX_PER_CATEGORY, scan_for_patterns

P1 = Period(label="Year ending Sep 2020", end_date=date(2020, 9, 30))
P2 = Period(label="Year ending Sep 2022", end_date=date(2022, 9, 30))
P3 = Period(label="Year ending Sep 2024", end_date=date(2024, 9, 30))

AREA_A = {"la_code": "E06000001", "la_name": "Area A", "region_country_code": "E12000001", "region_country_name": "Test Region"}
AREA_B = {"la_code": "E06000002", "la_name": "Area B", "region_country_code": "E12000001", "region_country_name": "Test Region"}
AREA_C = {"la_code": "E06000003", "la_name": "Area C", "region_country_code": "E12000001", "region_country_name": "Test Region"}
AREA_D = {"la_code": "E06000004", "la_name": "Area D", "region_country_code": "E12000001", "region_country_name": "Test Region"}
ALL_AREAS = [AREA_A, AREA_B, AREA_C, AREA_D]

# Hand-computed figures (see the fixture below for the raw prices):
#   Area A: steady new-build growth, 100000 -> 110000 -> 120000 (+20% overall)
#   Area B: decline, 100000 -> 95000 -> 90000 (-10% overall)
#   Area C: 100000 -> [suppressed] -> 105000 (+5% overall; largest period jump unavailable at P1->P2)
#   Area D: spike then reversion, 100000 -> 200000 -> 101000 (+1% overall, but a +100% P1->P2 jump)
#   Premium (new_build vs existing) at P1 -> P3:
#     Area A: (100000-90000)/90000=11.11% -> (120000-80000)/80000=50.00%   => +38.89pp (expansion)
#     Area B: (100000-70000)/70000=42.86% -> (90000-85000)/85000=5.88%    => -36.97pp (contraction)
#     Area C: (100000-90000)/90000=11.11% -> (105000-95000)/95000=10.53%  => -0.58pp
#     Area D: (100000-90000)/90000=11.11% -> (101000-90000)/90000=12.22%  => +1.11pp


def _row(area: dict, dataset: str, period: Period, price: int | None, suppressed: bool = False) -> dict:
    return {
        "dataset": dataset,
        "region_country_code": area["region_country_code"],
        "region_country_name": area["region_country_name"],
        "la_code": area["la_code"],
        "la_name": area["la_name"],
        "period_label": period.label,
        "period_end_date": period.end_date,
        "price_gbp": price,
        "suppressed": suppressed,
    }


@pytest.fixture
def scan_repository(tmp_path: Path) -> Repository:
    out_dir = tmp_path / "processed"
    out_dir.mkdir(parents=True)

    rows = [
        # new-build, all three periods
        *[_row(AREA_A, "new_build", p, v) for p, v in zip([P1, P2, P3], [100000, 110000, 120000])],
        *[_row(AREA_B, "new_build", p, v) for p, v in zip([P1, P2, P3], [100000, 95000, 90000])],
        _row(AREA_C, "new_build", P1, 100000),
        _row(AREA_C, "new_build", P2, None, suppressed=True),
        _row(AREA_C, "new_build", P3, 105000),
        *[_row(AREA_D, "new_build", p, v) for p, v in zip([P1, P2, P3], [100000, 200000, 101000])],
        # existing, only P1/P3 are ever read (premium change is a two-endpoint metric)
        _row(AREA_A, "existing", P1, 90000), _row(AREA_A, "existing", P3, 80000),
        _row(AREA_B, "existing", P1, 70000), _row(AREA_B, "existing", P3, 85000),
        _row(AREA_C, "existing", P1, 90000), _row(AREA_C, "existing", P3, 95000),
        _row(AREA_D, "existing", P1, 90000), _row(AREA_D, "existing", P3, 90000),
    ]
    frame = pd.DataFrame(rows)
    frame["price_gbp"] = frame["price_gbp"].astype("Int64")
    frame.to_parquet(out_dir / "detached_house_prices.parquet", index=False)
    pd.DataFrame([{**a, "aliases": []} for a in ALL_AREAS]).to_parquet(
        out_dir / "geography_reference.parquet", index=False
    )
    (out_dir / "BUILD_INFO.json").write_text(
        json.dumps(
            {
                "source": {},
                "edition": "fixture",
                "build_timestamp": "2026-01-01T00:00:00+00:00",
                "counts": {"local_authorities": 4, "periods": 3, "price_points": len(rows)},
            }
        )
    )
    return Repository.open(out_dir)


def _candidates_by_category(result, category: str) -> list[InsightCandidate]:
    return [c for c in result.candidates if c.category == category]


def test_growth_leader_and_laggard_are_correct_and_capped_to_one_by_default(
    scan_repository: Repository,
) -> None:
    result = scan_for_patterns(scan_repository, scope="all", period_or_range=(P1, P3))
    leaders = _candidates_by_category(result, "growth_leader")
    laggards = _candidates_by_category(result, "growth_laggard")
    assert len(leaders) == 1
    assert leaders[0].la_code == "E06000001"  # Area A, +20%
    assert leaders[0].value == pytest.approx(20.0)
    assert leaders[0].evidence_ids == ["E06000001"]
    assert len(laggards) == 1
    assert laggards[0].la_code == "E06000002"  # Area B, -10%


def test_regional_growth_distribution_reports_the_median(scan_repository: Repository) -> None:
    result = scan_for_patterns(scan_repository, scope="all", period_or_range=(P1, P3))
    distribution = _candidates_by_category(result, "regional_growth_distribution")
    assert len(distribution) == 1
    assert distribution[0].la_code is None  # scope-wide, not area-specific
    # Growth values: A=+20%, B=-10%, C=+5%, D=+1% -> median of [-10, 1, 5, 20] = 3.0
    assert distribution[0].value == pytest.approx(3.0)


def test_regional_divergence_identifies_the_area_furthest_from_the_median(
    scan_repository: Repository,
) -> None:
    result = scan_for_patterns(scan_repository, scope="all", period_or_range=(P1, P3))
    divergence = _candidates_by_category(result, "regional_divergence")
    assert len(divergence) == 1
    # median=3.0; |A-3|=17, |B-3|=13, |C-3|=2, |D-3|=2 -> Area A diverges most
    assert divergence[0].la_code == "E06000001"


def test_premium_expansion_and_contraction_are_correct(scan_repository: Repository) -> None:
    result = scan_for_patterns(scan_repository, scope="all", period_or_range=(P1, P3))
    expansion = _candidates_by_category(result, "premium_expansion")
    contraction = _candidates_by_category(result, "premium_contraction")
    assert len(expansion) == 1
    assert expansion[0].la_code == "E06000001"  # Area A, +38.89pp
    assert expansion[0].value == pytest.approx(38.888888, rel=1e-4)
    assert len(contraction) == 1
    assert contraction[0].la_code == "E06000002"  # Area B, -36.97pp
    assert contraction[0].value == pytest.approx(-36.974789, rel=1e-4)


def test_period_on_period_movement_catches_a_spike_masked_by_flat_overall_growth(
    scan_repository: Repository,
) -> None:
    """Area D's overall growth (+1%) looks unremarkable, but it has by far
    the largest single-period jump (+100%, P1 -> P2) -- exactly the
    "distinct from the overall start-end growth" case TASK-006 specifies."""
    result = scan_for_patterns(scan_repository, scope="all", period_or_range=(P1, P3))
    movement = _candidates_by_category(result, "period_on_period_movement")
    assert len(movement) == 1
    assert movement[0].la_code == "E06000004"  # Area D
    assert movement[0].value == pytest.approx(100.0)


def test_coverage_gap_reports_suppressed_observations(scan_repository: Repository) -> None:
    result = scan_for_patterns(scan_repository, scope="all", period_or_range=(P1, P3))
    coverage_gap = _candidates_by_category(result, "coverage_gap")
    assert len(coverage_gap) == 1
    assert coverage_gap[0].value == pytest.approx(1.0)  # Area C's one suppressed new-build observation


def test_unknown_area_code_is_never_fabricated_and_counted_in_coverage(
    scan_repository: Repository,
) -> None:
    result = scan_for_patterns(
        scan_repository, scope=["E06000001", "E09999999"], period_or_range=(P1, P3)
    )
    assert result.coverage.areas_in_scope == 2
    assert result.coverage.areas_excluded == 1


def test_no_qualifying_candidate_means_absent_not_fabricated(scan_repository: Repository) -> None:
    """A scope with only Area A (which has no suppression and complete
    premium data) produces no coverage_gap candidate -- absent, not a
    zero-valued placeholder."""
    result = scan_for_patterns(scan_repository, scope=["E06000001"], period_or_range=(P1, P3))
    assert _candidates_by_category(result, "coverage_gap") == []


def test_max_per_category_returns_more_than_one_when_requested(scan_repository: Repository) -> None:
    result = scan_for_patterns(scan_repository, scope="all", period_or_range=(P1, P3), max_per_category=2)
    leaders = _candidates_by_category(result, "growth_leader")
    assert len(leaders) == 2
    assert [c.salience_rank for c in leaders] == [1, 2]
    assert leaders[0].la_code == "E06000001"  # Area A, +20% (highest)
    assert leaders[1].la_code == "E06000003"  # Area C, +5% (second highest)


def test_max_per_category_and_max_candidates_out_of_bounds_are_rejected(
    scan_repository: Repository,
) -> None:
    with pytest.raises(InvalidRangeError):
        scan_for_patterns(scan_repository, scope="all", period_or_range=(P1, P3), max_per_category=MAX_PER_CATEGORY + 1)
    with pytest.raises(InvalidRangeError):
        scan_for_patterns(scan_repository, scope="all", period_or_range=(P1, P3), max_per_category=0)
    with pytest.raises(InvalidRangeError):
        scan_for_patterns(scan_repository, scope="all", period_or_range=(P1, P3), max_candidates=MAX_CANDIDATES + 1)
    with pytest.raises(InvalidRangeError):
        scan_for_patterns(scan_repository, scope="all", period_or_range=(P1, P3), max_candidates=0)


def test_a_single_period_instead_of_a_range_is_rejected(scan_repository: Repository) -> None:
    with pytest.raises(InvalidRangeError):
        scan_for_patterns(scan_repository, scope="all", period_or_range=P1)  # type: ignore[arg-type]


def test_insight_candidate_schema_has_no_cause_or_reason_field() -> None:
    """AC6: the structural half of "no causal interpretation" is verifiable
    by schema inspection, not merely convention."""
    field_names = set(InsightCandidate.model_fields)
    assert not any(name in field_names for name in ("cause", "reason", "because", "explanation"))


def test_repeated_calls_return_identical_candidates(scan_repository: Repository) -> None:
    first = scan_for_patterns(scan_repository, scope="all", period_or_range=(P1, P3))
    second = scan_for_patterns(scan_repository, scope="all", period_or_range=(P1, P3))
    assert first == second


def test_default_call_returns_at_most_one_candidate_per_category(scan_repository: Repository) -> None:
    result = scan_for_patterns(scan_repository, scope="all", period_or_range=(P1, P3))
    seen_categories: set[str] = set()
    for candidate in result.candidates:
        assert candidate.category not in seen_categories, f"duplicate category {candidate.category!r}"
        seen_categories.add(candidate.category)
        assert candidate.evidence_ids
        assert len(candidate.evidence_ids) <= 5
