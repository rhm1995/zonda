"""STORY-005 unit tests: `agent/session.py`'s `record_turn` (CMP-007,
ADR-008 v15). Uses the real bundled repository so `resolve_period` can
resolve real period labels into `Period` objects, per the design's own
testing note that period resolution needs a real/known period reference."""

from __future__ import annotations

from core.models import (
    AgentTurnResult,
    ComparisonResult,
    ConversationSession,
    GroundedClaim,
    GrowthMetricsResult,
    PriceLookupResult,
    RankedArea,
    RankingCoverageSummary,
    RankingResult,
)
from core.repository import Repository
from agent.session import MAX_RECENT_MESSAGES, record_turn

EMPTY_SESSION = ConversationSession()


def _price_result() -> AgentTurnResult:
    return AgentTurnResult(
        status="answered",
        answer_text="The median price was £400,000.",
        structured_data=[
            PriceLookupResult(
                la_code="E08000003", la_name="Manchester", dataset="existing",
                period_label="Year ending Sep 2025", price_gbp=400000, suppressed=False,
            )
        ],
        claims=[GroundedClaim(value=400000, unit="gbp")],
    )


def test_record_turn_appends_the_verbatim_exchange(real_repository: Repository) -> None:
    updated = record_turn(EMPTY_SESSION, "What was the price in Manchester?", _price_result(), real_repository)
    assert len(updated.recent_messages) == 2
    assert updated.recent_messages[0].role == "user"
    assert updated.recent_messages[0].text == "What was the price in Manchester?"
    assert updated.recent_messages[1].role == "assistant"
    assert updated.recent_messages[1].text == "The median price was £400,000."


def test_record_turn_extracts_la_codes_from_a_scalar_result(real_repository: Repository) -> None:
    updated = record_turn(EMPTY_SESSION, "q", _price_result(), real_repository)
    assert updated.last_area_codes == ["E08000003"]


# -- TASK-015: session_id/turn_number (log-correlation fields only) ----------


def test_record_turn_preserves_session_id_across_turns(real_repository: Repository) -> None:
    session = ConversationSession()
    first = record_turn(session, "q1", _price_result(), real_repository)
    second = record_turn(first, "q2", _price_result(), real_repository)
    assert first.session_id == session.session_id
    assert second.session_id == session.session_id


def test_record_turn_increments_turn_number_by_one_each_call(real_repository: Repository) -> None:
    session = ConversationSession()
    assert session.turn_number == 0
    first = record_turn(session, "q1", _price_result(), real_repository)
    assert first.turn_number == 1
    second = record_turn(first, "q2", _price_result(), real_repository)
    assert second.turn_number == 2


def test_two_fresh_sessions_get_distinct_session_ids() -> None:
    assert ConversationSession().session_id != ConversationSession().session_id


def test_record_turn_extracts_la_codes_from_a_ranking_result(real_repository: Repository) -> None:
    ranking = RankingResult(
        metric="premium_percentage_point_change",
        period_label_or_range="Year ending Sep 2015 to Year ending Sep 2025",
        direction="top",
        rows=[
            RankedArea(rank=1, la_code="E08000003", la_name="Manchester", value=42.8, suppressed=False),
            RankedArea(rank=2, la_code="E07000102", la_name="Three Rivers", value=39.0, suppressed=False),
        ],
        coverage=RankingCoverageSummary(areas_in_scope=318, areas_ranked=2, areas_excluded=316),
    )
    result = AgentTurnResult(
        status="answered", answer_text="...", structured_data=[ranking],
        claims=[GroundedClaim(value=42.8, unit="pct_point")],
    )
    updated = record_turn(EMPTY_SESSION, "Which areas changed the most?", result, real_repository)
    assert updated.last_area_codes == ["E08000003", "E07000102"]
    assert updated.last_metric == "premium_percentage_point_change"


def test_record_turn_resolves_period_labels_into_typed_periods(real_repository: Repository) -> None:
    growth = GrowthMetricsResult(
        la_code="E08000025", la_name="Birmingham", dataset="existing",
        period_start_label="Year ending Sep 2015", period_end_label="Year ending Sep 2025",
        latest_price=445000, latest_price_period="Year ending Sep 2025",
        growth_gbp=135000.0, growth_pct=43.5, cagr_pct=3.7, suppressed_periods=[],
    )
    result = AgentTurnResult(status="answered", answer_text="...", structured_data=[growth])
    updated = record_turn(EMPTY_SESSION, "q", result, real_repository)
    assert updated.last_start_period is not None and updated.last_start_period.label == "Year ending Sep 2015"
    assert updated.last_end_period is not None and updated.last_end_period.label == "Year ending Sep 2025"


def test_record_turn_extracts_dwelling_status_both_when_datasets_differ(real_repository: Repository) -> None:
    comparison = ComparisonResult(
        metric="price", period_label_or_range="Year ending Sep 2025",
        areas=[RankedArea(rank=0, la_code="E08000003", la_name="Manchester", value=400000, suppressed=False)],
        coverage=RankingCoverageSummary(areas_in_scope=1, areas_ranked=1, areas_excluded=0),
    )
    growth_existing = GrowthMetricsResult(
        la_code="E08000003", la_name="Manchester", dataset="existing",
        period_start_label="a", period_end_label="b", latest_price=1, latest_price_period="b",
        growth_gbp=1.0, growth_pct=1.0, cagr_pct=1.0, suppressed_periods=[],
    )
    growth_new_build = growth_existing.model_copy(update={"dataset": "new_build"})
    result = AgentTurnResult(
        status="answered", answer_text="...", structured_data=[growth_existing, growth_new_build]
    )
    updated = record_turn(EMPTY_SESSION, "q", result, real_repository)
    assert updated.last_dwelling_status == "both"
    del comparison  # unused fixture kept for documentation of the shape being tested


def test_record_turn_clears_last_fields_when_nothing_was_resolved(real_repository: Repository) -> None:
    """A clarification turn resolves nothing new -- last_* reflects only
    the most recent turn, never stale state from an earlier one."""
    session_with_history = record_turn(EMPTY_SESSION, "q1", _price_result(), real_repository)
    assert session_with_history.last_area_codes == ["E08000003"]

    clarification_result = AgentTurnResult(status="clarification_needed", answer_text="Did you mean X or Y?")
    updated = record_turn(session_with_history, "House prices in Richmond", clarification_result, real_repository)
    assert updated.last_area_codes == []
    assert updated.last_metric is None
    # But the verbatim exchange is still recorded -- conversational
    # continuity doesn't depend on whether the turn resolved anything.
    assert len(updated.recent_messages) == 4


def test_recent_messages_window_is_bounded_across_many_turns(real_repository: Repository) -> None:
    """AC3: session token growth per turn does not scale with the full
    conversation history length."""
    session = EMPTY_SESSION
    for i in range(10):
        session = record_turn(session, f"question {i}", _price_result(), real_repository)
    assert len(session.recent_messages) == MAX_RECENT_MESSAGES
    # Oldest evicted first -- the window holds only the most recent exchanges.
    assert session.recent_messages[-2].text == "question 9"


def test_record_turn_never_mutates_its_input(real_repository: Repository) -> None:
    original = ConversationSession()
    record_turn(original, "q", _price_result(), real_repository)
    assert original.recent_messages == []
    assert original.last_area_codes == []
