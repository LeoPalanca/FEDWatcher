# FedWatcher

Agentic sentiment analysis and monetary policy nowcasting for Federal Reserve documents.

> MSc Economics - Programming in Finance II, 2026  
> USI Universita della Svizzera italiana  
> Repository: https://github.com/LeoPalanca/FEDWatcher

Project workflow: [AGENTS.md](AGENTS.md)

## Project Overview

FedWatcher is an agentic financial application that monitors Federal Reserve documents,
extracts monetary-policy tone with an LLM, combines that tone with macroeconomic data from
FRED, and exposes nowcast results through a FastAPI backend and dashboard.

The project answers:

> Can LLM-extracted Fed communication tone, combined with CPI and unemployment data, help
> estimate the likely direction and size of the next FOMC policy-rate move?

The implementation target is intentionally lean:

- Federal Reserve documents first, ECB later only as a stretch goal.
- Three runtime agents: `MonitorAgent`, `AnalystAgent`, `StrategistAgent`.
- FastAPI backend for dashboard/API access.
- SQLite database for reproducible local development.
- FRED macro data, especially `CPILFESL` and `UNRATE`.
- Ordered or multinomial model over rate-move buckets.

## Current Status

This repository is still in active development.

Implemented:

- Initial SQL schema and database scripts.
- Agent/contributor workflow in `AGENTS.md`.
- SQLite-native `MonitorAgent` in `agents/monitor.py` for source-aware live document
  fetching from the official Fed site or FakeFed.
- Static FakeFed fixture site in `fakefed/` for end-to-end fake statement tests.
- Static FedWatcher brief homepage/dashboard in `fedwatcher/` for the first
  `fedwatcher.ellep.it` deployment. It includes a full SQLite explorer backed by the
  FastAPI API.
- Read-only FastAPI backend in `app/` exposing SQLite tables, documents, and a dashboard
  snapshot through `/api/tables`, `/api/tables/{table}`, `/api/documents`, and
  `/api/snapshot`.
- Historical official Fed document backfill in `scripts/inital_data_download.py` using
  FedTools.
- Direct Federal Reserve historical-page backfill in
  `agents/monitor-fed-historical-pages.py` for ranges FedTools misses, including
  2015-2020 statements and minutes.
- FRED monthly macro/rate ingestion in `sources/fred.py` and `scripts/backfill_fred.py`:
  stores `CPILFESL`, `UNRATE`, and monthly-average `DGS2` in `macro_data`.
- `AnalystAgent` in `agents/analyst.py`:
  - document segmentation: splits FOMC statements and minutes into weighted sections (`forward_guidance`, `inflation`, `labor_market`, `general` / `policy_discussion`) for downstream tone scoring.
  - LLM tone scoring calls the OpenRouter API (`OPENROUTER_API_KEY`) with the segmented sections and extracts a numeric `tone_score` in `[-1.0, +1.0]` (dovish → hawkish), plus `overall_tone`, `inflation_assessment`, `labor_market_assessment`, `forward_guidance`, `key_phrases`, and `confidence`.
  - Returns a typed `ToneResult` with a `to_db_row()` helper ready for the `sentiment` table.
- `Dual-model AnalystAgent` in `agents/dual_model_analyst.py` *(testing)*: calls two OpenRouter models and averages their results; writes to `sentiment2`.
- `DeepSeek AnalystAgent` in `agents/analyst_ds.py` *(testing)*: single-model pipeline using `deepseek/deepseek-v4-flash`; writes to `sentiment3` using `documents.processed3`.
- `StrategistAgent` in `agents/strategist.py`:
  - EWMA time-aware tone smoothing (`S_t = α_t·tone_t + (1−α_t)·S_{t−1}`,
    `α_t = 1 − exp(−ln(2)/21 · Δt)`), where Δt is the calendar-day gap between FOMC releases.
  - Ordered-probit nowcast over rate-move buckets `{-50, -25, 0, +25, +50}` bps,
    with latent index `η = β_S·S + β_π·(CPI_yoy − 2) + β_u·(U − U_baseline)`.
    Default `β` and cut points are sign-coherent placeholders pending calibration on historical FOMC outcomes.
  - Tone-implied next-meeting rate `current_rate + Σ_k P(Y=j_k)·j_k / 100`.
  - Divergence signal vs the market proxy (`DGS2`), with `aligned` / `hawkish` / `dovish` classification.
  - Returns a typed `PolicySignal` with a `to_db_row()` helper ready for the `signals` table.

