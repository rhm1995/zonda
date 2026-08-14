"""STORY-006 integration tests: an open-ended question is answered with
three distinct, evidenced, non-causal observations selected from
`scan_for_patterns`' candidate set. Stubbed-model test per the design's
testing philosophy (§13) -- `scan_for_patterns` itself (category bounds,
evidence caps, "no cause field") is already unit-tested directly in
`test_scan_for_patterns.py`; this file proves the orchestrator correctly
carries a multi-observation, per-observation-grounded turn through."""

from __future__ import annotations

from types import SimpleNamespace

from agents import Agent, ToolCallItem, ToolCallOutputItem

from agent.config import Config
from agent.orchestrator import answer_question
from core.models import (
    ConversationSession,
    DraftAnswer,
    EvidenceRef,
    GroundedClaim,
    InsightCandidate,
    PatternScanResult,
    RankingCoverageSummary,
)
from core.repository import Repository

AVAILABLE_CONFIG = Config(
    openai_api_key="sk-test", openai_model="gpt-4o-mini", openai_available=True, log_level="INFO"
)
EMPTY_SESSION = ConversationSession()
_STUB_AGENT = Agent(name="stub", instructions="stub")


def _scan_result() -> PatternScanResult:
    return PatternScanResult(
        scope_description="318 areas, Year ending Sep 2015 to Year ending Sep 2025",
        candidates=[
            InsightCandidate(
                category="growth_leader", salience_rank=1, la_code="E08000003", la_name="Manchester",
                value=42.8, value_unit="pct", evidence_ids=["E08000003"], data_completeness="complete",
                summary="Manchester had the highest price growth in scope (+42.8%).",
            ),
            InsightCandidate(
                category="growth_laggard", salience_rank=1, la_code="E06000030", la_name="Swindon",
                value=-3.1, value_unit="pct", evidence_ids=["E06000030"], data_completeness="complete",
                summary="Swindon had the lowest price growth in scope (-3.1%).",
            ),
            InsightCandidate(
                category="coverage_gap", salience_rank=1, la_code=None, la_name=None,
                value=12.0, value_unit="count", evidence_ids=[], data_completeness="partial",
                summary="12 area/period observations in scope were suppressed.",
            ),
        ],
        coverage=RankingCoverageSummary(areas_in_scope=318, areas_ranked=316, areas_excluded=2),
    )


def test_open_ended_answer_narrates_three_distinct_category_observations(
    real_repository: Repository,
) -> None:
    scan_result = _scan_result()
    tool_call = ToolCallItem(
        agent=_STUB_AGENT,
        raw_item={"type": "function_call", "name": "scan_for_patterns", "call_id": "call_1", "arguments": "{}"},
    )
    tool_output = ToolCallOutputItem(
        agent=_STUB_AGENT, raw_item={"type": "function_call_output", "call_id": "call_1", "output": "{}"},
        output={"status": "ok", **scan_result.model_dump(mode="json")},
    )
    draft = DraftAnswer(
        status="answered",
        answer_text=(
            "Three notable patterns emerge: Manchester had the highest price growth in scope "
            "(+42.8%). Swindon had the lowest price growth in scope (-3.1%). 12 area/period "
            "observations in scope were suppressed."
        ),
        claims=[
            GroundedClaim(
                value=42.8, unit="pct", la_code="E08000003",
                evidence=[EvidenceRef(result_index=0, row_index=0, field="value")],
            ),
            GroundedClaim(
                value=-3.1, unit="pct", la_code="E06000030",
                evidence=[EvidenceRef(result_index=0, row_index=1, field="value")],
            ),
            GroundedClaim(
                value=12.0, unit="count",
                evidence=[EvidenceRef(result_index=0, row_index=2, field="value")],
            ),
        ],
    )

    def run_agent(agent: Agent, question: str, *, max_turns: int) -> object:
        return SimpleNamespace(final_output=draft, new_items=[tool_call, tool_output])

    result = answer_question(
        EMPTY_SESSION,
        "Analyse detached-house prices in England and identify notable patterns",
        config=AVAILABLE_CONFIG, repository=real_repository, run_agent=run_agent,
    )
    assert result.status == "answered"
    assert len(result.claims) == 3
    # Every claim traces to a distinct candidate/category (result verified
    # structurally by the guardrail already having passed -- status wouldn't
    # be "answered" via the fallback path otherwise).
    cited_rows = {claim.evidence[0].row_index for claim in result.claims}
    assert cited_rows == {0, 1, 2}


