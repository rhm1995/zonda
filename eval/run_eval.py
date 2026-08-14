"""Evaluation harness CLI (`TASK-014`, design `CMP-013`, §13).

    python -m eval.run_eval [--chat-only | --dashboard-only]

Runs both fixture sets and prints a fixture-by-fixture pass/fail report
plus a summary count -- no numeric pass-rate SLA is invented (design §2's
own resolution); the fixture-by-fixture report itself is the evidence.

Dashboard fixtures (`eval/fixtures/dashboard.yaml`) call `core.tools`
directly, at zero API cost, with the OpenAI client patched to raise on any
construction (mirrors `TASK-008`'s own mechanism) -- an independent,
enforced check of the same zero-call guarantee, not just an assumption
carried over from that test.

Chat fixtures (`eval/fixtures/chat.yaml`) call `agent.orchestrator.
answer_question` for real, spending API credits (`NFR-006`/`RSK-001`) --
skipped with a clear message, never silently, if no `OPENAI_API_KEY` is
configured. A single fixture's failure (including a raised exception, e.g.
a transient API timeout) is caught and reported as that fixture's failure;
it never aborts the rest of the run (`TASK-014` AC4).
"""

from __future__ import annotations

import argparse
import contextlib
import sys
from pathlib import Path
from typing import Callable, Iterator

from pydantic import BaseModel

from agent.config import Config, load_config
from agent.observability import configure_logging
from agent.orchestrator import answer_question
from core.models import ConversationSession, Period
from core.repository import Repository
from core.tools import (
    compare_areas,
    growth_metrics,
    median_price_lookup,
    new_build_premium,
    premium_series,
    premium_trend,
    price_trend,
    rank_areas,
)
from eval.fixtures import ChatFixture, DashboardFixture, load_chat_fixtures, load_dashboard_fixtures
from eval.scoring import TurnOutcome, facts_equal, score_dashboard_fixture, score_turn

REPO_ROOT = Path(__file__).resolve().parents[1]
PROCESSED_DIR = REPO_ROOT / "data" / "processed"
FIXTURES_DIR = Path(__file__).parent / "fixtures"


class FixtureResult:
    def __init__(self, fixture_id: str, category: str, status: str, details: list[str]) -> None:
        self.fixture_id = fixture_id
        self.category = category
        self.status = status  # "pass" | "fail" | "skip"
        self.details = details


_STATUS_SYMBOL = {"pass": "PASS", "fail": "FAIL", "skip": "SKIP"}


def _report(result: FixtureResult) -> FixtureResult:
    """Prints one fixture's line (plus reasons, if any) the moment it
    completes, flushed immediately -- not batched until the whole section
    finishes. A chat-fixture run can take minutes end to end (real API
    calls, `MAX_TURNS` up to 15 each); silence until the very end would
    look identical to a hang. Returns `result` unchanged, so callers can
    report inline within a comprehension/generator."""
    print(f"[{_STATUS_SYMBOL[result.status]}] {result.fixture_id} ({result.category})", flush=True)
    for detail in result.details:
        print(f"       - {detail}", flush=True)
    return result


# -- Dashboard fixtures: zero-cost, direct core.tools calls -------------------


@contextlib.contextmanager
def _forbid_openai_client() -> Iterator[None]:
    """Mirrors `tests/unit/test_zero_api_guarantee.py`'s runtime
    enforcement, applied here around the dashboard-fixture run so
    TASK-014 AC3's "no OpenAI API call" is independently verified by the
    harness itself, not merely asserted."""
    import openai

    original = openai.OpenAI

    def _raise(*args: object, **kwargs: object) -> None:
        raise RuntimeError(
            "openai.OpenAI() was constructed while running a dashboard fixture -- "
            "this must never happen (ADR-011/NFR-011)."
        )

    setattr(openai, "OpenAI", _raise)
    try:
        yield
    finally:
        setattr(openai, "OpenAI", original)


def _period(period_by_label: dict[str, Period], label: str) -> Period:
    period = period_by_label.get(label)
    if period is None:
        raise ValueError(f"Unknown period label {label!r} -- not present in the dataset's period reference")
    return period