Planned next:

- Add FRED ingestion for policy-rate target series such as `DFEDTARU`, `DFEDTARL`,
  and `DFF`.
- Calibrate `StrategistAgent` β coefficients and cut points on historical FOMC rate-move outcomes.
- Build the dashboard against the FastAPI API, including an admin-protected FakeFed fetch
  action that appends synthetic documents to the same document feed.
- Add backtesting and academic documentation.

## Architecture

FedWatcher keeps FastAPI because it was covered in class, gives the web app a clean backend
interface, and can satisfy an additional project criterion if we add authentication or rate
limits.

The simplification is not "no API". The simplification is:

- keep FastAPI as the backend boundary;
- keep three real runtime agents;
- remove the separate `PublisherAgent`;
- avoid PostgreSQL, Docker, OIS curves, and ECB support until the Fed MVP works;
- make the finance model and data sources reproducible.

```text
Fed website -> MonitorAgent -> documents table
                              |
                              v
                         AnalystAgent
                              |
                              v
FRED API -> macro_data/market_data -> StrategistAgent -> signals table
                              |
                              v
                         FastAPI backend
                              |
                              v
                           Dashboard
```

FastAPI is not the agent orchestrator. The current API is read-only and exposes stored data
and model results. Controlled pipeline actions can be added later behind authentication. The
pipeline itself remains a plain Python workflow that is easy to test.

## Runtime Agents

### MonitorAgent

Finds new Fed documents and stores their metadata/raw text.

Responsibilities:

- Scrape official Federal Reserve or FakeFed FOMC pages.
- Detect statements, minutes, and related documents.
- Deduplicate documents by date/type.
- Fetch HTML text and store document records in SQLite.

### AnalystAgent

Uses an LLM as a text-analysis model. **Implemented.**

Responsibilities:

- Segment the document into weighted sections (`forward_guidance`, `inflation`, `labor_market`, `general` / `policy_discussion`).
- Call an OpenRouter-hosted LLM through the OpenAI client with the segmented sections.
- Extract a numeric `tone_score` in `[-1.0, +1.0]` (dovish → hawkish) plus `overall_tone`, `inflation_assessment`, `labor_market_assessment`, `forward_guidance`, `key_phrases`, and `confidence`.
- Return a typed `ToneResult`; call `result.to_db_row()` to get a dict ready for the `sentiment` table.

Three pipeline variants exist for testing purposes: `analyst.py` (single model, `sentiment`), `dual_model_analyst.py` (two-model average, `sentiment2`), and `analyst_ds.py` (DeepSeek only, `sentiment3`).

Future work: calibrate the section weights against historical 2-year Treasury yield reactions around FOMC releases. The current weights are transparent runtime assumptions; a proper event-study calibration should use daily or intraday 2Y yield changes and compare the empirical contribution of `forward_guidance`, `inflation`, `labor_market`, and `general` / `policy_discussion`.

### StrategistAgent

Combines text tone, macro data, and market proxies. **Implemented.**

Responsibilities:

- Smooth tone through time.
- Build model features from CPI and unemployment.
- Estimate probabilities over rate-move buckets.
- Compute tone-implied policy rate.
- Compare against market-rate proxies.
- Generate dashboard-ready signals.

No separate `PublisherAgent` is planned. Persisting outputs and serving them to the dashboard
is application plumbing handled by `pipeline.py`, `db.py`, and FastAPI.

## Current And Planned File Structure

