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
  `fedwatcher.ellep.it` deployment. It currently reads static JSON from
  `fedwatcher/assets/data.json` and `fedwatcher/assets/documents.json`.
- Historical official Fed document backfill in `scripts/inital_data_download.py` using
  FedTools.
- FRED monthly macro/rate ingestion in `sources/fred.py` and `scripts/backfill_fred.py`:
  stores `CPILFESL`, `UNRATE`, and monthly-average `DGS2` in `macro_data`.
- `AnalystAgent`  in `agents/analyst.py`:
  - document segmentation: splits FOMC statements and minutes into weighted sections (`forward_guidance`, `inflation`, `labor_market`, `general` / `policy_discussion`) for downstream tone scoring.
  - LLM tone scoring calls the OpenRouter API key (`OPENROUTER_API_KEY`) with the segmented sections and extracts a numeric `tone_score` in `[-1.0, +1.0]` (dovish → hawkish), plus `overall_tone`, `inflation_assessment`, `labor_market_assessment`, `forward_guidance`, `key_phrases`, and `confidence`.
  Returns a typed `ToneResult` with a `to_db_row()` helper ready for the `sentiment` table.

Planned next:

- Add FRED ingestion for policy-rate target series such as `DFEDTARU`, `DFEDTARL`,
  and `DFF`.
- Implement `StrategistAgent` (EWMA tone smoothing, multinomial nowcast, tone-implied rate, divergence signals).
- Add FastAPI endpoints.
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

FastAPI is not the agent orchestrator. It exposes stored data, model results, and controlled
pipeline actions. The pipeline itself remains a plain Python workflow that is easy to test.

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

### StrategistAgent

Combines text tone, macro data, and market proxies.

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
├── api/                        # planned FastAPI backend
│   └── main.py                 # FastAPI app
│
├── agents/
│   ├── monitor.py              # MonitorAgent
│   ├── analyst.py              # AnalystAgent
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

Implemented FRED storage uses one row per month in `macro_data`, because `CPILFESL` and
`UNRATE` are monthly series. `DGS2` is fetched from FRED at monthly frequency using average
aggregation, so the 2-year Treasury yield is aligned to the same monthly row:

| Column | Source | Frequency |
|---|---|---|
| `observation_month` | derived from FRED date | monthly key, `YYYY-MM` |
| `core_cpi_index` | `CPILFESL` | monthly |
| `core_cpi_mom` | `CPILFESL` transform | monthly percent change |
| `core_cpi_yoy` | `CPILFESL` transform | year-over-year percent change |
| `unemployment_rate` | `UNRATE` | monthly percentage rate |
| `us2y_yield` | `DGS2` | monthly average percentage yield |

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

Minimum endpoints:

```text
GET  /health
GET  /documents
GET  /sentiment/latest
GET  /sentiment/history
GET  /macro/latest
GET  /signals/latest
GET  /signals/history
POST /pipeline/run
```

Optional endpoint:

```text
GET /agents/status
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

The current static frontend in `fedwatcher/` is temporary. It reads static JSON files today;
the final version should read from the FastAPI backend.

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
python scripts/backfill_fred.py
python agents/monitor.py
```

FakeFed test target:

```bash
FED_BASE_URL=https://fakefed.ellep.it python agents/monitor.py
```

Target commands:

```bash
python scripts/init_db.py
python pipeline.py
uvicorn api.main:app --reload
```

These target commands will become valid as the corresponding modules are implemented.

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
panels, macro context, rate-move buckets, document feed, and the planned admin-only
educational FakeFed mode.

This static page is temporary. It currently reads `fedwatcher/assets/data.json` and
`fedwatcher/assets/documents.json`. The final version should read from the FastAPI backend
and replace placeholder values with database-backed documents, FRED data, sentiment results,
and model probabilities.

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
