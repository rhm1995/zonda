"""Evaluation Harness (`TASK-014`, design `CMP-013`, §13).

Two independent halves, matching design §13's own Tier 1/Tier 2 split:

- `eval.fixtures` -- typed fixture schemas (`ChatFixture`/`DashboardFixture`)
  loaded from `eval/fixtures/*.yaml`.
- `eval.scoring` -- pure, offline-testable pass/fail logic against an
  already-produced `AgentTurnResult` or tool result -- no API/DB calls of
  its own, so it is covered by the free `pytest` suite (Tier 1).
- `eval.run_eval` -- the CLI entry point (`python -m eval.run_eval`) that
  actually calls `agent.orchestrator.answer_question` (real API, on
  demand, `NFR-006`) and `core.tools` (free, `ADR-011`) and prints the
  fixture-by-fixture report.
"""