def test_narration_with_causal_language_is_caught_and_repaired_or_falls_back(
    real_repository: Repository,
) -> None:
    """AC3: "...because demand increased..." must never reach the user,
    even when every figure is otherwise perfectly grounded."""
    scan_result = _scan_result()
    tool_call = ToolCallItem(
        agent=_STUB_AGENT,
        raw_item={"type": "function_call", "name": "scan_for_patterns", "call_id": "call_1", "arguments": "{}"},
    )
    tool_output = ToolCallOutputItem(
        agent=_STUB_AGENT, raw_item={"type": "function_call_output", "call_id": "call_1", "output": "{}"},
        output={"status": "ok", **scan_result.model_dump(mode="json")},
    )
    causal_draft = DraftAnswer(
        status="answered",
        answer_text="Manchester had the highest growth (+42.8%) because of strong local demand.",
        claims=[
            GroundedClaim(
                value=42.8, unit="pct", la_code="E08000003",
                evidence=[EvidenceRef(result_index=0, row_index=0, field="value")],
            )
        ],
    )

    def always_causal_run_agent(agent: Agent, question: str, *, max_turns: int) -> object:
        return SimpleNamespace(final_output=causal_draft, new_items=[tool_call, tool_output])

    result = answer_question(
        EMPTY_SESSION, "Analyse detached-house prices in England and identify notable patterns",
        config=AVAILABLE_CONFIG, repository=real_repository, run_agent=always_causal_run_agent,
    )
    assert "because" not in result.answer_text.casefold()


def test_fewer_than_three_candidates_is_narrated_honestly_not_padded(real_repository: Repository) -> None:
    """AC4: a scope too narrow for a full 3-category candidate set explains
    the limitation rather than silently truncating without comment."""
    small_scan = PatternScanResult(
        scope_description="1 area, Year ending Sep 2015 to Year ending Sep 2025",
        candidates=[
            InsightCandidate(
                category="growth_leader", salience_rank=1, la_code="E08000003", la_name="Manchester",
                value=42.8, value_unit="pct", evidence_ids=["E08000003"], data_completeness="complete",
                summary="Manchester had the highest price growth in scope (+42.8%).",
            ),
        ],
        coverage=RankingCoverageSummary(areas_in_scope=1, areas_ranked=1, areas_excluded=0),
    )
    tool_call = ToolCallItem(
        agent=_STUB_AGENT,
        raw_item={"type": "function_call", "name": "scan_for_patterns", "call_id": "call_1", "arguments": "{}"},
    )
    tool_output = ToolCallOutputItem(
        agent=_STUB_AGENT, raw_item={"type": "function_call_output", "call_id": "call_1", "output": "{}"},
        output={"status": "ok", **small_scan.model_dump(mode="json")},
    )
    honest_draft = DraftAnswer(
        status="answered",
        answer_text=(
            "This scope only yielded one notable pattern: Manchester had the highest price growth "
            "in scope (+42.8%). A narrower scope than requested limited how many distinct "
            "observations were available."
        ),
        claims=[
            GroundedClaim(
                value=42.8, unit="pct", la_code="E08000003",
                evidence=[EvidenceRef(result_index=0, row_index=0, field="value")],
            )
        ],
    )

    def run_agent(agent: Agent, question: str, *, max_turns: int) -> object:
        return SimpleNamespace(final_output=honest_draft, new_items=[tool_call, tool_output])

    result = answer_question(
        EMPTY_SESSION, "Analyse Manchester and identify notable patterns",
        config=AVAILABLE_CONFIG, repository=real_repository, run_agent=run_agent,
    )
    assert result.status == "answered"
    assert len(result.claims) == 1
    assert "one notable pattern" in result.answer_text or "limited" in result.answer_text