def _period_or_range(args: dict, period_by_label: dict[str, Period]) -> Period | tuple[Period, Period]:
    if "period_label" in args:
        return _period(period_by_label, args["period_label"])
    return (
        _period(period_by_label, args["period_start_label"]),
        _period(period_by_label, args["period_end_label"]),
    )


def _call_dashboard_tool(fixture: DashboardFixture, repository: Repository, period_by_label: dict[str, Period]) -> BaseModel:
    args = fixture.args
    if fixture.tool == "median_price_lookup":
        return median_price_lookup(
            repository, args["area"], args["dataset"], _period(period_by_label, args["period_label"])
        )
    if fixture.tool == "price_trend":
        return price_trend(
            repository, args["area"], args["dataset"],
            _period(period_by_label, args["period_start_label"]), _period(period_by_label, args["period_end_label"]),
        )
    if fixture.tool == "growth_metrics":
        return growth_metrics(
            repository, args["area"], args["dataset"],
            _period(period_by_label, args["period_start_label"]), _period(period_by_label, args["period_end_label"]),
        )
    if fixture.tool == "new_build_premium":
        return new_build_premium(repository, args["area"], _period(period_by_label, args["period_label"]))
    if fixture.tool == "premium_trend":
        return premium_trend(
            repository, args["area"],
            _period(period_by_label, args["period_start_label"]), _period(period_by_label, args["period_end_label"]),
        )
    if fixture.tool == "premium_series":
        return premium_series(
            repository, args["area"],
            _period(period_by_label, args["period_start_label"]), _period(period_by_label, args["period_end_label"]),
        )
    if fixture.tool == "rank_areas":
        return rank_areas(
            repository, args["metric"], _period_or_range(args, period_by_label),
            args.get("scope", "all"), args["top_n"], args["direction"], args.get("dataset", "new_build"),
        )
    if fixture.tool == "compare_areas":
        return compare_areas(
            repository, args["areas"], args["metric"], _period_or_range(args, period_by_label),
            args.get("dataset", "new_build"),
        )
    raise ValueError(f"Unknown dashboard tool {fixture.tool!r}")  # pragma: no cover -- guarded by DashboardTool's Literal


def _run_dashboard_fixtures(repository: Repository) -> list[FixtureResult]:
    fixtures = load_dashboard_fixtures(FIXTURES_DIR / "dashboard.yaml")
    period_by_label = {period.label: period for period in repository.get_period_reference()}
    results: list[FixtureResult] = []
    with _forbid_openai_client():
        for fixture in fixtures:
            try:
                actual = _call_dashboard_tool(fixture, repository, period_by_label)
            except Exception as exc:
                results.append(_report(FixtureResult(fixture.id, fixture.category, "fail", [f"raised {exc!r}"])))
                continue
            outcome = score_dashboard_fixture(fixture, actual)
            results.append(
                _report(
                    FixtureResult(fixture.id, fixture.category, "pass" if outcome.passed else "fail", outcome.reasons)
                )
            )
    return results


# -- Chat fixtures: real API calls, on demand ---------------------------------


def _run_chat_fixture(
    fixture: ChatFixture, config: Config, repository: Repository, run_agent: Callable[..., object] | None = None
) -> FixtureResult:
    """`run_agent` is injectable, exactly mirroring `answer_question`'s own
    seam -- `None` (the real default) for actual harness runs; a hand-built
    stub for this module's own offline unit tests (`tests/unit/
    test_run_eval.py`), so the fixture-looping/exception-handling/
    reproducibility logic here is verified without spending API credits."""
    session = ConversationSession()
    details: list[str] = []
    for turn_index, turn in enumerate(fixture.turns):
        try:
            result = answer_question(session, turn.question, config=config, repository=repository, run_agent=run_agent)
        except Exception as exc:  # AC4: one fixture's failure never aborts the run
            details.append(f"turn {turn_index + 1}: raised {exc!r}")
            return FixtureResult(fixture.id, fixture.category, "fail", details)

        outcome: TurnOutcome = score_turn(turn, result)
        if not outcome.passed:
            details.extend(f"turn {turn_index + 1}: {reason}" for reason in outcome.reasons)
            # Diagnostic evidence for whoever reads a failed fixture's report --
            # the abstract mismatch reasons above say *what* didn't match; this
            # says what the turn actually produced, so a real defect can be told
            # apart from a too-strict fixture expectation without a re-run.
            claim_summary = [(c.la_code, c.unit, c.value) for c in result.claims]
            details.append(
                f"turn {turn_index + 1}: actual status={result.status!r}, "
                f"answer_text={result.answer_text[:300]!r}, claims={claim_summary!r}, "
                f"coverage_caveats={result.coverage_caveats!r}, period_assumptions={result.period_assumptions!r}"
            )

    if fixture.reproducibility_check:
        details.extend(_run_reproducibility_check(fixture, config, repository, run_agent))

    return FixtureResult(fixture.id, fixture.category, "pass" if not details else "fail", details)


