# Requirements Package — Housing Market Insights Agent

**Prepared by:** Requirements Analyst (per `claude-agents/Requirements Analyst.md`)
**Source document:** `technical-challenge.pdf` — "Technical Challenge — Housing Market Insights Agent" (3 pages, undated challenge brief, issuer unnamed)
**Stakeholder answers received:** three, so far — a 2026-08-12 addendum from the product owner mandating (1) a three-tab dashboard structure and zero-API-call behaviour for two of its tabs (BR-003/FR-021–FR-041/NFR-011), (2) a runtime data-engine change to DuckDB over bundled Parquet (CON-008/CON-009), and (3) a 2026-08-13 visualisation-plan directive adding a premium chart mode to "Explore trends" (FR-042–FR-045). All other open items remain recorded as assumptions or questions rather than resolved facts.

> **Secret redaction notice:** page 2 of the source document embeds a live credential-sharing link ("OpenAI API token: https://share.1password.com/..."). Per operating rules, this has been replaced with `[REDACTED]` throughout this package and is **not** reproduced anywhere below. See RSK-002 and NFR-005.

> **Revision history:** v1.1 (2026-08-12) — incorporates an explicit stakeholder addendum specifying a mandatory three-tab dashboard structure ("Ask the data", "Explore trends", "Compare and rank") and a hard requirement that two of the three tabs operate with **zero** OpenAI API calls. New/changed items are marked `(Addendum)` in their Source column. This narrows IR-001, which previously left interface modality fully open — see IR-001's updated row and AMB-007/RSK-005 for the auditable trail of what changed and why.
>
> **v1.2 (2026-08-12, same day)** — incorporates a second stakeholder directive mandating the runtime analytical engine: DuckDB over the bundled Parquet snapshot via fixed, developer-written, parameterised repository methods, with Pandas/OpenPyXL scoped to offline ingestion only and no LLM-generated or LLM-executed SQL (CON-008, CON-009). This narrows DR-007, which previously left storage format fully open — see DR-007's updated row and RSK-006 for the auditable trail. Confirmed with the stakeholder: this is a pure internal engine substitution — every existing tool signature, Pydantic schema, and agent/UI contract stays as designed, except where a current interface leaks Pandas-specific types (e.g. a raw DataFrame) across a component boundary, which should be tightened to a typed record/Pydantic model as part of the same change. Priority: Must, committed in full now — no pandas-runtime fallback path is to be built (see RSK-006 for the accepted time-budget trade-off this implies).
>
> **v1.3 (2026-08-13)** — backfills a gap the system designer surfaced (not silently absorbed into the design): the product owner's visualisation plan names four required chart types — price trend, new-build premium trend, area comparison, ranking — but this package's "Explore trends" scope (`FR-025`–`FR-034`) only ever specified a price trend chart. Premium previously appeared only as a point-in-time "Compare and rank" metric (`FR-036`/`FR-039`). New atomic requirements `FR-042`–`FR-045` capture the premium-mode chart directly from the product owner's stated bullets (area selection reuses `FR-025`; percentage-over-time display; an optional GBP unit toggle; negative-premium-as-discount labelling; missing-period display consistent with `FR-033`). This does not contradict `ASM-013` — that assumption was scoped to "Compare and rank" not needing a *separate ranking view* for premium, not to "Explore trends" never showing a premium *trend*; see `ASM-013`'s updated row.

---

## 1. Executive summary

The brief asks for a **locally runnable application** that lets a user explore and analyse historical UK **detached-house price data** (newly built and existing dwellings, ONS "year ending September 2025" edition, tab 2b of each dataset) through **natural-language queries**. The system must handle single-fact lookups, comparisons, trends, rankings, cross-dataset analysis (e.g. new-build "premium"), multi-step analysis, in-session follow-ups, and open-ended insight generation — while returning accurate, reproducible results and gracefully handling missing data, ambiguous questions, and questions the data cannot answer. A limited, unspecified OpenAI credit allowance is available (models at/above "GPT-5.5" and any "Pro" tier are explicitly excluded), and the brief explicitly leaves data storage, application structure, orchestration pattern, model choice, framework, and interface modality to the implementer. The deliverable is a self-contained package (source, README, tests/evaluation, architecture summary, example outputs) sized to roughly 8–12 hours of focused work.

**Level of certainty:** High on functional scope and deliverable contents (explicitly enumerated). Low on several details needed to build reliably: the actual geography level(s) and time granularity inside the ONS tab 2b workbooks (not inspected as part of this analysis — see AMB-001/AMB-002), the intended definition of "new-build premium" (AMB-005), and whether OpenAI usage is mandatory for all natural-language handling or only "where it adds value" (AMB-003). None of these are blocking — the brief explicitly permits implementer discretion on approach — but they are architecture-shaping and should be resolved by documented assumption before design proceeds.

**Addendum (2026-08-12):** the stakeholder has since mandated that the application take the specific form of a **three-tab dashboard** rather than a single chat surface: an "Ask the data" tab (the natural-language/chat capability described above), an "Explore trends" tab, and a "Compare and rank" tab. Critically, the latter two tabs **must operate using zero calls to the OpenAI API** — not merely tolerate its absence, but not invoke it at all in their normal operation — so that area/dataset/period exploration, growth and CAGR metrics, top/bottom ranking, and new-build premium comparison remain fully usable with no API key configured, an unreachable API, or an exhausted credit allowance. This is now a first-class, high-priority requirement set (BR-003, FR-021–FR-041, NFR-011) rather than an implementation detail of how chat answers happen to render, and it materially narrows what was previously open in IR-001 (see that row for the superseded framing).

---

## 2. Scope

**In scope**
- A locally runnable system answering natural-language questions about UK **detached** house prices from the two named ONS datasets (newly built, existing), tab 2b, year-ending-September-2025 edition.
- Factual lookup, comparison, trend, ranking, and cross-dataset (premium) analysis.
- Multi-step analysis and in-session follow-up questions.
- Open-ended, data-backed insight generation.
- Graceful handling of missing data, ambiguous questions, and unanswerable requests.
- A small automated evaluation/test suite.
- Configurable OpenAI API credentials; model usage restricted to below "GPT-5.5" and non-"Pro" tiers.
- Source code, README, architecture summary, and example queries/outputs as submission contents.
- **(Addendum)** A dashboard application with exactly three tabs — "Ask the data" (chat), "Explore trends", and "Compare and rank" — as dedicated, first-class deliverables rather than incidental chat-response rendering (BR-003, FR-021–FR-041, CON-006).
- **(Addendum)** "Explore trends" and "Compare and rank" implemented so they make **zero OpenAI API calls**: area/multi-area selection, dwelling-type (new-build/existing) selection, period selection, time-series/ranking charts, latest price, absolute and percentage growth, CAGR, new-build premium comparison, explicit missing-value display, and CSV download (NFR-011).
- **(Addendum)** "Ask the data" answers rendered with supporting tables/charts and an expandable calculation/source-detail view, not text-only output (FR-023, FR-024).
- **(v1.3 addendum)** "Explore trends" includes a premium-mode chart (new-build premium over time for the selected area, with a %/£ unit toggle, discount labelling for negative premium, and missing-period display) alongside its existing price-mode chart (FR-042–FR-045).

**Out of scope**
- Any dwelling type other than "detached" (semi-detached, terraced, flats/maisonettes are not part of the supplied datasets).
- Any geography outside what the named ONS "administrative geographies" datasets cover.
- Data beyond the "year ending September 2025" edition (no live/updating data feed implied).
- Infrastructure hosted by the submitter (the brief explicitly rules this out — page 3).
- Any use of OpenAI models at or above "GPT-5.5", or any "Pro" model tier.

**Scope not yet confirmed**
- Whether OpenAI API use is mandatory for all natural-language interpretation or only expected "where it adds clear value" (AMB-003) — a rule-based layer for simple lookups may be an acceptable partial substitute.
- Whether inflation-adjusted ("real terms") comparisons are expected — no CPI/RPI data is supplied, and the brief does not mention inflation adjustment, so this is presumed out of scope unless the implementer chooses to source and clearly label such an addition (see ASM-004).
- Multi-user / concurrent-session support — the brief describes a single conversational "session" and does not mention multiple simultaneous users (see ASM-001).
- Whether the submitted package should bundle a processed data snapshot or fetch/require the raw ONS files at setup time (see AMB-006 / Q3).

---

## 3. Stakeholders and user groups

| Actor | Goal | Interaction |
| --- | --- | --- |
| End user / analyst (primary system user) | Explore and understand UK detached-house price trends via natural language, without needing to write queries or code | Issues NL questions and follow-ups to the running application in a session |
| Challenge issuer / assessor (brief author, unnamed) | Evaluate correctness, design quality, insight quality, grounding, evaluation rigour, API efficiency, and ease of local setup | Downloads/reads submission, runs it locally with their own OpenAI credentials, poses questions including ones not in the illustrative list |
| OpenAI API (external service) | N/A — infrastructure dependency | Receives model calls for NL interpretation and/or insight generation; bounded by a limited, unspecified credit allowance and a model-tier restriction |
| ONS (data publisher, external, not an interactive stakeholder) | N/A — source-of-record for the datasets | Provides the two source workbooks at the given URLs; no further interaction expected |
| Product owner (interactive stakeholder, this session) | Directly specify UI/dashboard requirements not covered by the original brief, and confirm decisions the brief left open | Issued the 2026-08-12 dashboard addendum directly (three-tab structure, zero-API-call requirement for two tabs); further answers from this actor update this package per the interaction rules below rather than being appended as separate notes |

