# Housing Market Insights Agent

A three-tab Streamlit dashboard for exploring UK detached-house price data
(ONS HM Land Registry, "year ending September 2025" edition): **Ask the
data** (natural-language chat, OpenAI-agent-backed), **Explore trends**, and
**Compare and rank**. The latter two tabs make **zero calls to the OpenAI
API** and are fully functional with no API key configured (`BR-003`,
`NFR-011`).

> **Status:** All six increments are implemented. "Explore trends" and
> "Compare and rank" are fully working with zero OpenAI API calls (proven
> by an automated test). "Ask the data" wraps the full deterministic tool
> library (price/trend/growth/premium/ranking/comparison/pattern-scan, plus
> geography and period resolvers) with a structural grounding guardrail
> (`agent/guardrails.py`): every numeric claim must resolve to a real,
> non-suppressed field on this turn's own tool output, checked by
> value/unit/area/period, with one automatic repair attempt and a safe
> tool-output-only fallback if grounding still can't be verified — an
> unverified figure is never released. Ambiguous areas trigger a
> clarifying question; out-of-coverage areas (Scotland/NI) are explained,
> with a partial answer for mixed requests; open-ended questions return
> three distinct, evidenced, non-causal observations; follow-up questions
> ("those areas") resolve against the previous turn via a bounded
> conversation session. Every tool call, OpenAI call, and guardrail trigger
> is recorded as a structured JSON-lines log entry, correlated by
> session/turn ID, never containing the API key (`agent/observability.py`).
> `SPIKE-001` ran on 2026-08-13 and confirmed `gpt-4o-mini`
> (`TESTED_DEFAULT_MODEL` in `agent/config.py`).
>
> Live-API testing across Increments 4-5 (not just the stubbed test suite)
> found and fixed several real defects, most notably: a **DuckDB
> thread-safety bug** (the Agents SDK executes a turn's parallel tool calls
> on separate threads; the repository's single shared connection silently
> returned empty/wrong results under concurrent access without a lock —
> `core/repository.py`, now serialised); a **fuzzy geography false
> positive** ("England" scored above the original matching threshold
> against "Fenland" by shared-substring similarity — `core/geography.py`,
> recalibrated); and, most seriously, a **confidently-wrong answer for an
> unsupported dwelling type** (asked about a semi-detached house, the model
> silently substituted and relabelled the real *detached*-house figure —
> `agent/agent_definition.py`, fixed with an explicit instruction and a
> regression test). Full details, including honestly-documented residual
> soft limitations, in the "Assumptions and limitations" section below and
> in each increment's delivery record.

## Setup

```bash
python -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -e ".[dev]"
```

The processed data snapshot (`data/processed/*.parquet`) is already built
and checked into the repository, so no data build step is required to run
the app. To regenerate it from the raw workbooks (`data/raw/*.xlsx`):

```bash
python -m data_pipeline.build --newbuild data/raw/newbuild.xlsx --existing data/raw/existing.xlsx --out data/processed/
```

## Running

```bash
streamlit run ui/dashboard.py
```

No `OPENAI_API_KEY` is required to reach this step — "Explore trends" and
"Compare and rank" are fully usable immediately. Only "Ask the data" needs
a key; without one, it shows a clear unavailable-state message rather than
crashing.

## Configuration

Copy `.env.example` to `.env` and fill in your own values (never commit
`.env`):

| Variable | Required | Notes |
| --- | --- | --- |
| `OPENAI_API_KEY` | No | Enables "Ask the data" only. |
| `OPENAI_MODEL` | No | Overrides the tested default model. See "OpenAI model restriction" below. |
| `LOG_LEVEL` | No | Defaults to `INFO`. |
| `LOG_FILE` | No | Path to a JSON-lines structured log (design §12) — one line per tool call/OpenAI call/guardrail trigger, correlated by session/turn ID, never the API key. Defaults to stderr if unset. |

### OpenAI model restriction

The challenge restricts model usage: **no model at or above "GPT-5.5", and
no "Pro" tier**. This restriction is documented here rather than
mechanically enforced by pattern-matching the model name at startup — an
earlier design considered a startup-time deny-list against disallowed
name substrings (e.g. `gpt-5.5`, `-pro`, `gpt-6`) and rejected it as
brittle, since it hard-codes assumptions about model names that don't
exist yet (`ADR-007`, v4).

