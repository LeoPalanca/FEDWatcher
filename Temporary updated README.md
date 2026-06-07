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

The implementation is intentionally lean:

- Federal Reserve FOMC statements only; ECB as a stretch goal only.
- Three runtime agents: `MonitorAgent`, `AnalystAgent` (weight-aware), `StrategistAgent`.
- FastAPI backend for dashboard/API access.
- SQLite database for reproducible local development.
- FRED macro data: `CPILFESL`, `UNRATE`, `DGS2`, and policy rate.
- Ordered probit model over rate-move buckets.

## Current Status

### Implemented

- SQL schema and database init in `scripts/init_db.py`.
- Agent/contributor workflow in `AGENTS.md`.
- `MonitorAgent` in `agents/monitor.py`: scrapes the official Fed FOMC calendar, classifies and deduplicates documents, fetches HTML text, stores records in SQLite. Runs automatically via cron every 5 minutes on the server.
- Historical Fed document backfill in `agents/monitor-fed-historical-pages.py` for ranges FedTools misses (2015–2020 statements).
- Historical backfill via FedTools in `scripts/inital_data_download.py`.
- FRED monthly macro ingestion in `sources/fred.py` and `scripts/backfill_fred.py`: stores `CPILFESL`, `UNRATE`, monthly-average `DGS2`, and `policy_rate` in `macro_data`.
- **Weight-aware `AnalystAgent`** in `agents/w_agent.py` (production pipeline):
  - Loads per-`doc_type` section weights dynamically from the `weights` table.
  - Segments FOMC statements into weighted sections (`forward_guidance`, `inflation`, `labor_market`, `general`).
  - Calls DeepSeek via OpenRouter (`OPENROUTER_API_KEY`) for per-section tone scores in `[-1.0, +1.0]`.
  - Computes `tone_score` as a weighted average in Python — weights can be updated in the DB without re-running the LLM.
  - Writes to `sentiment_w` and marks `documents.processed_w = 1`.
  - Runs automatically via cron every 5 minutes with `flock` to prevent overlapping runs. Logs to `logs/w_agent.log`.
- Section weight calibration framework: `section_weight_calibration` and `section_weight_calibration_observations` tables store calibration runs and per-observation data for backtesting weight adjustments.
- `fomc_policy_moves` table stores actual historical FOMC rate decisions in basis points, used as ground truth for calibration.
- `llm_section_tone_cache` table caches LLM responses by model, document, section, and text hash to avoid redundant API calls.
- Static FakeFed fixture site in `fakefed/` for end-to-end scraper tests without hitting the live Fed website.
- Read-only FastAPI backend in `app/` exposing SQLite tables and a dashboard snapshot through `/api/tables`, `/api/tables/{table}`, `/api/documents`, and `/api/snapshot`.
- Static homepage/dashboard in `fedwatcher/` deployed at `fedwatcher.ellep.it`, backed by the FastAPI API (no stale JSON snapshots).
- `StrategistAgent` in `agents/strategist.py`: EWMA tone smoothing + ordered-probit nowcast over `{-50, -25, 0, +25, +50}` bps buckets. Computes tone-implied next rate and divergence vs the 2Y yield proxy. **DB write not yet wired** — `signals` table is currently empty.

### Legacy / Testing Variants

These agents were used during development and testing. The production pipeline uses `w_agent.py`.

- `agents/analyst.py` — single-model analyst, writes to `sentiment`.
- `agents/dual_model_analyst.py` — two-model average, writes to `sentiment2`.
- `agents/analyst_ds.py` — DeepSeek-only single model, writes to `sentiment3`.

### Planned Next

- Wire `StrategistAgent` to write signals to the `signals` table.
- Calibrate `StrategistAgent` β coefficients and cut points on historical FOMC outcomes using `fomc_policy_moves`.
- Build backtesting pipeline comparing tone-implied moves against actual `fomc_policy_moves`.
- Add academic documentation and final dashboard polish.

## Architecture

```text
Fed website ──► MonitorAgent ──► documents table
                                       │
                                       ▼
                               w_agent (AnalystAgent)
                               weights table ──────────┘
                                       │
                                       ▼
                               sentiment_w table
                                       │
FRED API ──► macro_data ──────────────►│
                                       ▼
                               StrategistAgent ──► signals table (pending)
                                       │
                                       ▼
                               FastAPI backend
                                       │
                                       ▼
                                   Dashboard
```

FastAPI is not the agent orchestrator. It is a read-only boundary that exposes stored data to the dashboard. The pipeline is a plain Python/cron workflow.

