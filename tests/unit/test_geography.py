"""STORY-003 unit tests: the basic geography resolver (CMP-003), against
the real bundled repository/out-of-coverage list."""

from __future__ import annotations

from core.geography import resolve_geography
from core.repository import Repository


def test_exact_name_match_resolves(real_repository: Repository) -> None:
    result = resolve_geography(real_repository, "Manchester")
    assert result.status == "matched"
    assert result.matches[0].la_code == "E08000003"


def test_match_is_case_and_whitespace_insensitive(real_repository: Repository) -> None:
    result = resolve_geography(real_repository, "  manchester  ")
    assert result.status == "matched"
    assert result.matches[0].la_name == "Manchester"


def test_curated_alias_resolves_to_canonical_name(real_repository: Repository) -> None:
    result = resolve_geography(real_repository, "Hull")
    assert result.status == "matched"
    assert result.matches[0].la_name == "Kingston upon Hull, City of"


def test_scotland_and_glasgow_are_out_of_coverage_not_fabricated(real_repository: Repository) -> None:
    for name in ("Glasgow", "Edinburgh", "Scotland"):
        result = resolve_geography(real_repository, name)
        assert result.status == "out_of_coverage", name
        assert result.matches == []
        assert result.coverage_note is not None and "England & Wales" in result.coverage_note


def test_unrecognised_name_is_not_found_never_a_guess(real_repository: Repository) -> None:
    result = resolve_geography(real_repository, "Not A Real Place")
    assert result.status == "not_found"
    assert result.matches == []


def test_empty_query_is_not_found() -> None:
    result = resolve_geography(None, "   ")  # type: ignore[arg-type]
    assert result.status == "not_found"