*The "end user / analyst" role is inferred from the nature of the example questions (page 1–2); the brief does not name this actor explicitly but implies a single conversational user per session. The "Product owner" row is the first genuinely interactive stakeholder this package has received input from — see the revision history note above.*

---

## 4. Business requirements

| ID | Requirement | Priority | Status | Rationale | Source | Acceptance criteria | Dependencies |
| --- | --- | --- | --- | --- | --- | --- | --- |
| BR-001 | The system must let a user explore and analyse historical UK detached-house price data (newly built and existing) through natural-language queries, running fully on the local machine. | Must | Explicit | States the core purpose of the challenge | p.1, "The task" | A user can pose the example question types (lookup, trend, comparison, ranking, cross-dataset, open-ended) and receive a data-grounded answer without any submitter-hosted service being reachable | FR-001–FR-013 |
| BR-002 | The delivered submission must be independently assessable: it must run locally from the package contents, and its analysis, design, insight quality, grounding, evaluation approach, and OpenAI API efficiency must be reviewable without further input from the submitter. | Must | Explicit | Directly reflects the "What we'll assess" criteria and submission requirements | p.2–3 | An assessor can follow the README from a clean environment to install, configure credentials, run, and test the system, and can independently judge each listed assessment criterion | FR-020, IR-003, NFR-004–NFR-010 |
| BR-003 | The delivered application must demonstrate clear, deterministic (non-LLM) value even when the OpenAI API is unavailable — unconfigured, unreachable, or out of credit — via a dashboard experience that goes beyond the chat interface. | Must | Explicit (Addendum) | States the business rationale behind the dashboard/zero-API-call requirement, not just its mechanics | Product owner, 2026-08-12 addendum: "This distinction matters because the dashboard provides useful deterministic functionality even if: No API key has been configured. The API is unavailable. The credit allowance has expired." | With `OPENAI_API_KEY` unset, both "Explore trends" and "Compare and rank" remain fully operable end to end; only "Ask the data" shows a clear unavailable-state message | FR-021–FR-041, NFR-011 |

---

## 5. Functional requirements

### 5.1 Natural-language querying and analysis

| ID | Requirement | Priority | Status | Rationale | Source | Acceptance criteria | Dependencies |
| --- | --- | --- | --- | --- | --- | --- | --- |
| FR-001 | The system must accept natural-language questions about the supplied datasets. | Must | Explicit | Core interaction mode | p.1 bullets | A free-text question is accepted and produces a response | DR-001, DR-002 |
| FR-002 | The system must answer factual lookup questions, e.g. the median price for a given area, dwelling type (newly built/existing), and time period present in the data. | Must | Explicit | Named example type | p.1 bullets; example Q1 | Given a valid area/type/period combination present in the data, the returned figure matches the source workbook value | FR-014, FR-015, DR-005 |
| FR-003 | The system must answer comparison questions across areas, dwelling types, and/or time periods. | Must | Explicit | Named example type | p.1 bullets; example Q5 | A comparison question returns the compared figures plus a stated basis of comparison | FR-002 |
| FR-004 | The system must answer trend questions describing how prices have changed over a stated or implied time range. | Must | Explicit | Named example type | p.1 bullets; example Q2, Q3 | A trend question returns direction/magnitude of change with the start and end reference points used | DR-004, ASM-005 |
| FR-005 | The system must answer ranking questions (e.g. top/bottom N areas by a specified metric). | Must | Explicit | Named example type | p.1 bullets; example Q4 | A ranking question returns an ordered list with the metric and period used to rank | FR-002–FR-004 |
| FR-006 | The system must answer questions that require combining the newly built and existing datasets, including computing a newly built "premium" over existing prices. | Must | Explicit | Named example type; explicit cross-dataset requirement | p.1 bullets; example Q3, Q4, Q5 | Given both datasets contain the needed area/period, a premium figure is returned with its definition stated | FR-014, FR-015, ASM-003 |
| FR-007 | The system must support questions requiring multiple sequential steps of analysis (e.g. filter, then aggregate, then rank). | Must | Explicit | Named requirement | p.1 bullets | A multi-step question (e.g. example Q4/Q6) is decomposed and answered without requiring the user to break it into sub-questions | FR-002–FR-006 |
| FR-008 | The system must retain conversational state within a session so it can resolve reasonable follow-up questions referring back to prior answers. | Must | Explicit | Named requirement, illustrated by the follow-up example | p.1 bullets; p.2 follow-up example | Posing the illustrative follow-up ("Which of those areas changed the most in the last five years?") after a prior multi-area answer resolves "those areas" correctly | FR-003–FR-005 |
| FR-009 | The system must produce broader, data-backed insights, citing supporting data, when the user asks a less specific/open-ended question. | Must | Explicit | Named requirement, illustrated by example Q6 | p.1 bullets; example Q6 | An open-ended request (e.g. "analyse detached-house prices in Scotland since 2015") returns multiple distinct, data-referenced observations, not a single figure | FR-002–FR-007 |
| FR-010 | Calculations, filtering, aggregation, and ranking must be accurate and reproducible: identical queries against unchanged data return identical results. | Must | Explicit | Named requirement | p.1 bullets | Re-running the same query twice returns numerically identical results; spot-checked figures match manual calculation from the source workbook | NFR-001, NFR-002 |
| FR-011 | The system must detect ambiguous questions and either ask a clarifying question or explicitly state the assumption it is making before answering. | Must | Derived | Necessary to satisfy "handle ... ambiguous questions" (p.1) in an observable way | p.1 bullets | A deliberately ambiguous test question (e.g. an area name matching more than one geography) produces either a clarifying question or a stated, visible assumption — never a silent guess | AMB-002 |
| FR-012 | The system must detect when a question cannot be reliably answered from the supplied datasets and say so, rather than fabricating an answer. | Must | Explicit | Named requirement; also drives the "grounding" assessment criterion | p.1 bullets; p.3 "Grounding and handling of unsupported ... questions" | A question about an unsupported dwelling type, unsupported geography, or unrelated topic returns an explicit "cannot answer from this data" response, not an invented figure | DR-003 |
| FR-013 | The system must detect and communicate missing/suppressed data relevant to a query rather than silently omitting or fabricating it. | Must | Explicit | Named requirement | p.1 bullets | A query touching a known-missing/suppressed cell in the source data is answered with an explicit statement that the figure is unavailable | DR-006 |

### 5.2 Data ingestion

| ID | Requirement | Priority | Status | Rationale | Source | Acceptance criteria | Dependencies |
| --- | --- | --- | --- | --- | --- | --- | --- |
| FR-014 | The system must ingest tab 2b ("median prices paid for newly built detached houses") of the ONS "Median house prices for administrative geographies, newly built dwellings" dataset, year-ending-September-2025 edition. | Must | Explicit | Named data source | p.1 "Dataset" | The ingested data reproduces at least a sample of known values from the source tab exactly | DR-001 |
| FR-015 | The system must ingest tab 2b ("median prices paid for existing detached houses") of the ONS "Median house prices for administrative geographies, existing dwellings" dataset, year-ending-September-2025 edition. | Must | Explicit | Named data source | p.1 "Dataset" | The ingested data reproduces at least a sample of known values from the source tab exactly | DR-002 |
| FR-016 | The system may process or transform the source data as needed (reshape, clean, load into a queryable store) to support querying and analysis. | Should | Explicit | Explicitly permitted, not mandated in any specific form | p.1, "You may process or transform the data as needed for your solution" | Chosen transformation is documented and does not alter the underlying figures | FR-014, FR-015 |

### 5.3 OpenAI API usage

| ID | Requirement | Priority | Status | Rationale | Source | Acceptance criteria | Dependencies |
| --- | --- | --- | --- | --- | --- | --- | --- |
| FR-017 | The system must use the OpenAI API for natural-language interpretation and/or insight generation where it adds clear, demonstrable value, applied thoughtfully rather than maximised in call volume. | Should | Explicit | Direct instruction, though conditioned on "adds clear value" (see AMB-003) | p.2, "Use of OpenAI APIs" | Design documentation explains where and why each model call is made; no call is made whose output is unused | CON-002, CON-003 |
| FR-018 | The system must not depend on any OpenAI model at or above "GPT-5.5", nor any "Pro" model tier. | Must | Explicit | Explicit restriction | p.2, "Access to GPT-5.5 and above, as well as Pro models, will be restricted" | Configuration/code contains no reference to a restricted model as the operative model; if attempted, calls fail fast with a clear error rather than silently succeeding via an unintended fallback | — |
| FR-019 | The system must accept OpenAI API credentials via a configurable mechanism so an assessor can supply their own credentials at run time. | Must | Explicit | Explicit requirement | p.3, "OpenAI API credentials should be configurable" | Running the app with a different valid API key (e.g. via an env var or config file) requires no code change | NFR-004 |

### 5.4 Evaluation

| ID | Requirement | Priority | Status | Rationale | Source | Acceptance criteria | Dependencies |
| --- | --- | --- | --- | --- | --- | --- | --- |
| FR-020 | The submission must include a small automated evaluation or test suite exercising the system's query-answering and analysis behaviour. | Must | Explicit | Named deliverable requirement | p.1 bullets; p.3 "Automated tests or evaluation" | A documented command runs the suite and reports pass/fail (or scored) results without manual steps | FR-002–FR-013 |

