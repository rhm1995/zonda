"""Evaluation fixture schemas and loader (`TASK-014`, design `CMP-013`, §13,
§6.2's "Evaluation fixtures" schema entry).

Two fixture kinds, matching the harness's two zero-overlap halves:

- `ChatFixture` -- routed through `agent.orchestrator.answer_question`
  (real OpenAI API calls, on demand -- `NFR-006`/`RSK-001`). Each fixture
  is one or more sequential `ChatTurn`s sharing a single
  `ConversationSession`: a single-question fixture is simply a one-turn
  list; the brief's own follow-up example (`STORY-005`) is a two-turn
  fixture, scored turn by turn.
- `DashboardFixture` -- routed directly through `core.tools`/
  `core.repository`, never through `agent` (`ADR-011`'s "one shared
  computation layer, two callers" -- the same functions
  `ui/explore_trends.py`/`ui/compare_rank.py` call, invoked here without a
  Streamlit runtime). Costs nothing and can run as often as desired.

Fixture files are YAML, one list of fixtures per file, loaded and validated
as Pydantic models -- a malformed fixture file fails fast at load time with
a specific validation error, never silently skipped or partially applied.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field

from core.models import ClaimUnit

FixtureCategory = Literal["happy_path", "edge_case", "negative_case", "non_functional"]
ExpectedStatus = Literal["answered", "clarification_needed", "declined"]

#: The `core.tools` functions a `DashboardFixture` may call -- deliberately
#: the exact set `ui/explore_trends.py`/`ui/compare_rank.py` call (never
#: `scan_for_patterns`, which neither deterministic tab exposes).
DashboardTool = Literal[
    "median_price_lookup",
    "price_trend",
    "growth_metrics",
    "new_build_premium",
    "premium_trend",
    "premium_series",
    "rank_areas",
    "compare_areas",
]


class FactAlternative(BaseModel):
    """One acceptable `(unit, value)` reading for an `ExpectedFact`. More
    than one alternative exists only where the brief's own question is
    genuinely ambiguous about which dataset/framing applies (e.g. "long-term
    price growth" names no dataset, "how has the premium changed" could
    reasonably be reported as a percentage-point change, a £ change, or the
    two endpoint values) -- listing every legitimate reading is honest
    scoring against real ambiguity, not a loosened check."""

    unit: ClaimUnit
    value: float


class ExpectedFact(BaseModel):
    """A turn passes this check if its `claims` contain at least one
    `GroundedClaim` matching `la_code` (when given) and any one of
    `alternatives`, within `tolerance`."""

    la_code: str | None = None
    alternatives: list[FactAlternative]
    tolerance: float = 0.5


class ChatTurn(BaseModel):
    """One question in a `ChatFixture`'s sequence, plus every check to run
    against `answer_question`'s result for it. Every check field defaults
    to "not checked" (`None`/empty/`0`/`False`), so a fixture states only
    the assertions relevant to what it's actually verifying."""

    question: str
    expected_status: list[ExpectedStatus] | None = None
    expected_facts: list[ExpectedFact] = Field(default_factory=list)
    forbidden_substrings: list[str] = Field(default_factory=list)
    required_substrings_any: list[str] = Field(default_factory=list)
    min_coverage_caveats: int = 0
    min_period_assumptions: int = 0
    min_distinct_insight_categories: int = 0
    check_no_causal_language: bool = False
    expect_no_claims: bool = False
    expect_chart: bool = False
    #: When set, every non-null `la_code` cited by this turn's claims must
    #: be a member of this list -- the follow-up fixture's core assertion
    #: (`STORY-005`): a scoped "which of those areas..." must stay scoped
    #: to the prior turn's areas, never expand back to the full dataset.
    allowed_la_codes: list[str] | None = None


class ChatFixture(BaseModel):
    id: str
    category: FixtureCategory
    description: str
    #: `TASK-014`'s reproducibility check (`NFR-002`, requirements §13's
    #: "repeated identical queries return identical results"): when `True`,
    #: the harness runs this fixture's single turn twice, in two fresh
    #: sessions, and requires both runs' facts/status to match each other
    #: (not just each individually matching `expected_status`/`expected_facts`).
    reproducibility_check: bool = False
    turns: list[ChatTurn]


class DashboardFixture(BaseModel):
    """`expected` is a structural subset-match against the tool call's
    typed result -- see `eval.scoring.match_expected` for the (deliberately
    generic, recursive) comparison rules; a nested dict addresses a nested
    object, a list addresses positions in a list-valued field (e.g.
    `rows`/`points`/`areas`), and a bare `null` asserts the field is
    suppressed (`None`)."""

    id: str
    category: FixtureCategory
    description: str
    tool: DashboardTool
    args: dict = Field(default_factory=dict)
    expected: dict = Field(default_factory=dict)
    tolerance: float = 0.5


def load_chat_fixtures(path: Path) -> list[ChatFixture]:
    data = yaml.safe_load(path.read_text())
    return [ChatFixture.model_validate(item) for item in data]


def load_dashboard_fixtures(path: Path) -> list[DashboardFixture]:
    data = yaml.safe_load(path.read_text())
    return [DashboardFixture.model_validate(item) for item in data]