Instead: the app is configured with **one tested default model**
(`agent/config.py`'s `TESTED_DEFAULT_MODEL`), confirmed to comply with this
restriction and to support the required capabilities (key access, function
calling, structured outputs) once, empirically, by `SPIKE-001`. Setting
`OPENAI_MODEL` overrides that default; if the override is unavailable or
inaccessible under your key, startup **fails fast with a specific error
naming the model** — it never silently falls back to the default.

## Architecture

A modular monolith, deliberately layered so most of the app never depends
on the OpenAI API at all:

```
data_pipeline/  →  core/                        →  agent/  →  ui/
(offline only,     (deterministic domain logic:     (OpenAI    (Streamlit,
build-time)        DuckDB repository, metrics,       Agents     3 tabs)
                    geography/period resolution)      SDK)
```

- **`data_pipeline/`** parses the two raw ONS workbooks into a validated,
  bundled Parquet snapshot (`data/processed/`) once, offline. Never imported
  by the running app.
- **`core/`** is the single source of truth for every number the app ever
  shows: a DuckDB repository over the Parquet snapshot (parameterised
  queries only — no LLM-generated SQL, ever), pure metric formulas
  (growth/CAGR/premium), and the geography/period resolvers. Imports
  nothing else in this repo, so it's independently unit-testable with no
  mocking. Two tabs ("Explore trends", "Compare and rank") call it
  **directly** — no agent involvement, no OpenAI dependency, by construction
  (`ADR-011`), which is what makes those two tabs work with zero API calls.
- **`agent/`** wraps `core`'s functions as typed OpenAI Agents SDK tools for
  "Ask the data" — the model plans which tool to call and phrases the
  answer; it never computes a figure itself. Every draft answer is checked
  by a structural grounding guardrail (`agent/guardrails.py`) before
  release: each numeric claim must resolve to a real, non-suppressed field
  on that turn's own tool output, checked by value/unit/area/period — not
  by scanning the text for numbers. A failed check gets one repair attempt,
  then a safe, tool-output-only fallback; an unverified figure is never
  released to the user.
- **`ui/`** is a three-tab Streamlit shell. Only `ui/ask_the_data.py` is
  permitted to import `agent` — the other two tabs' code cannot reach the
  OpenAI client even by mistake (enforced by a static import check,
  `tests/unit/test_zero_api_guarantee.py`, not just by convention).

**Key design decisions** (full rationale in
`docs/design/housing-market-insights-agent-system-design.md`'s ADR table):
fixed tool-calling instead of LLM-generated code/SQL (`ADR-001`); a curated
out-of-coverage list plus fuzzy matching for geography, so a real UK place
name is never silently mismatched to the wrong local authority (`ADR-006`);
evidence-linked claims rather than text-scanning for grounding (`ADR-009`);
mixed-coverage requests get a partial, caveated answer rather than a
blanket refusal (`ADR-010`); a fixed, agent-selected chart-type menu
rendered by developer-written functions, never agent-authored chart code
(`ADR-015`).

## Example queries and outputs

Captured from real runs against the live OpenAI API (`gpt-4o-mini`) and the
real bundled dataset — not hand-written.

**"Explore trends" (dashboard tab, no API key involved)** — Manchester,
existing dwellings, "Year ending Sep 2015" to "Year ending Sep 2025":
Latest price **£400,000**, Growth (£) **£180,000**, Growth (%) **81.82%**,
CAGR **6.16%**, plus a line chart and a byte-identical-on-repeat CSV
download.

**"Ask the data" — single factual lookup** (the brief's own example):

> **Q:** What was the median price of an existing detached house in
> Manchester in September 2025?
>
> **A:** The median price of an existing detached house in Manchester in
> September 2025 was £400,000.

**"Ask the data" — multi-step ranking, then a follow-up in the same
session** (the brief's own ranking example, plus its own follow-up
example):

> **Q:** Which five areas saw the largest increase in new-build premium
> between 2015 and 2025?
>
> **A:** The five areas that saw the largest increase in new-build premium
> between 2015 and 2025 are:
> 1. **Manchester** — increase of **42.84 percentage points**
> 2. **Three Rivers** — increase of **39.01 percentage points**
> 3. **Stockport** — increase of **34.80 percentage points**
> 4. **North Hertfordshire** — increase of **34.58 percentage points**
> 5. **Derby** — increase of **34.04 percentage points**
>
> *(with a bar chart, and both "2015"/"2025" flagged in `period_assumptions`
> as inferred to mean "year ending September", the dataset's own
> convention)*
>
> **Q (follow-up, same session):** Which of those areas changed the most in
> the last five years?
>
> **A:** In the last five years, the area that changed the most was
> **Three Rivers** with a price increase of **£478,000**, resulting in a
> growth of **79.67%**. *(followed by the same breakdown for the other four
> — Manchester, Stockport, North Hertfordshire, Derby — correctly scoped to
> exactly the five areas the first answer named, never a fresh full-dataset
> scope.)*

**"Ask the data" — mixed-coverage grounding (Scotland/Glasgow/Edinburgh)** —
the strongest evidence that out-of-coverage geography is explained, never
fabricated:

> **Q:** Compare Glasgow, Edinburgh, and Manchester in terms of long-term
> price growth and new-build premium.
>
> **A:** In terms of long-term price growth and new-build premium,
> Manchester shows promising figures:
> - **Price Growth**: Manchester experienced an impressive growth of
>   **81.82%** from the year ending September 2015 to the year ending
>   September 2025.
> - **New-Build Premium**: The new-build premium in Manchester is currently
>   **23.75%**.
>
> Please note that Glasgow and Edinburgh are outside the coverage of this
> dataset, as HM Land Registry price-paid data only includes regions in
> England and Wales.
>
> *(`coverage_caveats`: one explicit entry each for Glasgow and Edinburgh,
> naming the exact reason — no Scottish figure is ever fabricated, because
> no tool call for Glasgow/Edinburgh ever runs, so no evidence could exist
> to cite even if the model tried.)*

**"Ask the data" — open-ended insight**:

> **Q:** Analyse detached-house prices in England and Wales since 2015 and
> identify three notable patterns supported by the data.
>
> **A** *(this particular run's own drafted narration didn't pass the
> grounding check on its citations, so the guardrail correctly released its
> safe, tool-output-only fallback instead of an unverified figure — shown
> here deliberately, as real evidence the safety net works, not edited
> away)*:
>
> "I couldn't produce a fully verified answer to this question, so here is
> the underlying data retrieved this turn:
> - Isles of Scilly had the highest price growth in scope (**+110.0%**).
> - Southwark had the lowest price growth in scope (**-12.1%**).
> - Median price growth across 316 areas in scope was **+46.2%**."
>
> Three distinct categories (`growth_leader`, `growth_laggard`,
> `regional_growth_distribution`), each a real, evidence-linked
> `InsightCandidate` — no causal language, no invented observation, exactly
> `FR-009`/`ADR-017`'s contract, even under this run's own imperfect
> narration.

## Assumptions and limitations

**Assumptions** (full list with rationale in the requirements package,
`ASM-001`–`ASM-013`): a single local user per session, no concurrency
support needed; queries are in English; "new-build premium" = `(new_build −
existing) / existing × 100` (% primary, £ secondary); prices are nominal,
not inflation-adjusted; relative time expressions ("since 2015", "last
decade") anchor to the dataset's own latest period (year ending September
2025), not real-world "today"; growth/CAGR use the standard formulas in
`core/metrics.py`, with CAGR's `years` the exact elapsed time between
period-end dates, not a rounded integer count.

**Known limitations**, found and honestly documented via live-API testing
(not aspirational — see the full delivery record for root causes):
- **Confidently-wrong answers for unsupported dwelling types** were possible
  before a fix landed in Increment 5: asked about a semi-detached house, the
  model would silently substitute the real *detached*-house figure. Fixed
  with an explicit instruction plus a regression test
  (`tests/unit/test_agent_definition.py`); this class of risk — a real
  number correctly grounded to a field, but mislabelled in prose — is
  structurally difficult for the grounding guardrail to catch on its own,
  since it checks *values*, not what the model's sentence claims they mean.
- A **grounding-guardrail fallback discards the model's entire drafted
  prose**, including any legitimate out-of-coverage caveats, when it also
  has to discard an unverifiable figure — a mixed-coverage question that
  also triggers a fallback can lose its Glasgow/Edinburgh explanation along
  with the bad number. Always safe (no fabrication either way), occasionally
  less complete than it could be.
- A **context-free follow-up** ("which of those areas...", asked with no
  prior turn) is instructed to say so plainly rather than guess, but a
  smaller model doesn't always comply — occasionally proceeds with a
  full-dataset scope instead of asking for clarification.
- The **"Richmond" ambiguity** named in early planning docs no longer
  reproduces against the real bundled data: Richmondshire was abolished in
  England's 2023 local-government reorganisation and no longer exists as a
  separate local authority. The ambiguity-detection *mechanism* is proven
  against a fixture reproducing that exact scenario, and separately against
  a real ambiguous pair that does exist today ("Newcastle").
- A **"cross-dataset period mismatch"** edge case (one dataset missing a
  period the other has) is not reproducible either — both ONS editions'
  period axes are exactly aligned (120 identical labels). Exercised instead
  via a real, equivalent gap: one dataset's component suppressed for a
  period where the other's isn't.

## Tests

```bash
pytest
```

This is Tier 1 (design §13): free, offline, no OpenAI API calls, safe to run as often as you like.

### Evaluation harness (Tier 2)

```bash
python -m eval.run_eval               # dashboard fixtures (free) + chat fixtures (real API calls)
python -m eval.run_eval --dashboard-only   # free half only, no API key needed
python -m eval.run_eval --chat-only        # chat fixtures only, spends API credits
```

Runs the curated fixture set in `eval/fixtures/*.yaml` — the seven brief
questions verbatim (including the Glasgow/Edinburgh/Scotland ones), the
brief's own follow-up example, and the requirements package's happy-path/
edge/negative/non-functional categories — and prints a fixture-by-fixture
pass/fail report plus a summary count. No numeric pass-rate target is
claimed; the fixture-by-fixture report is the evidence. Dashboard fixtures
call `core.tools` directly (the same functions "Explore trends"/"Compare
and rank" call) at zero API cost, with the OpenAI client patched to raise
on any use — an enforced, not just assumed, check of `NFR-011`'s zero-call
guarantee. Chat fixtures call the real OpenAI API on demand — skipped
cleanly, not silently, if `OPENAI_API_KEY` isn't configured. Run on
demand, not in a tight loop or CI (`NFR-006`).

## Project layout

See `docs/design/housing-market-insights-agent-system-design.md` §9 for
the full repository structure and dependency-direction rules.