## Runtime Agents

### MonitorAgent (`agents/monitor.py`)

- Scrapes the official Fed FOMC calendar page.
- Classifies links as `statement` (minutes, press conferences, and implementation notes are skipped).
- Deduplicates by date/type, preferring HTML over PDF.
- Fetches HTML text and upserts records into `documents`.
- Optionally refreshes FRED macro data with `--refresh-macro`.

### AnalystAgent — weight-aware (`agents/w_agent.py`)

- Loads section weights from the `weights` table (falls back to hardcoded defaults if the table is empty).
- Segments the document by sentence classification into weighted sections.
- Calls DeepSeek via OpenRouter for per-section scores.
- Computes `tone_score` as a weighted average, excluding zero-scored sections to avoid neutral bias on short documents.
- Writes to `sentiment_w`; marks `processed_w = 1` on success.

### StrategistAgent (`agents/strategist.py`)

- EWMA tone smoothing: `S_t = α_t · tone_t + (1 − α_t) · S_{t-1}`, `α_t = 1 − exp(−ln(2)/21 · Δt)`.
- Ordered-probit nowcast with latent index `η = β_S·S + β_π·(CPI_yoy − 2) + β_u·(U − U_baseline)`.
- Tone-implied next rate: `current_rate + Σ_k P(Y=j_k) · j_k / 100`.
- Divergence vs `DGS2` market proxy.
- Default β and cut points are sign-coherent placeholders; calibration on historical FOMC outcomes is the next step.

## Automation

All agents run on the Bavaria server via cron. Scripts are in `scripts/`.

| Script | Agent | Cron | Log |
|---|---|---|---|
| `run_fed_documents_update.sh` | MonitorAgent | every 5 min | `logs/fed_documents_update.log` |
| `run_w_agent.sh` | w_agent | every 5 min | `logs/w_agent.log` |
| `run_fred_backfill.sh` | FRED ingestion | manual / periodic | `logs/fred_backfill.log` |
| `run_fakefed_documents_update.sh` | MonitorAgent (FakeFed) | manual | `logs/fakefed_documents_update.log` |

`run_w_agent.sh` uses `flock` to prevent overlapping runs when a batch takes longer than the cron interval.

## File Structure

```text
FEDWatcher/
├── AGENTS.md
├── README.md
├── .env.example
├── requirements.txt
│
├── agents/
│   ├── monitor.py                   # MonitorAgent (live Fed + FakeFed scraping)
│   ├── monitor-fed-historical-pages.py  # historical backfill for 2015-2020
│   ├── w_agent.py                   # weight-aware AnalystAgent (production)
│   ├── strategist.py                # StrategistAgent (EWMA + ordered probit)
│   ├── analyst.py                   # legacy single-model analyst → sentiment
│   ├── dual_model_analyst.py        # legacy two-model analyst → sentiment2
│   └── analyst_ds.py                # legacy DeepSeek-only analyst → sentiment3
│
├── app/                             # FastAPI backend
│   ├── db.py                        # SQLite connection helpers
│   └── main.py                      # read-only API
│
├── sources/
│   └── fred.py                      # FRED fetch + transformations
│
├── scripts/
│   ├── init_db.py                   # create tables
│   ├── backfill_fred.py             # FRED historical backfill
│   ├── inital_data_download.py      # FedTools historical document backfill
│   ├── run_fed_documents_update.sh  # cron: MonitorAgent with logging
│   ├── run_w_agent.sh               # cron: w_agent with logging + flock
│   ├── run_fred_backfill.sh         # FRED refresh
│   ├── run_fakefed_documents_update.sh
│   ├── update_fed_documents.py
│   ├── update_fakefed_documents.py
│   ├── clean_db.py
│   └── test_db.py
│
├── fakefed/                         # synthetic Fed fixture site
├── fedwatcher/                      # static dashboard (fedwatcher.ellep.it)
├── deploy/
│   └── nginx/                       # Nginx config templates
└── docs/
    └── fakefed_deployment.md
```

## Database

SQLite database: `fedwatcher.db`.

