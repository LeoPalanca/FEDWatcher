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

- Initial Fed document monitor in `agents/monitor.py`.
- Initial script for fetching raw HTML document text in `scripts/fetch_document_text.py`.
- Initial SQL schema and database scripts.
- Dashboard mockup in `dashboard_mockup.html`.
- Agent/contributor workflow in `AGENTS.md`.
- Static FakeFed fixture site in `fakefed/` for end-to-end fake statement tests.
- Static FedWatcher brief homepage/dashboard in `fedwatcher/` for the first
  `fedwatcher.ellep.it` deployment.
- `AnalystAgent` document segmentation in `agents/analyst.py`: splits FOMC statements and minutes into weighted sections (`forward_guidance`, `inflation`, `labor_market`, `general` / `policy_discussion`) for downstream tone scoring.
- `AnalystAgent` LLM tone scoring in `agents/analyst.py`: calls the Anthropic API (`claude-sonnet-4-6`) with the segmented sections and extracts a numeric `tone_score` in `[-1.0, +1.0]` (dovish → hawkish), plus `overall_tone`, `inflation_assessment`, `labor_market_assessment`, `forward_guidance`, `key_phrases`, and `confidence`. Returns a typed `ToneResult` with a `to_db_row()` helper ready for the `sentiment` table.

Planned next:

- Migrate the prototype database layer from MySQL-style scripts to SQLite.
- Add FRED ingestion for `CPILFESL`, `UNRATE`, policy-rate series, and market-rate proxies.
- Implement `StrategistAgent` (EWMA tone smoothing, multinomial nowcast, tone-implied rate, divergence signals).
- Add FastAPI endpoints.
- Build the dashboard against the FastAPI API.
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
FRED API -> macro/rates tables -> StrategistAgent -> signals table
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

- Scrape Federal Reserve FOMC pages.
- Detect statements, minutes, and related documents.
- Deduplicate documents by date/type.
- Store document records in the database.

### AnalystAgent

Uses an LLM as a text-analysis model. **Implemented.**

Responsibilities:

- Segment the document into weighted sections (`forward_guidance`, `inflation`, `labor_market`, `general` / `policy_discussion`).
- Call the Anthropic API (`claude-sonnet-4-6`) with the segmented sections.
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

## Proposed File Structure

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
├── api/
│   └── main.py                 # FastAPI app
│
├── agents/
│   ├── monitor.py              # MonitorAgent
│   ├── analyst.py              # AnalystAgent
│   └── strategist.py           # StrategistAgent
│
├── sources/
│   ├── fed.py                  # Fed scraping helpers
│   └── fred.py                 # FRED fetch + transformations
│
├── models/
│   └── nowcast.py              # ordered/multinomial model
│
├── dashboard/
│   └── app.py                  # dashboard frontend
│
├── db.py                       # SQLite interface
├── pipeline.py                 # workflow runner
└── scripts/
    ├── init_db.py
    ├── backfill_fred.py
    └── backfill_documents.py
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

The existing `dashboard_mockup.html` is a visual prototype, not the final dashboard.

## Database

Target database: SQLite.

Tables:

```text
documents
sentiment
macro_data
market_data
signals
model_runs
```

This is enough to demonstrate SQL, ETL, joins, and reproducible local development without
requiring a database server.

## Installation

Prerequisites:

- Python 3.10+
- FRED API key
- Anthropic API key for LLM sentiment extraction (`ANTHROPIC_API_KEY`)

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
needed by the original prototype scripts.

## Usage

Current prototype commands:

```bash
python agents/monitor.py
python scripts/fetch_document_text.py
```

FakeFed test target:

```bash
FED_BASE_URL=https://fakefed.ellep.it python agents/monitor.py
FED_BASE_URL=https://fakefed.ellep.it python scripts/fetch_document_text.py
```

Target commands:

```bash
python scripts/init_db.py
python scripts/backfill_fred.py
python scripts/backfill_documents.py
python pipeline.py
uvicorn api.main:app --reload
streamlit run dashboard/app.py
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

The mode split is documented in `docs/dashboard_modes.md`.

## FedWatcher Homepage Dashboard

`fedwatcher/` contains a static brief homepage/dashboard for the first public deployment at
`https://fedwatcher.ellep.it`. It presents the project concept, current placeholder signal
panels, macro context, rate-move buckets, document feed, and the planned admin-only
educational FakeFed mode.

This static page is temporary. The final version should read from the FastAPI backend and
replace placeholder values with database-backed documents, FRED data, sentiment results,
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