```text
FEDWatcher/
├── AGENTS.md
├── README.md
├── .env.example
├── requirements.txt
│
├── fakefed/                    # Static synthetic Fed website fixture
├── fedwatcher/                 # Static homepage/dashboard placeholder
├── deploy/
│   └── nginx/                  # Nginx templates for VM deployment
├── docs/
│   └── fakefed_deployment.md
│
├── app/                        # FastAPI backend
│   ├── db.py                   # SQLite connection helpers
│   └── main.py                 # read-only API app
│
├── agents/
│   ├── monitor.py              # MonitorAgent
│   ├── analyst.py              # AnalystAgent (single model, sentiment)
│   ├── dual_model_analyst.py   # AnalystAgent (two-model average, sentiment2) - testing
│   ├── analyst_ds.py           # AnalystAgent (DeepSeek only, sentiment3) - testing
│   └── strategist.py           # planned StrategistAgent
│
├── sources/
│   ├── fed.py                  # planned Fed scraping helpers
│   └── fred.py                 # FRED fetch + transformations
│
├── models/                     # planned nowcast model package
│   └── nowcast.py              # ordered/multinomial model
│
├── fedwatcher/                 # current static dashboard frontend
│
├── db.py                       # planned SQLite interface
├── pipeline.py                 # planned workflow runner
└── scripts/
    ├── init_db.py
    ├── backfill_fred.py
    └── inital_data_download.py # historical FedTools document backfill
```

## Data Sources

### Federal Reserve Documents

Primary source:

- https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm

Core document types:

| Document | Use |
|---|---|
| FOMC statements | Direct policy action and forward guidance |
| FOMC minutes | Rich detail on committee reasoning |
| Chair press conferences | Optional extension for additional tone |
| Speeches | Optional extension; lower signal strength than statements/minutes |

### FRED Macro and Rates Data

Core series:

| Series | Meaning | Use |
|---|---|---|
| `CPILFESL` | Core CPI index | Inflation predictor |
| `UNRATE` | Unemployment rate | Labor-market predictor |
| `DFEDTARU` | Fed target range upper bound | Current target rate |
| `DFEDTARL` | Fed target range lower bound | Current target rate |
| `DFF` | Effective Fed Funds Rate | Realized short rate |
| `DGS2` | 2-year Treasury yield | Market-rate proxy |
| `SOFR` | Secured Overnight Financing Rate | Short-rate proxy |

Optional later:

| Series | Meaning | Use |
|---|---|---|
| `CPIAUCSL` | Headline CPI index | Robustness check |
| `NROU` or `NROUST` | Natural unemployment estimate | Unemployment gap |

Core transformations:

```text
core_cpi_yoy = 100 * (CPILFESL_t / CPILFESL_t-12 - 1)
core_cpi_mom = 100 * (CPILFESL_t / CPILFESL_t-1 - 1)
unemployment_rate = UNRATE_t
unemployment_gap = UNRATE_t - NROU_t       # optional once NROU/NROUST is added
policy_midpoint = (DFEDTARU_t + DFEDTARL_t) / 2
market_policy_gap = DGS2_t - policy_midpoint
```

Implemented FRED storage starts at `1994-01` to align with the earliest Fed statement
history. The fetcher pulls one hidden prior year of `CPILFESL` so stored `1994` rows can
still calculate `core_cpi_yoy`; those lookback rows are not stored in `macro_data`.
`UNRATE` is monthly, and `DGS2` is fetched from FRED at monthly frequency using average
aggregation, so the 2-year Treasury yield is aligned to the same monthly row. The fetcher
creates a continuous monthly index and fills only isolated one-month gaps by averaging the
previous and following month. Filled fields are recorded in `interpolated_fields`; longer
gaps stay null and are highlighted as missing in the dashboard table.

| Column | Source | Frequency |
|---|---|---|
| `observation_month` | derived from FRED date | monthly key, `YYYY-MM` |
| `core_cpi_index` | `CPILFESL` | monthly |
| `core_cpi_mom` | `CPILFESL` transform | monthly percent change |
| `core_cpi_yoy` | `CPILFESL` transform | year-over-year percent change |
| `unemployment_rate` | `UNRATE` | monthly percentage rate |
| `us2y_yield` | `DGS2` | monthly average percentage yield |
| `interpolated_fields` | fetcher metadata | comma-separated fields filled from adjacent months |

All data transformations should keep units explicit. Interest-rate and inflation variables
must consistently use either percentage points or basis points.

## Finance Model

The presentation slide uses a latent-variable/multinomial framework. FedWatcher should follow
that design rather than a binary hike/not-hike model.

Target outcome:

