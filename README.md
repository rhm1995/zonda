# Housing Market Insights Agent

A three-tab Streamlit dashboard for exploring UK detached-house price data
(ONS HM Land Registry, "year ending September 2025" edition): **Ask the
data** (natural-language chat, OpenAI-agent-backed), **Explore trends**, and
**Compare and rank**. The latter two tabs make **zero calls to the OpenAI
API** and are fully functional with no API key configured (`BR-003`,
`NFR-011`).

> **Status:** Increments 1-3 are implemented — data ingestion, the
> DuckDB-backed repository, the shared analysis tool library, the
> three-tab shell, a fully working "Explore trends" tab (price and premium
> modes), and a fully working "Compare and rank" tab (ranking, new-build
> premium, Plotly chart, CSV export) — both proven, by an automated test,
> to make zero OpenAI API calls — plus a working "Ask the data" tab that
> answers a single factual question end to end via the OpenAI Agents SDK.
> **`SPIKE-001` ran on 2026-08-13 against a live credential** and confirmed
> `gpt-4o-mini` (`TESTED_DEFAULT_MODEL` in `agent/config.py`) for key
> access, function calling, structured outputs, and `CON-002` compliance.
> The live run also surfaced and fixed two real bugs the stubbed test
> suite couldn't catch — a numeric-month tool argument ("09" vs.
> "September") that caused an infinite retry loop, and `structured_data`
> never populating because the SDK's real tool output is a plain `dict`,
> not a `BaseModel` — both now covered by regression tests. Comparison/
> trend/ranking/premium questions, follow-ups, and full grounding
> validation arrive in Increment 4.

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

## Tests

```bash
pytest
```

## Project layout

See `docs/design/housing-market-insights-agent-system-design.md` §9 for
the full repository structure and dependency-direction rules.