### 5.5 Dashboard: "Ask the data" tab (Addendum)

| ID | Requirement | Priority | Status | Rationale | Source | Acceptance criteria | Dependencies |
| --- | --- | --- | --- | --- | --- | --- | --- |
| FR-021 | The application must present a dedicated "Ask the data" tab providing the chat interface described in FR-001–FR-013. | Must | Explicit (Addendum) | Named tab | Product owner, 2026-08-12 addendum | The tab labelled "Ask the data" contains the full conversational NL query capability | FR-001–FR-013, IR-004 |
| FR-022 | The "Ask the data" tab must display example prompts the user can use to begin a query. | Must | Explicit (Addendum) | Named requirement | Product owner, 2026-08-12 addendum | On opening the tab (or an empty session), at least one example prompt is visible and usable without typing | FR-021 |
| FR-023 | The "Ask the data" tab must render tables and/or charts generated from the structured tool results backing an answer, not a text-only response, wherever the answer contains tabular or comparable numeric data. | Must | Explicit (Addendum) | Named requirement; operationalises the existing "structured tool outputs" design intent as a visible UI requirement | Product owner, 2026-08-12 addendum | A ranking, comparison, or trend answer displays an accompanying table or chart alongside the prose answer | FR-003–FR-006, FR-021 |
| FR-024 | The "Ask the data" tab must let the user expand any answer to reveal the calculation steps and data sources used to produce it. | Must | Explicit (Addendum) | Named requirement; directly operationalises NFR-003 (grounding) as a user-facing affordance | Product owner, 2026-08-12 addendum | Each answer has an expandable/collapsible section showing the tool call(s), inputs, and source dataset/period/area used | NFR-003, FR-021 |

### 5.6 Dashboard: "Explore trends" tab — zero OpenAI calls (Addendum)

| ID | Requirement | Priority | Status | Rationale | Source | Acceptance criteria | Dependencies |
| --- | --- | --- | --- | --- | --- | --- | --- |
| FR-025 | The "Explore trends" tab must provide an area selector. | Must | Explicit (Addendum) | Named control | Product owner, 2026-08-12 addendum | User can pick a single area from the covered England & Wales local authorities | DR-005 |
| FR-026 | The "Explore trends" tab must provide a new-build/existing dataset selector. | Must | Explicit (Addendum) | Named control | Product owner, 2026-08-12 addendum | User can switch between the two source datasets (or view both) for the selected area | DR-001, DR-002 |
| FR-027 | The "Explore trends" tab must provide a start-period and end-period selector. | Must | Explicit (Addendum) | Named control | Product owner, 2026-08-12 addendum | User can bound the displayed range to any two valid periods present in the data | DR-004 |
| FR-028 | The "Explore trends" tab must display a price time-series chart for the selected area/dataset/period range. | Must | Explicit (Addendum) | Named requirement | Product owner, 2026-08-12 addendum | Chart renders the selected series with periods on one axis and price on the other | FR-025–FR-027 |
| FR-029 | The "Explore trends" tab must display the latest available price for the current selection. | Must | Explicit (Addendum) | Named requirement | Product owner, 2026-08-12 addendum | The most recent non-suppressed price within the selected range is shown, labelled with its period | FR-025–FR-027, ASM-009 |
| FR-030 | The "Explore trends" tab must display the absolute (£) growth over the selected period range. | Must | Explicit (Addendum) | Named requirement | Product owner, 2026-08-12 addendum | Displayed value equals end-period price minus start-period price, per ASM-010's formula | FR-025–FR-027, ASM-010 |
| FR-031 | The "Explore trends" tab must display the percentage growth over the selected period range. | Must | Explicit (Addendum) | Named requirement | Product owner, 2026-08-12 addendum | Displayed value matches (end − start) / start × 100 per ASM-010's formula | FR-025–FR-027, ASM-010 |
| FR-032 | The "Explore trends" tab must display the compound annual growth rate (CAGR) over the selected period range. | Must | Explicit (Addendum) | Named requirement | Product owner, 2026-08-12 addendum | Displayed value matches ASM-010's CAGR formula over the exact elapsed time between the two selected periods | FR-025–FR-027, ASM-010 |
| FR-033 | The "Explore trends" tab must visibly indicate missing/suppressed values within the selected period range rather than omitting or interpolating them silently. | Must | Explicit (Addendum) | Named requirement; extends DR-006/FR-013 to this tab | Product owner, 2026-08-12 addendum | A suppressed period within the selected range is shown as an explicit gap/marker, not skipped or zero-filled | DR-006, FR-013, ASM-011 |
| FR-034 | The "Explore trends" tab must let the user download the displayed series as CSV. | Must | Explicit (Addendum) | Named requirement | Product owner, 2026-08-12 addendum | Downloaded CSV contains exactly the periods/values shown for the current selection | DR-008, ASM-012 |

### 5.6a Dashboard: "Explore trends" tab — premium chart mode (v1.3 addendum)

The product owner's visualisation plan named four required chart types for the dashboard: price trend, new-build premium trend, area comparison, ranking. The first was already fully specified above (`FR-028`); the second was not — premium previously only appeared as a point-in-time "Compare and rank" metric (`FR-036`/`FR-039`). This subsection closes that gap directly from the product owner's own bullets.

| ID | Requirement | Priority | Status | Rationale | Source | Acceptance criteria | Dependencies |
| --- | --- | --- | --- | --- | --- | --- | --- |
| FR-042 | The "Explore trends" tab must provide a premium-mode chart displaying new-build premium over time for the selected area and period range, alongside the existing price-mode chart (`FR-028`). | Must | Explicit | Named chart type in the product owner's visualisation plan ("new-build premium trend"), previously missing from this tab's scope | Product owner, 2026-08-13 visualisation-plan directive | Selecting premium mode for a valid area/period range displays a chart of premium values across the selected periods, using the same area/period selectors already used for price mode | FR-025–FR-027, FR-006, ASM-003 |
| FR-043 | In premium mode, the "Explore trends" tab must let the user switch the displayed unit between percentage and GBP. | Must | Explicit | Named in the product owner's directive ("optionally switch to GBP premium") | Product owner, 2026-08-13 visualisation-plan directive | Toggling the unit control re-renders the same period range's chart using the percentage or GBP premium value per ASM-003's formula, without changing the selected area or period | FR-042, ASM-003 |
| FR-044 | In premium mode, the "Explore trends" tab must label a negative premium value as a discount rather than displaying it as an unlabelled negative number. | Must | Explicit | Named in the product owner's directive ("display negative premium as a discount") | Product owner, 2026-08-13 visualisation-plan directive | A period where the new-build price is lower than the existing price displays with a "discount" label, derived from the same signed premium value already computed — not a separately stored or computed figure | FR-042, ASM-003 |
| FR-045 | In premium mode, the "Explore trends" tab must visibly indicate a period where either source dataset's observation is unavailable for the selected area, consistent with `FR-033`'s missing-value display principle. | Must | Explicit | Named in the product owner's directive ("show missing periods where either source observation is unavailable") | Product owner, 2026-08-13 visualisation-plan directive | A period missing a new-build or existing observation for the selected area shows an explicit gap/marker in the premium chart, not an interpolated or zero value | FR-033, FR-042 |

`FR-034` (CSV download of "the displayed series") already covers the premium-mode chart without a new requirement — it is written generically over whatever series is currently shown, not scoped to price mode specifically.

### 5.7 Dashboard: "Compare and rank" tab — zero OpenAI calls (Addendum)

| ID | Requirement | Priority | Status | Rationale | Source | Acceptance criteria | Dependencies |
| --- | --- | --- | --- | --- | --- | --- | --- |
| FR-035 | The "Compare and rank" tab must provide a multi-area selector. | Must | Explicit (Addendum) | Named control | Product owner, 2026-08-12 addendum | User can select two or more areas at once | DR-005 |
| FR-036 | The "Compare and rank" tab must provide a metric selector, including new-build premium as a selectable metric. | Must | Explicit (Addendum) | Named control; satisfies the separately-listed "new-build premium comparison" bullet via metric choice rather than a fourth view | Product owner, 2026-08-12 addendum | Metric options include at minimum: price, price growth (£/%), CAGR, and new-build premium (£/%) | ASM-003, ASM-013 |
| FR-037 | The "Compare and rank" tab must provide a period selector. | Must | Explicit (Addendum) | Named control | Product owner, 2026-08-12 addendum | User can choose the period or period range the ranking/comparison is computed over | DR-004 |
| FR-038 | The "Compare and rank" tab must produce a top/bottom ranking of the selected areas by the selected metric and period. | Must | Explicit (Addendum) | Named requirement | Product owner, 2026-08-12 addendum | User can view areas ordered ascending or descending by the chosen metric | FR-035–FR-037 |
| FR-039 | The "Compare and rank" tab must support comparing the new-build premium across the selected areas. | Must | Explicit (Addendum) | Named requirement | Product owner, 2026-08-12 addendum | With "new-build premium" selected as the metric, the ranking/table/chart reflects premium values per ASM-003's formula | FR-036, ASM-003 |
| FR-040 | The "Compare and rank" tab must display results as both a table and a Plotly visualisation. | Must | Explicit (Addendum) | Named requirement; names a specific library | Product owner, 2026-08-12 addendum | Results render as a data table and as a Plotly chart for the same selection | FR-035–FR-039, CON-007 |
| FR-041 | The "Compare and rank" tab must let the user download the displayed comparison/ranking as CSV. | Must | Explicit (Addendum) | Named requirement | Product owner, 2026-08-12 addendum | Downloaded CSV contains exactly the areas/values shown for the current selection | DR-008, ASM-012 |