| Table | Description |
|---|---|
| `documents` | All Fed documents: URL, type, release date, raw text, processing flags |
| `sentiment_w` | Per-section tone scores from the weight-aware analyst (production) |
| `weights` | Section weights per `doc_type`, loaded dynamically by `w_agent.py` |
| `fomc_policy_moves` | Actual historical FOMC rate decisions in basis points |
| `llm_section_tone_cache` | Cached LLM responses by model, document, section, text hash |
| `section_weight_calibration` | Calibration run results: weight, correlation, sample count |
| `section_weight_calibration_observations` | Per-document observations for each calibration run |
| `macro_data` | Monthly FRED macro: core CPI, unemployment, 2Y yield, policy rate |
| `market_data` | Market snapshots: SOFR, OIS rates, 2Y yield |
| `signals` | StrategistAgent output: tone-implied rate, divergence, narrative (pending) |
| `sentiment` | Legacy: single-model analyst output |
| `sentiment2` | Legacy: dual-model analyst output |
| `sentiment3` | Legacy: DeepSeek-only analyst output |

## Data Sources

### Federal Reserve Documents

- Source: https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm
- Types ingested: FOMC statements only.
- Press conferences and implementation notes are excluded.

### FRED Macro Data

| Series | Meaning | Stored as |
|---|---|---|
| `CPILFESL` | Core CPI index | `core_cpi_index`, `core_cpi_mom`, `core_cpi_yoy` |
| `UNRATE` | Unemployment rate | `unemployment_rate` |
| `DGS2` | 2-year Treasury yield (monthly avg) | `us2y_yield` |
| `DFF` | Effective Fed Funds Rate | `policy_rate` |

## Finance Model

### Tone Smoothing (EWMA)

```text
S_t = α_t · tone_t + (1 − α_t) · S_{t-1}
α_t = 1 − exp(−ln(2)/21 · Δt)
```

where Δt is the calendar-day gap between FOMC releases.

### Ordered-Probit Nowcast

```text
η_t = β_S · S_t + β_π · (CPI_yoy_t − 2) + β_u · (U_t − U_baseline)
P(Y_t = j_k) = Φ(c_k − η_t) − Φ(c_{k−1} − η_t)
j ∈ {−50, −25, 0, +25, +50} bps
```

### Tone-Implied Rate

```text
tone_implied_rate_t = current_rate_t + Σ_k P(Y_t = j_k) · j_k / 100
```

## FastAPI Endpoints

```text
GET /api/health
GET /api/tables
GET /api/tables/{table}?limit=100&offset=0&search=...
GET /api/documents?limit=100&offset=0&search=...
GET /api/snapshot
```

## Installation

Prerequisites: Python 3.10+, FRED API key, OpenRouter API key.

```bash
git clone https://github.com/LeoPalanca/FEDWatcher.git
cd FEDWatcher
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Set in `.env`:

```text
OPENROUTER_API_KEY=
FRED_API_KEY=
```

## Usage

```bash
# Initialize database
python scripts/init_db.py

# Historical document backfill
python scripts/inital_data_download.py
python agents/monitor-fed-historical-pages.py --start-year 2015 --end-year 2020

# FRED macro backfill
python scripts/backfill_fred.py

# Run agents manually
python agents/monitor.py
python agents/monitor.py --refresh-macro
python agents/w_agent.py --db fedwatcher.db --limit 5

# FakeFed test
FED_BASE_URL=https://fakefed.ellep.it python agents/monitor.py

# FastAPI backend
uvicorn app.main:app --reload
```

## FakeFed Test Site

`fakefed/` is a synthetic static website that mirrors Fed URL structure for scraper testing.

- `https://fakefed.ellep.it/monetarypolicy/fomccalendars.htm`
- `https://fakefed.ellep.it/newsevents/pressreleases/monetary20260507a.htm`

Deployment notes: `docs/fakefed_deployment.md`, `deploy/nginx/fakefed.ellep.it.conf`.

## Course Criteria Coverage

| Criterion | How FedWatcher satisfies it |
|---|---|
| Advanced LLM | weight-aware `AnalystAgent` extracts per-section structured Fed policy tone |
| Advanced ML/statistics | ordered-probit nowcast over rate-move buckets with EWMA tone smoothing |
| Real-time/data processing | cron-scheduled Fed monitoring and FRED refresh pipeline |
| Non-trivial database | SQLite with multiple related tables, calibration history, LLM cache |
| Own API | FastAPI read-only backend for dashboard access |
| Advanced visualization | dashboard for tone, macro, probabilities, divergence, document explorer |
| Agentic project | `AGENTS.md`, runtime agents, regular GitHub process, AI-authored contributions |

## References

- Gürkaynak, Sack, and Swanson on monetary policy surprises.
- Lucca and Trebbi on automated FOMC communication measurement.
- Hansen and McMahon on Fed communication text analysis.
- Shapiro and Wilson on text-based measures of central-bank communication.
- Taylor on policy-rule benchmarks.