def _run_reproducibility_check(
    fixture: ChatFixture, config: Config, repository: Repository, run_agent: Callable[..., object] | None = None
) -> list[str]:
    """NFR-002/requirements §13: re-runs this fixture's single turn in a
    second, independent fresh session and requires it to agree with the
    first run -- `_run_chat_fixture`'s own turn loop already ran it once."""
    turn = fixture.turns[0]
    second_session = ConversationSession()
    try:
        second_result = answer_question(
            second_session, turn.question, config=config, repository=repository, run_agent=run_agent
        )
    except Exception as exc:
        return [f"reproducibility re-run: raised {exc!r}"]
    first_result = answer_question(
        ConversationSession(), turn.question, config=config, repository=repository, run_agent=run_agent
    )
    return [f"reproducibility: {reason}" for reason in facts_equal(first_result, second_result)]


def _run_chat_fixtures(config: Config, repository: Repository) -> list[FixtureResult]:
    fixtures = load_chat_fixtures(FIXTURES_DIR / "chat.yaml")
    return [_report(_run_chat_fixture(fixture, config, repository)) for fixture in fixtures]


def _skip_chat_fixtures(reason: str) -> list[FixtureResult]:
    fixtures = load_chat_fixtures(FIXTURES_DIR / "chat.yaml")
    return [_report(FixtureResult(fixture.id, fixture.category, "skip", [reason])) for fixture in fixtures]


# -- Report --------------------------------------------------------------------


def _print_summary(results: list[FixtureResult]) -> bool:
    """Prints the aggregate counts; returns whether the whole run is clean
    (no `fail` results -- a `skip` does not count against this)."""
    passed = sum(1 for r in results if r.status == "pass")
    failed = sum(1 for r in results if r.status == "fail")
    skipped = sum(1 for r in results if r.status == "skip")
    print(f"{passed} passed, {failed} failed, {skipped} skipped, {len(results)} total")
    return failed == 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Housing Market Insights Agent -- evaluation harness")
    parser.add_argument("--chat-only", action="store_true", help="Run only the chat fixtures (real API calls).")
    parser.add_argument(
        "--dashboard-only", action="store_true", help="Run only the dashboard fixtures (zero API cost)."
    )
    args = parser.parse_args(argv)

    repository = Repository.open(PROCESSED_DIR)
    results: list[FixtureResult] = []

    if not args.chat_only:
        print("== Dashboard fixtures (zero API cost) ==", flush=True)
        dashboard_results = _run_dashboard_fixtures(repository)
        results.extend(dashboard_results)
        print(f"(Zero-API-call guarantee enforced for all {len(dashboard_results)} dashboard fixtures above.)\n")

    if not args.dashboard_only:
        print("== Chat fixtures (real API calls) ==", flush=True)
        config = load_config()
        configure_logging(config.log_file, config.log_level)
        if not config.openai_available:
            chat_results = _skip_chat_fixtures("no OPENAI_API_KEY configured -- set it to run chat fixtures")
        else:
            chat_results = _run_chat_fixtures(config, repository)
        results.extend(chat_results)
        print()

    print("== Summary ==")
    clean = _print_summary(results)
    return 0 if clean else 1


if __name__ == "__main__":
    sys.exit(main())