---

## 6. Data requirements

| ID | Requirement | Priority | Status | Rationale | Source | Acceptance criteria | Dependencies |
| --- | --- | --- | --- | --- | --- | --- | --- |
| DR-001 | Source 1: ONS "Median house prices for administrative geographies, newly built dwellings", tab 2b, year-ending-September-2025 edition, at the URL given in the brief. | Must | Explicit | Named source | p.1 | Downloaded file matches the named dataset/edition/tab | — |
| DR-002 | Source 2: ONS "Median house prices for administrative geographies, existing dwellings", tab 2b, year-ending-September-2025 edition, at the URL given in the brief. | Must | Explicit | Named source | p.1 | Downloaded file matches the named dataset/edition/tab | — |
| DR-003 | Dataset scope is limited to the **detached** house type only, per tab 2b of both datasets. | Must | Explicit | Both tabs are explicitly scoped to detached houses | p.1 | Other dwelling types are recognised as out-of-scope by FR-012, not silently answered | FR-012 |
| DR-004 | Time coverage and periodicity (annual vs. rolling "year ending" points, and the earliest year available) is not stated in the brief and must be established by inspecting the downloaded workbook before the data model is finalised. | Must | Derived | Needed to satisfy FR-004/FR-002 correctly | inferred from p.1 dataset naming ("year ending September 2025 edition") | The data model's time dimension is documented alongside a statement of the actual periods found in the workbook | AMB-001 |
| DR-005 | The geography level(s) present under "administrative geographies" (e.g. local authority, region, country) is not stated in the brief and must be established by inspecting the workbook; a name-resolution strategy must map informal place names used in questions (e.g. "Manchester", "Scotland") to the actual geography labels. | Must | Derived | Needed to satisfy FR-002/FR-003 correctly against real column labels | inferred from p.1 dataset naming and example questions (p.1–2) | Every place name used in the illustrative examples resolves to a defined geography row, or the system explains why it cannot | AMB-002 |
| DR-006 | Cells that ONS marks as suppressed/unavailable (e.g. for small sample counts) must be treated as missing data, not as zero or an error. | Must | Derived | Necessary to satisfy FR-013 | inferred from p.1 "Handle missing data" | A query touching a suppressed cell returns an explicit "not available" response | FR-013 |
| DR-007 | ~~Data may be reshaped/transformed at the implementer's discretion (e.g. long format, local database, in-memory structure).~~ **Narrowed by addendum:** the processed data must be a long-format Parquet snapshot (unchanged from the original design), and the runtime query mechanism over it is now specifically mandated as DuckDB via fixed repository methods — see CON-008. Ingestion-time reshaping via Pandas/OpenPyXL remains at the implementer's discretion within that mandate. | Must (was Could) | Explicit, narrowed by Explicit (Addendum v1.2) | Originally left fully open; the v1.2 addendum resolves the runtime-engine part of that openness | p.1, "You may process or transform the data as needed"; narrowed by Product owner, 2026-08-12 addendum (second directive) | Runtime reads happen through DuckDB-backed repository methods, not direct Pandas operations over the loaded dataset | FR-016, CON-008, CON-009 |
| DR-008 | CSV exports from "Explore trends" and "Compare and rank" must contain exactly the rows/columns/values underlying the currently displayed table or chart — same figures, same units — with no additional transformation applied only at export time. | Must | Explicit (Addendum) + Derived | Named requirement, extended to be verifiable per the atomicity rule | Product owner, 2026-08-12 addendum ("CSV download") | A downloaded CSV, re-loaded, numerically matches the on-screen table/chart for the same selection | FR-034, FR-041, ASM-012, NFR-012 |

---

## 7. Interface and integration requirements

| ID | Requirement | Priority | Status | Rationale | Source | Acceptance criteria | Dependencies |
| --- | --- | --- | --- | --- | --- | --- | --- |
| IR-001 | ~~The system must expose a natural-language query interface to the user; the modality (CLI, API, or lightweight UI) is left to the implementer.~~ **Superseded by addendum:** the system must expose a natural-language chat interface as the "Ask the data" tab of the three-tab dashboard mandated by IR-004; only the underlying UI framework/technology remains at the implementer's discretion. | Must | Explicit, narrowed by Explicit (Addendum) | Original brief left modality open; the 2026-08-12 addendum resolves that openness with a specific structural mandate | p.2, "Whether to expose the system through a CLI, API, or lightweight UI"; narrowed by Product owner, 2026-08-12 addendum | A user can submit a question and receive a response inside the "Ask the data" tab specifically, not any arbitrary interface | FR-001, FR-021, IR-004 |
| IR-002 | The system must integrate with the OpenAI API as an external service, with a configurable credential/endpoint. | Must | Explicit | Explicit dependency | p.2–3 | The app connects to the OpenAI API using a supplied key; connection details are not hard-coded | FR-019 |
| IR-003 | The system must not depend on any infrastructure hosted by the submitter; besides the OpenAI API, all runtime dependencies must run locally. | Must | Explicit | Explicit constraint on submission | p.3, "The application should run locally without relying on infrastructure hosted by you" | Running the app with no network access other than to the OpenAI API succeeds for all data-only functionality | CON-001 |
| IR-004 | The application must be structured as a single dashboard with exactly three tabs: "Ask the data", "Explore trends", and "Compare and rank". | Must | Explicit (Addendum) | Named structural mandate | Product owner, 2026-08-12 addendum | The running application shows exactly these three tabs, each matching the scope defined in FR-021–FR-041 | IR-001, CON-006 |
| IR-005 | The "Compare and rank" tab's visualisation must be implemented using Plotly. | Must | Explicit (Addendum) | Names a specific library | Product owner, 2026-08-12 addendum, "Table and Plotly visualisation" | The chart rendered in "Compare and rank" is produced via Plotly | FR-040, CON-007 |

---

## 8. Non-functional requirements

| ID | Requirement | Priority | Status | Rationale | Source | Acceptance criteria | Dependencies |
| --- | --- | --- | --- | --- | --- | --- | --- |
| NFR-001 (Accuracy) | Calculations, filtering, aggregation, and ranking results must be accurate against the source data. | Must | Explicit | Named requirement | p.1 bullets | Spot-checked answers match manually verified figures from the source workbooks | DR-001, DR-002 |
| NFR-002 (Reproducibility) | Identical queries against unchanged data must return consistent results across repeated runs. No numeric target for "consistent" is given. | Must | Explicit | Named requirement; measure is TBD | p.1 bullets | TBD — needs a defined tolerance/determinism mechanism (e.g. deterministic code path for all numeric answers) | AMB — none blocking; Architect consideration below |
| NFR-003 (Grounding) | Quantitative answers must be traceable to the underlying dataset; the system must not present unsupported or fabricated figures. | Must | Derived | Directly reflects the "Grounding" assessment criterion | p.3, "Grounding and handling of unsupported or ambiguous questions" | Every numeric answer can be traced to a specific source cell/computation path in a test or log | FR-012 |
| NFR-004 (Secrets management) | OpenAI API credentials must be supplied via configuration, never hard-coded, and never committed to the submitted source code. | Must | Explicit + Derived | Explicit configurability requirement, extended to standard secret hygiene | p.3, "OpenAI API credentials should be configurable" | `git`/archive history contains no literal API key; README documents the configuration mechanism | FR-019 |
| NFR-005 (Secrets management) | The credential-sharing link embedded in the source brief must not be reproduced in any submitted artefact or documentation. | Must | Analyst rule | Standard secret-handling rule applied to source material | Analyst policy, applied to p.2 | No submitted file contains the literal share link or token | RSK-002 |
| NFR-006 (Cost/API efficiency) | The system must make efficient, deliberate use of the OpenAI API: reasonable model choice, context size, retry policy, and avoidance of unnecessary repeated calls, against a limited but unquantified credit allowance. | Must | Explicit | Named requirement | p.2, "Your solution should make reasonable choices around model usage, context size, retries, repeated requests, and overall API consumption" | Architecture summary states the model(s) used and the reasoning; no retry loop can call the API unboundedly | FR-017, CON-002 |
| NFR-007 (Usability/docs) | Setup and documentation must be concise and practical. | Should | Explicit | Named requirement | p.3, "Keep setup and documentation concise and practical" | README is reviewable in a few minutes and sufficient to run the app without external help | — |
| NFR-008 (Ease of local operation) | The solution must be easy to run locally. No measurable target given. | Must | Explicit | Named assessment criterion | p.3, "How easy the solution is to run locally" | TBD — needs a concrete target (e.g. "N commands from clone to first answered query"); Proposed under Architect considerations | — |
| NFR-009 (Code quality) | Code quality and engineering judgement will be assessed. No specific standard mandated. | Should | Explicit | Named assessment criterion, no threshold given | p.3, "Code quality and engineering judgement" | TBD — no objective pass/fail threshold from the source; left to reviewer judgement | — |
| NFR-010 (Testability) | The system's evaluation approach itself will be assessed for quality, not just presence. | Must | Explicit | Named assessment criterion, distinct from FR-020's presence requirement | p.3, "Evaluation approach" | Test/evaluation suite covers happy paths, edge cases, and negative cases (see §13), not only trivial checks | FR-020 |
| NFR-011 (Availability without the API) | The "Explore trends" and "Compare and rank" tabs must operate correctly using **zero calls to the OpenAI API** — not merely degrade gracefully in its absence, but not invoke it during normal operation — so all of their functionality is available and correct with no API key configured, an unreachable API, or an exhausted credit allowance. | Must | Explicit (Addendum) | Directly reflects the stated rationale ("useful deterministic functionality even if...") | Product owner, 2026-08-12 addendum | With `OPENAI_API_KEY` unset and/or outbound API access blocked, every control and output in FR-025–FR-041 still functions; an automated test exercising these tabs' code paths with the API blocked passes | FR-025–FR-041, BR-003, RSK-005 |
| NFR-012 (Export fidelity) | CSV exports must be reproducible: identical selections in "Explore trends"/"Compare and rank" must always produce byte-for-byte-equivalent (modulo timestamp/filename) CSV content. | Must | Derived | Necessary to make DR-008 verifiable as a quality attribute, not just a one-off data check | Derived from Product owner, 2026-08-12 addendum ("CSV download") | Downloading the same selection twice yields numerically identical CSV content | DR-008, NFR-002 |

