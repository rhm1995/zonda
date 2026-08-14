"""Geography Resolver (CMP-003) -- full scope, `TASK-011` (Increment 4),
extending `STORY-003`'s basic exact/alias matcher with fuzzy matching and
genuine ambiguity detection.

Used exclusively by the "Ask the data" tab (`ADR-012`): "Explore trends" and
"Compare and rank" use closed selectors bound directly to the geography
reference table, so free-text resolution never arises there.

**Matching stages, in order:**
1. Exact name/alias match (case/whitespace-insensitive) -- unambiguous by
   construction, returned immediately without ever reaching the fuzzy stage.
2. Curated out-of-coverage lookup (Scotland/NI place names) -- deterministic,
   not a fuzzy-match failure (`ADR-006`): a pure fuzzy matcher could weakly
   match "Glasgow" to an unrelated English name and answer wrongly.
3. Fuzzy match (`rapidfuzz`) against every local authority's name and
   aliases, scored by the stronger of a typo-tolerant ratio and a
   substring-aware ratio (so both "Mancestr" [a typo] and "Newcastle" [a
   legitimate short form of two different areas] are handled). Below
   `_FUZZY_THRESHOLD`, the resolver never guesses -- it returns `not_found`
   rather than a low-confidence pick (`ADR-006`). Two or more candidates
   within `_AMBIGUITY_MARGIN` of the top score are returned as `ambiguous`;
   a single dominant candidate is returned as `matched`.

**On "Richmond" (TASK-011 AC2):** the backlog's illustrative example expects
"Richmond" to be ambiguous between "Richmond upon Thames" and
"Richmondshire". The bundled ONS "year ending September 2025" edition
reflects England's 2023 local-government reorganisation, under which
Richmondshire was abolished and absorbed into the new unitary "North
Yorkshire" -- it no longer exists as a separate local authority in this
dataset, so "Richmond" now resolves unambiguously to "Richmond upon Thames"
against the real bundled data. This is a real, present-day discrepancy
between the backlog's illustrative wording and the actual data, not an
implementation gap: `tests/unit/test_geography.py` proves the *mechanism*
against a small fixture reproducing the two-candidate scenario verbatim (so
the ambiguity logic itself is verified exactly as specified), and separately
proves a genuinely ambiguous pair that *does* exist in the real bundled data
today ("Newcastle" -> "Newcastle upon Tyne" / "Newcastle-under-Lyme").
"""

from __future__ import annotations

import json
from pathlib import Path

from rapidfuzz import fuzz

from core.models import GeographyMatch, LocalAuthority
from core.repository import Repository

_OUT_OF_COVERAGE_PATH = (
    Path(__file__).resolve().parents[1] / "data" / "processed" / "out_of_coverage_places.json"
)

#: Below this score (0-100), a name is not considered a candidate at all --
#: the resolver returns `not_found` rather than a low-confidence guess.
#: Calibrated directly against a real false positive TASK-009's live-API
#: testing surfaced: "England" scored 92.3 against "Fenland" under
#: `rapidfuzz.fuzz.partial_ratio` (both 7 letters, sharing the "nland"
#: suffix) -- a genuine, silent mismatch of exactly the kind `ADR-006` was
#: written to prevent, just for "England" instead of "Glasgow"/"Scotland".
#: 90.0 excludes that pair (its `WRatio`-only score is 85.7) while still
#: passing every known-legitimate case this module's tests exercise
#: (typo tolerance at ~94.7, "Newcastle"/"Richmond"-style short forms at
#: exactly 90.0).
_FUZZY_THRESHOLD = 90.0
#: A second-place candidate scoring within this many points of the top
#: candidate makes the result `ambiguous`; anything further behind means the
#: top candidate dominates and the result is `matched`.
_AMBIGUITY_MARGIN = 15.0
#: Queries shorter than this are too short to fuzzy-match reliably (a 2-
#: character query can trivially substring-match almost any name) -- treated
#: as `not_found` rather than fed into the fuzzy stage.
_MIN_FUZZY_QUERY_LENGTH = 3