```text
j in {-50, -25, 0, +25, +50}
```

where `j` is the next FOMC rate move in basis points.

Model:

```text
Y*_t = X_t beta + epsilon_t

P(Y_t = j | X_t) =
    exp(X_t beta_j) / (1 + sum_k exp(X_t beta_k))

Y*_t = beta_1 S_t + beta_2 Delta CPI_t + beta_3 Ugap_t + epsilon_t
```

Tone smoothing:

```text
S_t = alpha_t * tone_score_t + (1 - alpha_t) * S_{t-1}
alpha_t = 1 - exp(-lambda * Delta t)
lambda = ln(2) / h
h = 21
```

Interpretation:

- `S_t` is the time-weighted smoothed Fed tone score.
- `Delta CPI_t` is based on `CPILFESL`.
- `Ugap_t` starts as `UNRATE_t`; later it can become `UNRATE_t - NROU_t`.
- The model should be ordered logit/probit or multinomial logit.

Tone-implied rate:

```text
tone_implied_rate_t =
    current_rate_t + sum_j P(Y_t = j) * magnitude_j
```

This avoids the earlier binary-logit problem where `P(cut)` was needed but not estimated.

## FastAPI Scope

Implemented read-only endpoints:

```text
GET /api/health
GET /api/tables
GET /api/tables/{table}?limit=100&offset=0&search=...
GET /api/documents?limit=100&offset=0&search=...
GET /api/snapshot
```

Planned controlled endpoints:

```text
POST /api/pipeline/run
GET  /api/agents/status
```

If we want the course criterion "own API with authentication or rate limits", add one small
control:

- Bearer token for `POST /pipeline/run`; or
- simple request rate limiting.

## Dashboard

The dashboard should use FastAPI as its data source and show:

- current tone gauge;
- Fed tone time series;
- core CPI and unemployment context;
- next-meeting rate-move probabilities;
- tone-implied rate vs market-rate proxy;
- divergence history;
- document explorer with extracted evidence phrases.

The current frontend in `fedwatcher/` is static HTML/CSS/JS, but its data source is the
FastAPI backend. It does not use stale database JSON snapshots.

## Database

Target database: SQLite.

Tables:

```text
documents
sentiment
macro_data
market_data
signals
```

This is enough to demonstrate SQL, ETL, joins, and reproducible local development without
requiring a database server.

## Installation

Prerequisites:

- Python 3.10+
- FRED API key
- OpenRouter API key (`OPENROUTER_API_KEY`) for LLM sentiment extraction.

Setup:

```bash
git clone https://github.com/LeoPalanca/FEDWatcher.git
cd FEDWatcher

python -m venv venv
source venv/bin/activate

pip install -r requirements.txt
cp .env.example .env
```

The `.env.example` contains the target SQLite/API/FRED variables plus legacy MySQL fields
kept for old prototype compatibility.

For AI calls:

```text
OPENROUTER_API_KEY=
```

## Usage

Current prototype commands:

```bash
python scripts/init_db.py
python scripts/inital_data_download.py
python agents/monitor-fed-historical-pages.py --start-year 2015 --end-year 2020
python scripts/backfill_fred.py
python scripts/backfill_fred.py --dry-run
uvicorn app.main:app --reload
python agents/monitor.py
python agents/monitor.py --refresh-macro
python agents/analyst.py --limit 5
python agents/dual_model_analyst.py --limit 5   # two-model average, testing
python agents/analyst_ds.py --limit 5            # DeepSeek only, testing
```

FakeFed test target:

```bash
FED_BASE_URL=https://fakefed.ellep.it python agents/monitor.py
```

The FastAPI backend is read-only for now, so it does not change agent execution. Agents keep
writing to SQLite; the API exposes those stored rows to the dashboard.

## Deployment

The VPS deployment helper is `scripts/deploy.sh`. It can sync the static dashboard,
FastAPI backend code, and FakeFed static fixture site either over SSH or directly from the
VM with `--local`.

Expected VPS layout:

```text
/var/www/fedwatcher   static public dashboard
/var/www/fakefed      synthetic FakeFed fixture site
/opt/FEDWatcher       FastAPI/backend working tree
```

Common commands:

```bash
bash scripts/deploy.sh --frontend --reload-nginx
bash scripts/deploy.sh --backend --restart
bash scripts/deploy.sh --all --restart --reload-nginx
bash scripts/deploy.sh --all --local --restart --reload-nginx
bash scripts/deploy.sh --all --dry-run
```

Backend deploys preserve local runtime state by excluding `.env`, SQLite databases,
virtual environments, logs, caches, `fedwatcher/`, and `fakefed/`. The production FastAPI
service template expects `/opt/FEDWatcher/.venv` and is defined in
`deploy/fedwatcher-api.service`.

## Development Workflow

Use [AGENTS.md](AGENTS.md) as the contributor and coding-agent rulebook.

Important rules:

- Use `/Users/leonardo/FEDWatcher` as the active working copy.
- Update this README whenever architecture, setup, usage, data sources, or model assumptions
  change.
- Keep `/Users/leonardo/FEDWatcher_Hide` as local-only teacher/course context; do not commit it.
- Do not commit `.env`, `.DS_Store`, local databases, generated outputs, or secrets.
- Commit regularly with meaningful messages.

## FakeFed Test Site

`fakefed/` is a synthetic static website that preserves the Fed URL paths needed by the
scraper. It is used to test fake statements without touching the live Federal Reserve
website.

Important URLs:

- `https://fakefed.ellep.it/monetarypolicy/fomccalendars.htm`
- `https://fakefed.ellep.it/newsevents/pressreleases/monetary20260507a.htm`

Deployment notes and the Nginx template are in
`docs/fakefed_deployment.md` and `deploy/nginx/fakefed.ellep.it.conf`.

The final dashboard should support two modes:

- clean app mode using the official Federal Reserve source;
- educational demo mode with admin-only FakeFed controls for writing synthetic statements.

For the next dashboard integration, the top-right FakeFed control should authenticate an
admin, trigger the backend to run `MonitorAgent` against `https://fakefed.ellep.it`, append
the fetched synthetic documents to the same document feed as official Fed documents, and
label every FakeFed row as synthetic/test content. If LLM credentials are configured, the
backend can run analysis after fetch; otherwise the fetched rows remain pending analysis.

The mode split is documented in `docs/dashboard_modes.md`.

## FedWatcher Homepage Dashboard

`fedwatcher/` contains a static brief homepage/dashboard for the first public deployment at
`https://fedwatcher.ellep.it`. It presents the project concept, current placeholder signal
panels, AnalystAgent section-weight legend, macro context, rate-move buckets, document feed,
a full SQLite table explorer, and the planned admin-only educational FakeFed mode.

This static page is temporary, but it now reads live database-backed JSON from the FastAPI
backend. The old static database snapshots were removed so the website and SQLite database
cannot drift apart.

## Course Criteria Coverage

| Criterion | How FedWatcher satisfies it |
|---|---|
| Advanced LLM | `AnalystAgent` extracts structured Fed policy tone |
| Advanced ML/statistics | ordered/multinomial nowcast over rate-move buckets |
| Real-time/data processing | scheduled Fed monitoring and FRED refresh pipeline |
| Non-trivial database | SQLite with multiple related tables |
| Own API | FastAPI backend for dashboard and pipeline access |
| Advanced visualization | dashboard for tone, macro variables, probabilities, divergence, documents |
| Agentic project | `AGENTS.md`, runtime agents, regular GitHub process, AI-authored PR |

## Academic Documentation Plan

The PDF submission should cover:

- project plan;
- project diary;
- GitHub process and AI-agent usage;
- data sources and citations;
- economics and finance model;
- implementation choices;
- sample results and interpretation;
- limitations and lessons learned.

External code, datasets, tutorials, and AI tools must be cited.

## References To Add

The final academic documentation should cite the relevant central-bank communication and
monetary-policy literature. Candidate references:

- Gurkaynak, Sack, and Swanson on monetary policy surprises.
- Lucca and Trebbi on automated FOMC communication measurement.
- Hansen and McMahon on Fed communication text analysis.
- Shapiro and Wilson on text-based measures of central-bank communication.
- Taylor on policy-rule benchmarks.

The literature section should be checked carefully before final submission so every citation
supports the claim being made.
