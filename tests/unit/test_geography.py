"""STORY-003/TASK-011 unit tests: the geography resolver (CMP-003), against
the real bundled repository/out-of-coverage list, plus small hand-authored
fixtures for TASK-011's fuzzy-ambiguity cases the real (post local-
government-reorganisation) data can no longer literally reproduce -- see
`core/geography.py`'s module docstring for why."""

from __future__ import annotations

from core.geography import resolve_geography
from core.models import LocalAuthority
from core.repository import Repository


class _FixtureRepository:
    """A minimal duck-typed stand-in for `Repository` -- `resolve_geography`
    only ever calls `get_geography_reference()`, so a real DuckDB-backed
    instance isn't needed to test the matching *logic* in isolation."""

    def __init__(self, areas: list[LocalAuthority]) -> None:
        self._areas = areas

    def get_geography_reference(self) -> list[LocalAuthority]:
        return self._areas


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


# -- TASK-011: fuzzy matching, ambiguity, out-of-coverage (Increment 4) ------


def test_richmond_ambiguity_against_a_fixture_reproducing_the_backlogs_example() -> None:
    """TASK-011 AC2, verified against a fixture: the backlog's own example
    ("Richmond" -> "Richmond upon Thames" / "Richmondshire") no longer holds
    against the real bundled data post-reorganisation (see the module
    docstring) -- this proves the ambiguity-detection *mechanism* itself
    against the scenario exactly as specified."""
    fixture_repository = _FixtureRepository(
        [
            LocalAuthority(
                la_code="E09000027",
                la_name="Richmond upon Thames",
                region_country_code="E12000007",
                region_country_name="London",
            ),
            LocalAuthority(
                la_code="E07000999",
                la_name="Richmondshire",
                region_country_code="E12000003",
                region_country_name="Yorkshire and The Humber",
            ),
        ]
    )
    result = resolve_geography(fixture_repository, "Richmond")
    assert result.status == "ambiguous"
    assert {la.la_name for la in result.matches} == {"Richmond upon Thames", "Richmondshire"}


def test_newcastle_ambiguity_against_the_real_bundled_data(real_repository: Repository) -> None:
    """A genuinely ambiguous pair that *does* exist in the current bundled
    dataset: "Newcastle" is a strict substring of both "Newcastle upon Tyne"
    and "Newcastle-under-Lyme" -- neither name should be silently preferred."""
    result = resolve_geography(real_repository, "Newcastle")
    assert result.status == "ambiguous"
    assert {la.la_name for la in result.matches} == {"Newcastle upon Tyne", "Newcastle-under-Lyme"}


def test_richmond_upon_thames_alone_is_unambiguous(real_repository: Repository) -> None:
    """The full, unambiguous name always resolves cleanly, distinguishing
    this from the bare "Richmond" case above."""
    result = resolve_geography(real_repository, "Richmond upon Thames")
    assert result.status == "matched"
    assert result.matches[0].la_name == "Richmond upon Thames"


def test_minor_typo_still_resolves_via_fuzzy_matching(real_repository: Repository) -> None:
    result = resolve_geography(real_repository, "Mancester")  # missing an 'h'
    assert result.status == "matched"
    assert result.matches[0].la_name == "Manchester"


def test_exact_la_code_resolves_directly(real_repository: Repository) -> None:
    """TASK-009: the agent path may legitimately echo back an la_code it
    already has from a prior tool result rather than a name -- this is
    authoritative and unambiguous, so it must resolve directly, never
    fall through to fuzzy matching (which found the code not_found)."""
    result = resolve_geography(real_repository, "E08000003")
    assert result.status == "matched"
    assert result.matches[0].la_name == "Manchester"


def test_england_never_silently_mismatches_to_fenland(real_repository: Repository) -> None:
    """Regression test for a real false positive TASK-009's live-API
    testing surfaced: "England" scored 92.3 against "Fenland" under the
    substring-aware `partial_ratio` (both 7 letters, sharing an "nland"
    suffix) -- a genuine, silent mismatch of exactly the kind ADR-006
    exists to prevent. "England" is not a local authority in this dataset
    at all, so the only correct outcome is not_found, never a fabricated
    match to an unrelated area."""
    result = resolve_geography(real_repository, "England")
    assert result.status == "not_found"
    assert result.matches == []


def test_resolver_never_returns_matched_below_the_confidence_threshold(
    real_repository: Repository,
) -> None:
    """TASK-011 AC5: a name unrelated to anything in the geography
    reference must never be force-matched to the nearest-sounding entry."""
    result = resolve_geography(real_repository, "Xyzzyplonk")
    assert result.status == "not_found"
    assert result.matches == []