def _load_out_of_coverage_names() -> frozenset[str]:
    if not _OUT_OF_COVERAGE_PATH.exists():
        return frozenset()
    names = json.loads(_OUT_OF_COVERAGE_PATH.read_text())
    return frozenset(name.casefold() for name in names)


def _exact_match(normalized: str, areas: list[LocalAuthority]) -> LocalAuthority | None:
    for local_authority in areas:
        # An exact la_code match first (TASK-009: the agent path may
        # legitimately pass back a code it already has -- e.g. from a
        # prior tool result's evidence_ids/la_code field -- rather than a
        # name; this is authoritative and unambiguous, never a guess).
        if local_authority.la_code.casefold() == normalized:
            return local_authority
        if local_authority.la_name.casefold() == normalized:
            return local_authority
        if normalized in {alias.casefold() for alias in local_authority.aliases}:
            return local_authority
    return None


def _best_score(query: str, names: list[str]) -> float:
    """`WRatio` only -- deliberately *not* also taking `partial_ratio`'s
    score (an earlier version did): `partial_ratio` rewards any strong
    substring alignment almost regardless of overall word shape, which is
    exactly what let "England" (a country name, not a local authority)
    silently score 92.3 against "Fenland" via a shared "nland" suffix.
    `WRatio` alone still recognises a misspelling ("Mancestr" vs
    "Manchester", ~94.7) and a legitimate short form that's a strict
    substring of a longer official name ("Newcastle" vs "Newcastle upon
    Tyne", exactly 90.0) -- both comfortably clear `_FUZZY_THRESHOLD`.

    `query` is already casefolded by the caller; `name` is not (`la_name`/
    `aliases` keep their canonical display casing) -- `rapidfuzz.fuzz.WRatio`
    does **not** casefold internally (found the hard way: every score above
    was computed against a casefolded `name` and silently dropped by
    10-15 points once real mixed-case names were used), so this function
    must casefold `name` itself before scoring."""
    return max(fuzz.WRatio(query, name.casefold()) for name in names)


def _fuzzy_candidates(normalized: str, areas: list[LocalAuthority]) -> list[tuple[LocalAuthority, float]]:
    scored: list[tuple[LocalAuthority, float]] = []
    for local_authority in areas:
        names = [local_authority.la_name, *local_authority.aliases]
        score = _best_score(normalized, names)
        if score >= _FUZZY_THRESHOLD:
            scored.append((local_authority, score))
    scored.sort(key=lambda pair: (-pair[1], pair[0].la_name))
    return scored


def resolve_geography(repository: Repository, query_text: str) -> GeographyMatch:
    """Never guesses silently below `_FUZZY_THRESHOLD`, and never picks one
    of several near-equally-strong candidates -- both return a typed
    non-`matched` status (`not_found`/`ambiguous`) instead (`ADR-006`)."""
    normalized = query_text.strip().casefold()
    if not normalized:
        return GeographyMatch(query_text=query_text, status="not_found")

    areas = repository.get_geography_reference()

    exact = _exact_match(normalized, areas)
    if exact is not None:
        return GeographyMatch(query_text=query_text, status="matched", matches=[exact])

    if normalized in _load_out_of_coverage_names():
        return GeographyMatch(
            query_text=query_text,
            status="out_of_coverage",
            coverage_note=(
                f"{query_text.strip()} is not covered by these datasets -- HM Land Registry "
                f"price-paid data (the source of both ONS releases) covers England & Wales only."
            ),
        )

    if len(normalized) < _MIN_FUZZY_QUERY_LENGTH:
        return GeographyMatch(query_text=query_text, status="not_found")

    candidates = _fuzzy_candidates(normalized, areas)
    if not candidates:
        return GeographyMatch(query_text=query_text, status="not_found")

    top_score = candidates[0][1]
    close_candidates = [la for la, score in candidates if top_score - score < _AMBIGUITY_MARGIN]

    if len(close_candidates) == 1:
        return GeographyMatch(query_text=query_text, status="matched", matches=close_candidates)
    return GeographyMatch(query_text=query_text, status="ambiguous", matches=close_candidates)