**Not addressed by the source and not invented here:** performance/latency targets, concurrency or multi-user capacity, availability/uptime targets, accessibility standard, internationalisation, data retention or query-logging policy, authentication/authorisation, a quantified OpenAI credit ceiling, a hard submission deadline date, target OS/environment. Recorded as open items only where they could materially change scope (§11–§12).

---

## 9. Constraints and mandated decisions

| ID | Constraint | Type | Source |
| --- | --- | --- | --- |
| CON-001 | Application must run fully locally; no submitter-hosted infrastructure. | Genuine constraint | p.1 bullet; p.3 |
| CON-002 | No use of OpenAI models at/above "GPT-5.5", or any "Pro" tier. | Genuine constraint | p.2 |
| CON-003 | OpenAI API usage is expected "where it adds clear value" — not stated as strictly mandatory for every code path (see AMB-003). | Constraint, ambiguous enforcement | p.2 |
| CON-004 | Deliverable must be packaged as a ZIP file or equivalent. | Genuine constraint (submission mechanics) | p.2, "Submission and local operation" |
| CON-005 | Effort guideline: roughly 8–12 hours of focused work. | Constraint of unclear enforcement (advisory vs. hard cap) | p.3 |
| CON-006 | Application must be a single dashboard with exactly three named tabs: "Ask the data", "Explore trends", "Compare and rank" (IR-004). | Genuine constraint (Addendum) | Product owner, 2026-08-12 addendum |
| CON-007 | "Compare and rank" tab's visualisation must specifically use Plotly (IR-005). | Genuine constraint (Addendum) | Product owner, 2026-08-12 addendum, "Table and Plotly visualisation" |
| CON-008 | Runtime analytical queries must be executed by **DuckDB** against the bundled Parquet snapshot, via **fixed, developer-written, parameterised repository methods only**. Pandas and OpenPyXL are scoped to the offline ingestion pipeline (reading/cleaning/validating the raw workbooks and writing the Parquet snapshot) and must not be the runtime query-execution path. The LLM must never generate or execute SQL — it only calls the same typed tool functions as before, which internally call the repository. No separate database service is introduced (DuckDB runs embedded/in-process, consistent with CON-001) and DuckDB is never exposed as a callable surface to the agent directly. Mandated pipeline: **Excel → Pandas/OpenPyXL ingestion → Parquet → DuckDB repository → deterministic analysis tools → agent/dashboard**. | Genuine constraint (Addendum v1.2) | Product owner, 2026-08-12 addendum (second directive) |
| CON-009 | Internal interfaces between the new repository layer and the analysis/domain layer must not leak Pandas-specific types (e.g. a raw `DataFrame`) across the boundary; use domain records or typed Pydantic models instead. Existing interfaces should only be changed where they currently leak such types — this is not a general licence to redesign contracts (see the confirmed scope note in the v1.2 revision history above). | Genuine constraint (Addendum v1.2) | Product owner, 2026-08-12 addendum (second directive) |

Implementation choices the brief explicitly leaves open (recorded here so they are **not** mistaken for constraints): application structure, agent orchestration and conversational-state handling, which permitted OpenAI model(s) to use, and frameworks/libraries generally beyond what CON-008/CON-009 now mandate. Source: p.2, "Implementation" — "There is no requirement to use a particular agent framework, tool pattern, database, or analysis approach." **Note (Addendum):** interface *modality* is no longer open — CON-006/IR-004 now mandate the three-tab dashboard structure and CON-007/IR-005 mandate Plotly for one tab's chart; the underlying UI framework (e.g. which Python web-UI library renders the dashboard) and the "Explore trends" tab's charting library remain at the implementer's discretion, since neither was named by the addendum. **Note (Addendum v1.2):** data storage/processing approach and the runtime request-execution mechanism are also no longer open — CON-008 now mandates DuckDB over Parquet via fixed parameterised methods (the original brief's "generated SQL, Pandas/Python code, predefined tools, or another approach" framing is resolved as: fixed tools, DuckDB-backed, never generated SQL — a further narrowing of what the design had already independently decided via `ADR-001`).

---

## 10. Assumptions and dependencies

