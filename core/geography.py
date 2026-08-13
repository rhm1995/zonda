"""Geography Resolver (CMP-003) -- basic scope, per `STORY-003`.

Maps a free-text place name to a canonical local authority. Used
exclusively by the "Ask the data" tab (`ADR-012`): "Explore trends" and
"Compare and rank" use closed selectors bound directly to the geography
reference table, so free-text resolution never arises there.

**Scope note:** this is the *basic* version `STORY-003` calls for --
exact/alias, case-insensitive matching only, sufficient for well-known,
unambiguous names (e.g. "Manchester"). It already returns the full
`GeographyMatch` status vocabulary (`ambiguous`/`out_of_coverage`/
`not_found`) so `TASK-011` (Increment 4) only has to *improve* matching
(fuzzy matching, genuine multi-candidate ambiguity detection) — it does not
need to change this function's contract or any caller of it. This version
never produces `ambiguous` itself (it has no fuzzy layer to produce
multiple candidates from), but the status exists so callers already handle
it correctly ahead of `TASK-011` landing.
"""

from __future__ import annotations

import json
from pathlib import Path

from core.models import GeographyMatch
from core.repository import Repository

_OUT_OF_COVERAGE_PATH = (
    Path(__file__).resolve().parents[1] / "data" / "processed" / "out_of_coverage_places.json"
)


def _load_out_of_coverage_names() -> frozenset[str]:
    if not _OUT_OF_COVERAGE_PATH.exists():
        return frozenset()
    names = json.loads(_OUT_OF_COVERAGE_PATH.read_text())
    return frozenset(name.casefold() for name in names)


def resolve_geography(repository: Repository, query_text: str) -> GeographyMatch:
    """Never guesses silently below a confidence threshold (ADR-006): an
    out-of-coverage or unrecognised name returns a typed non-matched
    status with `matches=[]`, never a fabricated or best-effort area."""
    normalized = query_text.strip().casefold()
    if not normalized:
        return GeographyMatch(query_text=query_text, status="not_found")

    if normalized in _load_out_of_coverage_names():
        return GeographyMatch(
            query_text=query_text,
            status="out_of_coverage",
            coverage_note=(
                f"{query_text.strip()} is not covered by these datasets -- HM Land Registry "
                f"price-paid data (the source of both ONS releases) covers England & Wales only."
            ),
        )

    for local_authority in repository.get_geography_reference():
        if local_authority.la_name.casefold() == normalized:
            return GeographyMatch(query_text=query_text, status="matched", matches=[local_authority])
        if normalized in {alias.casefold() for alias in local_authority.aliases}:
            return GeographyMatch(query_text=query_text, status="matched", matches=[local_authority])

    return GeographyMatch(query_text=query_text, status="not_found")