| ID | Assumption | Affected requirements | Impact if false | Validation owner |
| --- | --- | --- | --- | --- |
| ASM-001 | A single local user operates the system per session; no multi-user/concurrency support is required. | NFR (capacity not modelled) | Would need session isolation and possibly a different interface design | Challenge issuer |
| ASM-002 | Natural-language queries are in English. | FR-001 | Would need multilingual NL handling | Challenge issuer |
| ASM-003 | "New-build premium" means the relative or absolute difference between the newly built median price and the existing median price for the same area/period (proposed metric: % difference as primary, £ difference as secondary), documented plainly wherever used. | FR-006, example Q3/Q4/Q5 | Computed premium figures could be judged incorrect against a different intended definition | Challenge issuer (AMB-005) |
| ASM-004 | Prices are presented in nominal (as-published) terms; inflation-adjusted ("real terms") comparison is out of scope unless explicitly added with a clearly labelled, separately sourced deflator. | FR-004, FR-009 | Trend/insight answers could be judged incomplete without real-terms framing | Challenge issuer |
| ASM-005 | Relative time expressions ("since 2015", "last decade", "last five years") are anchored to the latest available data point in the year-ending-September-2025 edition. | FR-004, FR-008 | Anchoring choice could shift computed trend windows | Challenge issuer |
| ASM-006 | Informal place names in queries are mapped to the nearest matching administrative-geography label(s) actually present in the data; where a name is genuinely ambiguous across levels, the system surfaces the ambiguity per FR-011 rather than guessing silently. | FR-002, FR-003, DR-005 | Wrong-area answers if mapping is incorrect and unflagged | Implementer, pending workbook inspection |
| ASM-007 | The ONS datasets are licensed (typically Open Government Licence) for reuse in this local, non-commercial technical evaluation without further licensing action. | DR-001, DR-002 | Would require a licensing review before redistribution/processing | Challenge issuer |
| ASM-008 | "Locally runnable" permits outbound calls to the OpenAI API as an external cloud dependency; it does not require full network isolation — "local" describes the application and data layer, not the LLM dependency. | CON-001, IR-002 | Would require an entirely offline/local-model architecture | Challenge issuer |
| ASM-009 | "Latest price" (FR-029) means the most recent non-suppressed price within the user's selected period range for the selected area/dataset (not necessarily the dataset's overall latest period, if the user has narrowed the end period). | FR-029 | A different, unstated definition could show an unexpected value at the range boundary | Product owner |
| ASM-010 | Growth metrics use standard formulas: absolute growth (£) = price(end) − price(start); percentage growth = (price(end) − price(start)) / price(start) × 100; CAGR = (price(end) / price(start))^(1 / years) − 1, where `years` is the exact elapsed time between the two periods' end dates (not a rounded integer year count). | FR-030, FR-031, FR-032 | A different formula convention (e.g. simple annualised growth instead of CAGR) would change displayed figures | Product owner |
| ASM-011 | "Missing-value display" (FR-033) means the UI visibly marks suppressed/unavailable periods within the selected range (e.g. a gap in the chart plus a listed note), rather than silently skipping them or interpolating a value. | FR-033 | Silent interpolation would misrepresent suppressed periods as real data points | Product owner |
| ASM-012 | CSV downloads (FR-034, FR-041) export the currently filtered/selected view shown on screen, not the full underlying dataset. | FR-034, FR-041, DR-008 | A "full dataset export" feature is a distinct, unrequested scope item if this assumption is wrong | Product owner |
| ASM-013 | "New-build premium comparison" (the addendum's Compare-and-rank bullet) is satisfied by including new-build premium (£ and/or %) as one of the options in the tab's metric selector (FR-036), rather than requiring a separate, fourth view. **(v1.3 clarification)** This assumption is scoped to "Compare and rank" specifically not needing a dedicated *ranking* view for premium — it does not extend to, and is not contradicted by, `FR-042`'s premium *trend* chart in "Explore trends", which is a different tab answering a different question (single-area change over time, not multi-area ranking at a point in time). | FR-036, FR-039, FR-042 | A dedicated premium-only sub-view would be a materially larger UI surface if this reading is wrong | Product owner |

**External dependencies**
- Availability and continued accessibility of the two ONS dataset URLs and their "year ending September 2025" edition, tab 2b (owned by ONS, outside this project's control).
- Availability of the OpenAI API and the credential the challenge issuer provides (see RSK-002 for handling of the embedded share link).

---

## 11. Ambiguities, conflicts, and risks

| ID | Description | Impact | Likelihood | Mitigation / required decision | Affected requirements |
| --- | --- | --- | --- | --- | --- |
| AMB-001 | Example Q1 asks for a price "in September 2025" (month-level), but the dataset editions are named by rolling "year ending September" periods; unclear whether tab 2b contains true sub-annual data points or only annual, year-ending figures labelled by end month. | Literal example question may not be answerable as phrased; may need reinterpretation as "for the year ending September 2025." | High (naming pattern strongly suggests annual, rolling periods) | Inspect the downloaded workbook's actual time dimension before finalising the data model; document the resolved interpretation in the README's assumptions | FR-002, FR-004, DR-004 |
| AMB-002 | Exact geography level(s) in "administrative geographies" unknown until the workbook is inspected; example questions use informal city names that may not literally match the data's labels or may match more than one level. | Wrong-area answers, or unresolved lookups, if name matching is naive | Medium–High | Inspect workbook; define an explicit name-resolution/fuzzy-matching strategy; apply FR-011 when a name is ambiguous across levels | FR-002, FR-003, DR-005, ASM-006 |
| — | *Note:* AMB-001 and AMB-002 were subsequently resolved by direct inspection of both downloaded workbooks during system design (see `docs/design/housing-market-insights-agent-system-design.md` §6.1) — confirmed as quarterly rolling year-ending periods, and as 318 England & Wales local authorities only (no Scotland/Northern Ireland). This requirements package's DR-004/DR-005 rows are left as originally written (Derived, pending inspection) to preserve the audit trail of what was known at requirements time; the design document is now the authoritative, confirmed source for these facts. | — | — | — | DR-004, DR-005 |
| AMB-003 | Whether OpenAI API use is mandatory for all natural-language handling, or optional/supplementary ("where it adds clear value"), is not unambiguous; a deterministic parser could in principle handle simple lookups without any model call. | Materially shapes core agent architecture and API cost profile | Medium | Adopt the plain reading: use the model for NL understanding, multi-step planning, and insight generation; use deterministic code for the actual numeric computation (also mitigates RSK-004). Document the choice explicitly. | FR-017, CON-002, CON-003 |
| AMB-004 | The 8–12 hour effort figure could be a soft expectation or a hard cap the assessors will factor into scoring. | Low materiality to design; moderate to project planning/prioritisation | Low | Treat as a target; if time-constrained, prioritise Must-priority requirements first and document any cut scope in README limitations | CON-005 |
| AMB-005 | No definition of "new-build premium" is given in the brief. | Multiple reasonable definitions (£ difference, % difference, ratio) could each be defended; wrong choice risks being marked incorrect | Medium | Choose % difference as the primary reported metric with £ difference shown alongside; state the definition wherever the premium is reported | FR-006, ASM-003 |
| AMB-006 | "Reasonable follow-up questions within the same session" does not define session lifetime (single process run vs. persisted) or maximum follow-up depth. | Could under- or over-build conversational-state persistence | Low–Medium | Scope session state to a single running process/conversation by default; document this limitation | FR-008 |
| RSK-001 | The OpenAI credit allowance is "limited" but unquantified; inefficient design (large re-sent context, unbounded retries, repeated re-derivation) could exhaust it mid-assessment. | Assessment could fail to complete if credits run out | Medium | Minimise tokens per call; cache/summarise conversational state instead of resending full history; bound retries; prefer deterministic code execution over repeated LLM re-computation | NFR-006, CON-002 |
| RSK-002 | A live credential-sharing link was embedded directly in the source brief (page 2). | Credential leakage if reproduced in documentation, code, or version history | Confirmed present in source, not yet a realised leak | Never reproduce the link/token; load credentials only via local, git-ignored configuration (e.g. `.env`) at run time; already redacted in this package | NFR-004, NFR-005 |
| RSK-003 | ONS workbooks (Excel, multiple tabs, header/footnote rows, possible merged cells) are often irregular; misaligned parsing could silently corrupt figures. | Wrong answers that appear confident and correct | Medium | Validate parsed data against several known figures before use; add ingestion-level tests | DR-001, DR-002, NFR-001 |
| RSK-004 | Because "correctness" and "grounding" are both explicitly assessed, free-text LLM answers not tied to a verifiable computation path risk hallucinated figures. | Numerically wrong or unsupported answers presented confidently | Medium | Architect consideration: let the model plan/orchestrate and phrase answers, but compute all numeric results via deterministic code with visible intermediate steps | NFR-001, NFR-003 |
| AMB-007 | The addendum uses "This **must** work without an OpenAI call" for "Explore trends" but "This **should** also work without an OpenAI call" for "Compare and rank" — read literally against this package's own Must/Should convention, that would make one tab's zero-API behaviour optional. | Could under-build zero-API guarantees for "Compare and rank" if read too literally | Low | The surrounding rationale paragraph ("the dashboard provides useful deterministic functionality even if...") applies identically and without qualification to both tabs, and the word "also" reads as "likewise," not as a deliberate priority downgrade. Resolved as: **both tabs are Must** (see NFR-011, FR-025–FR-041). Flagged here rather than silently normalised, per the no-silent-inference rule. | NFR-011, FR-025–FR-041 |
| RSK-005 | If any implementation convenience routes "Explore trends" or "Compare and rank" through the agent/orchestrator (e.g. reusing the same code path as chat "for simplicity"), NFR-011 could be silently violated without an obvious symptom during normal testing (the app would still "work," just not with zero API calls). | Undermines BR-003's core value proposition without being visibly broken | Medium | Add an automated test that exercises both tabs' full functionality with outbound API access blocked/mocked-to-fail and asserts success; treat this as a hard acceptance gate, not a manual spot-check (see §13) | NFR-011, FR-025–FR-041 |
| RSK-006 | The v1.2 addendum introduces DuckDB as a new runtime dependency and a new repository abstraction layer, replacing an already-designed and already-backlogged in-memory Pandas Dataset Store. This adds real implementation and testing surface (a new dependency, a parameterised query per analysis function, DuckDB-specific test fixtures, an audit of existing interfaces for Pandas-type leakage) on top of a schedule already under pressure from the dashboard addendum (design `RSK-006`/backlog `BDR-001`, indicative effort already ≈10–12.5h against the 8–12h guideline). At the current, fixed dataset size (~76,000 numeric cells), this change is not solving an active performance problem — the stated rationale ("efficient analytical joins... clear scaling path for larger datasets") is prospective/architectural, not a fix for a measured bottleneck. The stakeholder has explicitly accepted this trade-off: Must, committed in full now, no pandas-runtime fallback (see the v1.2 revision-history note above). A secondary, low-likelihood setup risk: the `duckdb` Python package ships prebuilt wheels for common platforms/architectures, so installation friction should be minimal, but this has not been verified on every possible target environment. | Further compresses an already-tight schedule; secondary, low risk of install friction on an unusual platform | Medium (schedule), Low (install friction) | Accepted by the stakeholder as a deliberate trade-off, not a design flaw — tracked here for visibility rather than as a call to revert. If schedule pressure becomes acute, the affected components (Dataset Store/repository, and every analysis function that reads through it) are the first place to look for scope reduction, since they are now the most time-costly rework in the backlog | DR-007, CON-008, CON-009, NFR-008 |

No direct conflicts between statements in the source document were identified (no `CFL-###` items raised). Neither addendum conflicts with any prior requirement — each resolves something the original brief had explicitly left open (IR-001 for the dashboard structure; DR-007 for the runtime data engine) rather than contradicting it.

---

## 12. Clarification questions

This is a fixed, non-interactive challenge brief rather than a live stakeholder relationship, so none of the following are treated as blocking — each has a documented working assumption above that allows the design to proceed. They are recorded here in case a real stakeholder becomes available, and are otherwise carried into the README as stated assumptions/limitations.

**Blocking:** none. The brief explicitly permits implementer discretion on approach and states the examples are illustrative, so no open item prevents scoping or starting design.

**Architecture-shaping**
1. What geography level(s) and exact labels does tab 2b actually use, and how should informal place names in questions be resolved to them? *(shapes the data model and query-parsing/name-resolution design — resolving via direct workbook inspection; working assumption: ASM-006/DR-005)*
2. Is OpenAI API use expected for all natural-language handling, or is a hybrid/deterministic layer for simple lookups acceptable, reserving the API for harder analysis and insight generation? *(shapes the core agent architecture and cost profile — working assumption: AMB-003)*
3. Should the submission bundle a pre-processed snapshot of the ONS data, or should the app fetch the raw files as part of setup? *(affects setup reproducibility and offline runnability — working assumption: bundle a processed snapshot for reproducibility, since ONS site availability shouldn't gate assessment)*

**Refinement**
4. What definition of "new-build premium" should be used? *(working assumption: % difference primary, £ difference secondary — ASM-003)*
5. Is the 8–12 hour effort guideline a hard scope cap? *(working assumption: a target, not enforced — Must-priority requirements take precedence if time-constrained)*
6. ~~Is any interface modality (CLI/API/UI) preferred for ease of review?~~ *(Resolved by the 2026-08-12 addendum: a three-tab dashboard is now mandated — IR-004.)*
7. Should CSV exports include periods/rows with suppressed (`"[x]"`) values (explicitly marked), or omit them entirely? *(working assumption: include them with an explicit marker, consistent with FR-013/DR-006/FR-033's "don't silently omit missing data" principle extended to exports — ASM-011)*
8. ~~Is the DuckDB migration a pure internal engine substitution, or does it also reshape existing tool/result contracts?~~ *(Resolved directly by the stakeholder, 2026-08-12: pure internal substitution — contracts unchanged except where a current interface leaks a Pandas-specific type across a boundary, which should be tightened as part of the same change. See CON-009 and the v1.2 revision history note.)*
9. ~~Should the DuckDB migration be a hard Must committed now, or a Should/stretch with a documented pandas fallback given the tight schedule?~~ *(Resolved directly by the stakeholder, 2026-08-12: Must, committed in full now, no fallback path built. The resulting schedule pressure is tracked as an accepted trade-off in RSK-006, not re-litigated here.)*

---

## 13. Acceptance and evaluation outline

**Happy paths**
- Single-area, single-period factual lookup (mirrors example Q1).
- Trend description for an area since a given year (mirrors example Q2).
- New-build premium trend for an area over a decade (mirrors example Q3).
- Top-N ranking by change in a metric over a period (mirrors example Q4).
- Multi-area, multi-dimension comparison — growth and premium together (mirrors example Q5).
- Open-ended "analyse and identify patterns" request returning multiple distinct, data-referenced observations (mirrors example Q6).
- In-session follow-up question resolved using prior conversational context (mirrors the page-2 follow-up example).
- **(Addendum)** "Explore trends": selecting a valid area/dataset/period range shows a correct latest price, absolute growth, percentage growth, and CAGR, plus a matching chart and a CSV download whose content matches what's displayed (FR-025–FR-034, DR-008).
- **(v1.3 addendum)** "Explore trends", premium mode: selecting a valid area/period range shows a correct premium-over-time chart, switches correctly between % and £ units, labels a negative value as a discount, and shows an explicit gap for any period missing a source observation (FR-042–FR-045).
- **(Addendum)** "Compare and rank": selecting several areas, a metric (including new-build premium), and a period produces a correct top/bottom ranking as both table and Plotly chart, with a matching CSV download (FR-035–FR-041).
- **(Addendum)** "Ask the data": an answer involving a ranking or trend renders an accompanying table/chart and an expandable calculation/source-detail view (FR-023, FR-024).

**Edge cases**
- Area name absent, misspelled, or matching more than one geography level.
- Requested time period outside the data's coverage (before earliest year, after latest available period).
- Requested dwelling type not covered by the supplied tabs (e.g. semi-detached, flats).
- A query touching a known missing/suppressed data point in an otherwise valid request.
- A comparison spanning periods available in one dataset but not the other.
- **(Addendum)** "Explore trends": a selected period range includes one or more suppressed periods — the chart/summary shows an explicit gap/marker, not a silent omission or interpolation (FR-033).
- **(Addendum)** "Compare and rank": a selected area has no data for part of the selected period range.

**Negative cases**
- A question unrelated to the datasets (must decline gracefully, not hallucinate).
- A question requiring data not supplied (e.g. rental yields, non-detached prices) — must state the limitation.
- A follow-up referencing a prior answer that does not exist in the current session.

**Non-functional checks**
- Repeated identical queries return identical results (reproducibility, NFR-002).
- No literal API credential appears in the repository or its history (NFR-004/NFR-005).
- A fresh environment can install, configure credentials, run the app, and execute the test/evaluation suite by following the README alone (NFR-007/NFR-008).
- **(Addendum) Zero-API-call check**: with `OPENAI_API_KEY` unset (or outbound API access blocked/mocked-to-fail), "Explore trends" and "Compare and rank" remain fully operable end to end — every control, computed metric, chart, table, and CSV download in FR-025–FR-041 still works — while "Ask the data" shows a clear, non-crashing unavailable-state message (NFR-011, RSK-005). An automated test should assert zero outbound OpenAI calls are made while exercising these two tabs' code paths.
- **(Addendum)** Downloading the same "Explore trends"/"Compare and rank" selection twice produces numerically identical CSV content (NFR-012).

The illustrative questions in the source brief (page 1–2) are explicitly non-exhaustive ("These examples are illustrative rather than a fixed test set" — p.2); the evaluation suite should therefore generalise beyond them rather than hard-coding only these seven questions.

---

## 14. Traceability matrix

| Source reference | Requirement IDs | Coverage note |
| --- | --- | --- |
| p.1, "The task" | BR-001, FR-001 | Fully covered |
| p.1, "Dataset" (both bullets + URLs) | DR-001, DR-002, DR-003, FR-014, FR-015 | Fully covered |
| p.1, "You may process or transform the data as needed" | FR-016, DR-007 | Fully covered |
| p.1, "What the solution should support" (9 bullets) | FR-001–FR-013 (one-to-one, in bullet order) | Fully covered |
| p.1–2, seven illustrative example questions | Cited inline against FR-002–FR-009 and §13 | Treated as illustrative evidence, not an exhaustive requirement or test set, per explicit statement on p.2 |
| p.2, follow-up example | FR-008 | Fully covered |
| p.2, "These examples are illustrative rather than a fixed test set" | §13 note | Directly shapes evaluation-suite design guidance; not a testable requirement itself |
| p.2, "Use of OpenAI APIs" paragraph | FR-017, CON-003, NFR-006 | Covered; enforcement ambiguity recorded as AMB-003 |
| p.2, "Access to GPT-5.5 and above... restricted" | CON-002, FR-018 | Fully covered |
| p.2, "OpenAI API token: [share link]" | RSK-002, NFR-005 | Redacted per secret-handling rule; not reproduced anywhere in this package |
| p.2, "reasonable choices around model usage, context size, retries, repeated requests, and overall API consumption" | NFR-006 | Fully covered |
| p.2, "Implementation" (bulleted list of open decisions) | Recorded in §9 as explicitly non-mandated, and in IR-001 for interface modality | Fully covered as *absence* of constraint, not as requirements |
| p.2–3, "Submission and local operation" (contents + README coverage) | BR-002, IR-003, FR-020, NFR-007 | Fully covered |
| p.3, "OpenAI API credentials should be configurable" | FR-019, NFR-004 | Fully covered |
| p.3, "Keep setup and documentation concise and practical" | NFR-007 | Fully covered |
| p.3, "8–12 hours of focused work" | CON-005, AMB-004 | Covered; enforcement ambiguity recorded |
| p.3, "What we'll assess" (8 bullets) | NFR-001, NFR-003, NFR-009, NFR-006, NFR-008, NFR-010, plus cross-references to FR-002–FR-013 for "correctness"/"insight quality" | Fully covered — each assessment criterion is mapped to at least one requirement or explicitly flagged as a quality attribute without a stated measurable threshold |
| Product owner, 2026-08-12 addendum — "three tabs: Ask the data / Explore trends / Compare and rank" | BR-003, IR-004, FR-021, FR-025, FR-035 | Fully covered |
| Product owner, 2026-08-12 addendum — "Ask the data" bullets (chat, example prompts, NL questions, follow-up, tables/charts, expandable detail) | FR-001, FR-008, FR-021–FR-024 | Fully covered |
| Product owner, 2026-08-12 addendum — "Explore trends" bullets + "This must work without an OpenAI call" | FR-025–FR-034, NFR-011, DR-008 | Fully covered |
| Product owner, 2026-08-12 addendum — "Compare and rank" bullets + "This should also work without an OpenAI call" | FR-035–FR-041, NFR-011, IR-005, CON-007, DR-008 | Fully covered; wording asymmetry ("should") addressed in AMB-007 rather than silently normalised |
| Product owner, 2026-08-12 addendum — "This distinction matters because... No API key... API unavailable... credit allowance has expired" | BR-003, NFR-011, RSK-005 | Fully covered |
| Product owner, 2026-08-12 addendum — "The task breakdown should include dedicated dashboard work rather than treating charts as a side effect of chat responses" | Routed to §15 Architect handoff (not a system requirement — a delivery-planning instruction for the system designer's increment structure) | Covered as a directive to the next stage, not encoded as an `FR/NFR` since it governs how work is sequenced, not what the system must do |
| Product owner, 2026-08-12 addendum (second directive) — "Runtime analytics using embedded DuckDB over bundled Parquet... At runtime, DuckDB queries the processed Parquet snapshot through fixed, parameterised repository methods. No model-generated SQL is permitted." | CON-008, DR-007 (narrowed) | Fully covered |
| Product owner, 2026-08-12 addendum (second directive) — "Excel → Pandas ingestion → Parquet → DuckDB repository → deterministic tools" (pipeline diagram) | CON-008 | Fully covered |
| Product owner, live clarification — scope confirmation ("pure internal data-engine substitution... keep all existing agent tool signatures, Pydantic input/output schemas... unchanged unless a contract currently exposes Pandas-specific types... introduce a repository abstraction... prefer domain records or typed Pydantic models at repository boundaries") | CON-009 | Fully covered |
| Product owner, live clarification — testing approach ("Tests should use an in-memory DuckDB connection or temporary Parquet fixtures") and exclusions ("Do not create a separate database service or expose DuckDB directly to the agent") | CON-008 (exclusions folded in); testing approach routed to §15 Architect handoff for the design's §13 test strategy | Fully covered |
| Product owner, live clarification — priority ("Must — commit fully now") | RSK-006 (records the accepted schedule trade-off) | Fully covered |
| Product owner, 2026-08-13 visualisation-plan directive — "premium mode: select an area, show premium percentage over time, optionally switch to GBP premium, display negative premium as a discount, show missing periods where either source observation is unavailable" | FR-042, FR-043, FR-044, FR-045 | Fully covered — backfills a gap the system designer surfaced rather than one this package originated |

No mandatory source statement was left unmapped.

---

## 15. Architect handoff

**Capabilities the design must provide**
- A three-tab dashboard (IR-004): "Ask the data", "Explore trends", "Compare and rank" — each a dedicated, first-class part of the design, not an incidental rendering path off the chat feature (see the delivery-planning note below).
- A natural-language front door inside the "Ask the data" tab that classifies/plans incoming questions across: factual lookup, comparison, trend, ranking, cross-dataset premium analysis, multi-step analysis, open-ended insight generation, and in-session follow-up resolution (FR-001–FR-009, FR-021–FR-024), rendering tables/charts and an expandable calculation/source-detail view per answer, not text-only output.
- A local data layer holding both ingested ONS tab-2b datasets (detached, newly built and existing) with a resolved geography and time dimension (FR-014–FR-016, DR-004–DR-006).
- A computation layer that performs all filtering/aggregation/ranking/premium/growth/CAGR arithmetic deterministically and traceably, independent of whether an LLM is used to plan or phrase an answer (NFR-001–NFR-003, RSK-004) — **and reachable directly by the UI, with no dependency on the agent/LLM layer, for "Explore trends" and "Compare and rank"** (NFR-011). This is the specific architectural implication of the addendum: those two tabs are not "the chat feature with graceful API-outage fallback" — they must not call the OpenAI API in their normal code path at all.
- Explicit handling paths for: ambiguous input, unanswerable/out-of-scope input, and missing/suppressed data — each must produce a visible, non-fabricated response (FR-011–FR-013), including the dashboard-specific missing-value display in "Explore trends" (FR-033).
- A conversational-state mechanism scoped at least to a single running session (FR-008).
- CSV export from "Explore trends" and "Compare and rank" that is fidelity-preserving and reproducible (DR-008, NFR-012).
- A small, generalising evaluation suite covering the happy-path/edge/negative/non-functional cases in §13 (FR-020, NFR-010), including an automated, API-blocked test proving NFR-011's zero-call guarantee (RSK-005) rather than relying on manual spot-checking.
- **(v1.3)** A premium-mode chart in "Explore trends" (FR-042–FR-045): new-build premium over time for a single selected area, a %/£ unit toggle, discount labelling for negative premium (derived from the sign at render time, not a separate field), and the same missing-period gap treatment FR-033 already established for price mode. The system designer has already produced a compatible design for this (reusing the existing `get_premium_series` repository method via a new `premium_series` function) — these FRs formalise that work's traceability rather than requesting a new design.

**Architecture-shaping constraints**
- Fully local execution except for outbound calls to the OpenAI API (CON-001, IR-003, ASM-008) — and note that two of the three dashboard tabs must make *no* such calls at all (NFR-011).
- No OpenAI model at/above "GPT-5.5" and no "Pro" tier (CON-002/FR-018) — model choice must be validated against whatever the issuer's provisioned credential actually permits.
- Unquantified but limited OpenAI credit budget — design must default to lean token/context usage and bounded retries (NFR-006, RSK-001).
- Two ONS Excel workbooks (tab 2b each) of unconfirmed internal structure — ingestion must be validated, not assumed (DR-004, DR-005, RSK-003).
- Three-tab dashboard structure and Plotly for "Compare and rank" are now mandated technology/structure choices, not open implementation decisions (CON-006, CON-007, IR-004, IR-005).

**Delivery-planning note for the system designer (Addendum, verbatim instruction from the product owner):** *"The task breakdown should include dedicated dashboard work rather than treating charts as a side effect of chat responses."* This is not a system requirement but a direct instruction about how implementation increments should be structured — it should be reflected explicitly in the design's delivery plan (e.g. as its own increment(s) covering "Explore trends" and "Compare and rank" as first-class deliverables with their own exit criteria), rather than folded into or implied by chat-feature work.

**v1.2 addendum — runtime data-engine change, with explicit instructions for the system designer:**

The existing design (`ADR-005`: in-memory Pandas over a Parquet snapshot, no database engine) is **superseded** by CON-008/CON-009. This is a confirmed, Must-priority, stakeholder-directed change, not a proposal to weigh — the design must be updated to reflect it, not asked to reconsider it. Specifically, the system designer should:

1. **Replace `ADR-005`** with a new decision record for "DuckDB repository over bundled Parquet" — Context: CON-008/CON-009; Options considered should include the SQLite/DuckDB/pandas-in-memory comparison the original `ADR-005` already made, updated to show why DuckDB is now selected; Consequences should note the added implementation/testing surface and that this is a prospective/architectural choice rather than a fix for a measured performance problem at the current ~76,000-cell data volume (see RSK-006) — state this honestly rather than overstating necessity.
2. **Introduce a repository abstraction** between the domain/analysis layer (the existing fixed tool functions) and DuckDB, so the analysis layer is not coupled directly to the database — likely a renamed/redefined evolution of the previously-designed "Dataset Store" component.
3. **Audit every existing interface that currently returns or passes a Pandas `DataFrame`** across a component boundary (most likely the previous Dataset Store's accessor methods) and tighten it to a typed record or Pydantic model at the repository boundary, per CON-009 — this is the one place the "contracts stay unchanged" confirmation has a real exception, and it should be called out explicitly wherever it applies.
4. **Preserve every other existing contract unchanged**: agent tool signatures, Pydantic result schemas (`GrowthMetricsResult`, `RankingResult`, `PremiumResult`, etc.), orchestration behaviour, conversation-state contracts, and UI contracts. The confirmed scope of this change is the data-access layer only.
5. **Update the data architecture, code/repository structure, dependency list** (add `duckdb`, retain `pyarrow`), **security/threat model** (note that all queries are fixed, developer-written, and parameterised — no injection surface from user input or model output, since the LLM never constructs or sees SQL text), **test strategy** (tests should use an in-memory DuckDB connection or temporary Parquet fixtures, not the full bundled dataset, per the stakeholder's explicit instruction), and **delivery plan/backlog** accordingly — the previously-backlogged Dataset Store task and every analysis-function task that reads through it will need their implementation notes revised to reference repository method calls instead of direct Pandas operations.
6. **Do not** introduce a separate database service, and **do not** expose DuckDB as a callable surface to the agent directly — both explicitly excluded by the stakeholder; DuckDB remains embedded/in-process, consistent with `CON-001`.
7. Confirm the pipeline diagram in any updated architecture summary reads exactly: **Excel → Pandas/OpenPyXL ingestion → Parquet → DuckDB repository → deterministic analysis tools → agent/dashboard**.

**Key data flows and external boundaries**
- Inbound: two ONS workbook downloads (offline, one-time or setup-time ingestion) → local data store/structure.
- Runtime: user NL question → (local) interpretation/planning, optionally aided by the OpenAI API → deterministic local computation against the ingested data → response, optionally aided by the OpenAI API for phrasing/insight synthesis.
- External boundary: only the OpenAI API is a live network dependency at query time; no other externally hosted service is permitted (IR-003).

**Quality attributes that drive trade-offs**
- Grounding/accuracy vs. fluency: prefer deterministic computation with LLM-assisted planning/phrasing over free-form LLM arithmetic (NFR-001, NFR-003, RSK-004).
- API cost efficiency vs. conversational richness: session-state design should avoid resending full history on every turn (NFR-006, RSK-001).
- Ease of local setup vs. ingestion robustness: bundling a validated, pre-processed data snapshot trades a small amount of "freshness" for materially better reproducibility and reviewer setup time (Architect consideration below).

**Unresolved decisions and affected components**
- Actual geography/time structure of tab 2b in both workbooks — affects the data-ingestion component and the name-resolution component (AMB-001, AMB-002; must be resolved by direct inspection before the data model is finalised).
- Degree of OpenAI reliance for NL understanding vs. deterministic parsing — affects the query-orchestration component and cost model (AMB-003).
- Definition of "new-build premium" — affects the computation/premium component and any insight templates referencing it (AMB-005).

**Architect considerations (recommendations, not requirements)**
- *Proposed:* separate an "interpretation/planning" concern (LLM-assisted) from a "computation" concern (deterministic code operating over the ingested data), so every numeric figure in a response has a non-LLM-generated, testable origin.
- *Proposed:* bundle a validated, processed snapshot of both datasets in the submission (rather than requiring a live ONS download at setup) to maximise reproducibility and reviewer ease-of-run, while documenting the source URLs and edition for provenance.
- *Proposed:* keep session state as a compact structured summary (e.g. last-referenced areas/periods/metrics) rather than replaying full conversation text on each OpenAI call, to control token/cost growth (RSK-001).
- *Proposed:* define a single canonical "new-build premium" formula (percentage difference, existing as base) up front and reuse it everywhere the concept appears, to keep answers internally consistent.

Do not treat the above four bullets as requirements — they are recommendations for the system designer to accept, adapt, or reject.
