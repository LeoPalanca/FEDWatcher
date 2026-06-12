# FedWatcher

Agentic sentiment analysis and monetary policy nowcasting for Federal Reserve documents.

> MSc Economics - Programming in Finance II, 2026  
> USI Universita della Svizzera italiana  
> Repository: https://github.com/LeoPalanca/FEDWatcher

Project workflow: [AGENTS.md](AGENTS.md)  
User guide (install · usage · API): [USER_GUIDE.md](USER_GUIDE.md)

## Project Overview

FedWatcher is an agentic financial application that monitors Federal Reserve documents,
extracts monetary-policy tone with an LLM, combines that tone with macroeconomic data from
FRED, and exposes nowcast results through a FastAPI backend and dashboard.

The project answers:

> Can LLM-extracted Fed communication tone, combined with CPI and unemployment data, help
> estimate the likely direction and size of the next FOMC policy-rate move?

The implementation is intentionally lean:

- Federal Reserve FOMC statements only; ECB as a stretch goal only.
- Runtime agents: `MonitorFedAgent`, `MonitorFakeFedAgent`, `MonitorFredAgent`, weight-aware
  `AnalystAgent`, `StrategistAgent`.
- FastAPI backend for dashboard/API access, including accountability and AI-generated
  narrative endpoints.
- SQLite database for reproducible local development.
- FRED macro data: `CPILFESL`, `UNRATE`, `DGS2`, and `FEDFUNDS`.
- Ordered probit model over rate-move buckets, with a live accountability/backtest endpoint.

## Current Status

### Implemented

- SQL schema in `db/schema.sql` (single source of truth) and `scripts/init_db.py` to create
  `fedwatcher.db` from it.
- `run.py`: local setup/launcher CLI (`setup`, `dev`, `analyze`, `macro`, `pipeline`,
  `weights`). Writes `.env`, initializes the database, configures `AnalystAgent` section
  weights, and can run the Monitor → Analyst → Strategist loop plus a local dashboard proxy.
- Agent/contributor workflow in `AGENTS.md`.
- `MonitorFedAgent` in `agents/monitor_fed.py`: single entrypoint with three modes
  (`--mode recent|calendar|historical`):
  - `recent` (cron default): FedTools 90-day lookback for FOMC statements.
  - `calendar`: live scrape of the official FOMC calendar page.
  - `historical`: FedTools full-history backfill plus `fomchistorical{year}.htm` scraping
    for 1994–2014.
  Classifies, deduplicates (HTML over PDF), and stores FOMC statements only — minutes,
  press conferences, and implementation notes are filtered out. Runs via
  `scripts/run_fed_documents_update.sh` every 5 minutes on the server.
- `MonitorFakeFedAgent` in `agents/monitor_fakefed.py`: scrapes the FakeFed FOMC calendar
  (`https://fakefed.ellep.it`) and ingests synthetic statements released after 2026-05-21
  into the same `documents` table, for scraper/analyst testing without touching the live
  Fed site.
- `MonitorFredAgent` in `agents/monitor_fred.py`: fetches `CPILFESL`, `UNRATE`,
  monthly-average `DGS2`, and `FEDFUNDS`, aligns them into monthly rows, fills isolated
  one-month gaps by averaging adjacent months, and creates one forward proxy month after
  the latest real observation. Upserts into `macro_data`; proxy values are overwritten once
  real FRED data arrives.
- **Weight-aware `AnalystAgent`** in `agents/w_agent.py` (production pipeline):
  - Loads per-`doc_type` section weights dynamically from the `weights` table.
  - Segments FOMC statements into weighted sections (`forward_guidance`, `inflation`,
    `labor_market`, `general`).
  - Calls DeepSeek via OpenRouter (`OPENROUTER_API_KEY`) for per-section tone scores in
    `[-1.0, +1.0]`.
  - Computes `tone_score` as a weighted average in Python — weights can be updated in the
    DB without re-running the LLM.
  - Writes to `sentiment_w` and marks `documents.processed_w = 1`.
  - Runs automatically via cron every 5 minutes with `flock` to prevent overlapping runs.
    Logs to `logs/w_agent.log`.
- `StrategistAgent` in `agents/strategist.py`: EWMA tone smoothing + ordered-probit nowcast
  over `{-50, -25, 0, +25, +50}` bps buckets. Computes the tone-implied next rate and the
  divergence vs the FEDFUNDS-based market proxy, and replays the full EWMA chain to write
  one row per new document into the `signals` table. Runs via `scripts/run_strategist.sh`
  every 5 minutes with `flock`. Logs to `logs/strategist.log`.
- FastAPI backend in `app/`:
  - `app/main.py`: `/api/health`, `/api/tables`, `/api/tables/{table}`, `/api/documents`,
    and `/api/snapshot`, plus admin-protected `POST`/`DELETE /api/fakefed/statements` for
    publishing or removing synthetic FakeFed statements (guarded by
    `FAKEFED_PUBLISH_PASSWORD`).
  - `app/accountability.py`: `/api/accountability` — track-record metrics (hit rate, MAE
    in bps, Brier score) comparing `StrategistAgent` signals against realized FOMC outcomes
    inferred from FEDFUNDS, excluding intermeeting/emergency moves.
  - `app/narrative.py`: `/api/narrative` — AI-generated hero and §02 Breakdown copy
    (DeepSeek via OpenRouter) layered over deterministically computed fields, cached per
    latest `signals` row id, with a deterministic fallback when no API key or signal is
    available.
- Static FakeFed fixture site in `fakefed/` (4 synthetic statements published so far:
  March, May ×2, and June 2026) for end-to-end scraper tests without hitting the live Fed
  website.
- Static homepage/dashboard in `fedwatcher/` deployed at `fedwatcher.ellep.it`, backed
  entirely by the FastAPI API (no stale JSON snapshots).
- Automated test suite in `tests/`: `test_api.py`, `test_fred_source.py`,
  `test_monitor_fakefed.py`.
- Deployment tooling: `scripts/deploy.sh` (frontend/backend/FakeFed sync over SSH or
  `--local`), `scripts/setup_deploy_user.sh` (one-time VPS deploy-user provisioning),
  `deploy/fedwatcher-api.service` (systemd unit), `deploy/nginx/`.
- Academic documentation source in `academic_doc/` (LaTeX project plan, diary, methods,
  results, lessons, AI acknowledgement; `make` builds `main.pdf`).

### Legacy / Testing Variants

These agents were used during development and testing. The production pipeline uses
`w_agent.py`.

- `agents/analyst.py` — single-model analyst, writes to `sentiment`. Cron wrapper:
  `scripts/run_analyst.sh`.
- `agents/dual_model_analyst.py` — two-model average, writes to `sentiment2`.
- `agents/analyst_ds.py` — DeepSeek-only single model, writes to `sentiment3`.

### Planned Next

- Calibrate `StrategistAgent` β coefficients and cut points on historical FOMC outcomes —
  `/api/accountability` now provides the hit-rate/MAE/Brier baseline to calibrate against.
- Fill in the `%% TODO` markers in `academic_doc/sections/03_results.tex` with backtest
  results and the divergence case study.
- Resolve the empty `dashboard/` placeholder directory (remove it or define its purpose).
- ECB support remains a stretch goal only.

## Architecture

```text
Fed website  ──► MonitorFedAgent     ──┐
FakeFed site ──► MonitorFakeFedAgent ──┤──► documents table
                                        │
                                        ▼
                                w_agent (AnalystAgent)
                                weights table ─────────────┘
                                        │
                                        ▼
                                sentiment_w table
                                        │
FRED API ──► MonitorFredAgent ──► macro_data ──────────────┐
                                        │                   │
                                        ▼                   │
                                StrategistAgent ◄───────────┘
                                        │
                                        ▼
                                  signals table
                                        │
                                        ▼
                  FastAPI backend (main · accountability · narrative)
                                        │
                                        ▼
                                    Dashboard
```

FastAPI is not the agent orchestrator. It is mostly a read-only boundary that exposes
stored data to the dashboard (plus the FakeFed admin write/delete endpoints). The pipeline
itself is a plain Python/cron workflow.

## Runtime Agents

### MonitorFedAgent (`agents/monitor_fed.py`)

- Single entrypoint with `--mode recent|calendar|historical`.
- `recent` (cron default): FedTools 90-day lookback.
- `calendar`: live scrape of the official FOMC calendar page.
- `historical`: FedTools full-history backfill plus `fomchistorical{year}.htm` scraping for
  1994–2014.
- Classifies links as `statement` (minutes, press conferences, and implementation notes are
  skipped).
- Deduplicates by date/type, preferring HTML over PDF, and upserts records into `documents`.

### MonitorFakeFedAgent (`agents/monitor_fakefed.py`)

- Scrapes the FakeFed FOMC calendar at `https://fakefed.ellep.it`.
- Ingests statements released after 2026-05-21 into the same `documents` table used by
  `MonitorFedAgent`, so the analyst/strategist pipeline can be exercised end-to-end on
  synthetic data.

### MonitorFredAgent (`agents/monitor_fred.py`)

- Fetches `CPILFESL`, `UNRATE`, monthly-average `DGS2`, and `FEDFUNDS` from FRED.
- Aligns series into monthly rows starting `1994-01`, fills isolated one-month gaps by
  averaging adjacent months, and creates exactly one forward proxy month after the latest
  real FRED month.
- Upserts into `macro_data`; proxy values are non-permanent and are overwritten once real
  FRED data is published.

### AnalystAgent — weight-aware (`agents/w_agent.py`)

- Loads section weights from the `weights` table (falls back to hardcoded defaults if the
  table is empty).
- Segments the document by sentence classification into weighted sections.
- Calls DeepSeek via OpenRouter for per-section scores.
- Computes `tone_score` as a weighted average, excluding zero-scored sections to avoid
  neutral bias on short documents.
- Writes to `sentiment_w`; marks `processed_w = 1` on success.

### StrategistAgent (`agents/strategist.py`)

- EWMA tone smoothing: `S_t = α_t · tone_t + (1 − α_t) · S_{t-1}`,
  `α_t = 1 − exp(−ln(2)/21 · Δt)`.
- Ordered-probit nowcast with latent index
  `η = β_S·S + β_π·(CPI_yoy − 2) + β_u·(U − U_baseline)`.
- Tone-implied next rate: `current_rate + Σ_k P(Y=j_k) · j_k / 100`.
- Divergence vs the FEDFUNDS-based market proxy (`DGS2`).
- Replays the full EWMA chain over `sentiment_w` history and writes one `signals` row per
  document that doesn't already have one.
- Default β and cut points are sign-coherent placeholders; calibration against
  `/api/accountability` is the next step.

## Automation

All agents run on the Bavaria server via cron. Scripts are in `scripts/`.

| Script | Agent | Cron | Log |
|---|---|---|---|
| `run_fed_documents_update.sh` | MonitorFedAgent (`--mode recent`) | every 5 min | `logs/fed_documents_update.log` |
| `run_w_agent.sh` | w_agent (AnalystAgent) | every 5 min, `flock` | `logs/w_agent.log` |
| `run_strategist.sh` | StrategistAgent | every 5 min, `flock` | `logs/strategist.log` |
| `run_fred_backfill.sh` | MonitorFredAgent | manual / periodic | `logs/fred_backfill.log` |
| `run_fakefed_documents_update.sh` | MonitorFakeFedAgent | manual, `flock` | `logs/fakefed_documents_update.log` |
| `run_analyst.sh` | legacy `analyst.py` | manual (legacy) | `logs/analyst.log` |

`run_w_agent.sh` and `run_strategist.sh` use `flock` to prevent overlapping runs when a
batch takes longer than the cron interval.

## File Structure

```text
FEDWatcher/
├── AGENTS.md
├── README.md
├── run.py                            # local setup/launcher CLI
├── .env.example
├── requirements.txt
│
├── agents/
│   ├── monitor_fed.py               # MonitorFedAgent (recent/calendar/historical)
│   ├── monitor_fakefed.py           # MonitorFakeFedAgent (FakeFed ingestion)
│   ├── monitor_fred.py              # MonitorFredAgent (FRED macro ingestion)
│   ├── w_agent.py                   # weight-aware AnalystAgent (production)
│   ├── strategist.py                # StrategistAgent (EWMA + ordered probit)
│   ├── analyst.py                   # legacy single-model analyst → sentiment
│   ├── dual_model_analyst.py        # legacy two-model analyst → sentiment2
│   └── analyst_ds.py                # legacy DeepSeek-only analyst → sentiment3
│
├── app/                              # FastAPI backend
│   ├── db.py                        # SQLite connection helpers
│   ├── main.py                      # core API + FakeFed publish/delete admin endpoints
│   ├── accountability.py            # /api/accountability track-record metrics
│   └── narrative.py                 # /api/narrative AI-generated dashboard copy
│
├── sources/
│   └── fred.py                      # FRED fetch + transformations
│
├── db/
│   └── schema.sql                   # single source of truth for the schema
│
├── scripts/
│   ├── init_db.py                   # create tables from db/schema.sql
│   ├── clean_db.py                  # wipe selected tables for a clean rerun
│   ├── show_db_structure.py         # print schema/table overview
│   ├── read_one_document.py         # inspect a single document row
│   ├── update_weights.py            # manage analyst section weights
│   ├── compare_sentiments.sh        # compare sentiment/sentiment2/sentiment3/sentiment_w
│   ├── run_fed_documents_update.sh  # cron: MonitorFedAgent (recent) with logging
│   ├── run_fakefed_documents_update.sh # FakeFed ingestion with flock
│   ├── run_fred_backfill.sh         # MonitorFredAgent macro refresh
│   ├── run_w_agent.sh               # cron: w_agent with logging + flock
│   ├── run_strategist.sh            # cron: StrategistAgent with logging + flock
│   ├── run_analyst.sh               # legacy: analyst.py cron wrapper
│   ├── deploy.sh                    # frontend/backend/FakeFed deploy (SSH or --local)
│   ├── setup_deploy_user.sh         # one-time VPS deploy-user provisioning
│   └── test_db.py
│
├── tests/
│   ├── test_api.py
│   ├── test_fred_source.py
│   └── test_monitor_fakefed.py
│
├── fakefed/                          # synthetic Fed fixture site
├── fedwatcher/                       # static dashboard (fedwatcher.ellep.it)
├── dashboard/                        # reserved, currently empty
├── deploy/
│   ├── nginx/                       # Nginx config templates
│   └── fedwatcher-api.service       # systemd unit for the FastAPI backend
├── docs/
│   ├── fakefed_deployment.md
│   ├── dashboard_modes.md
│   └── design-system.html
└── academic_doc/                     # LaTeX academic report (make → main.pdf)
    ├── main.tex
    └── sections/
```

## Database

SQLite database: `fedwatcher.db`.

| Table | Description |
|---|---|
| `documents` | All Fed/FakeFed documents: URL, type, release date, raw text, processing flags |
| `sentiment_w` | Per-section tone scores from the weight-aware analyst (production) |
| `weights` | Section weights per `doc_type`, loaded dynamically by `w_agent.py` |
| `macro_data` | Monthly FRED macro: core CPI, unemployment, 2Y yield, FEDFUNDS policy rate |
| `signals` | StrategistAgent output: smoothed tone, tone/market-implied rates, divergence, rate-move probabilities |
| `sentiment` | Legacy: single-model analyst output |
| `sentiment2` | Legacy: dual-model analyst output |
| `sentiment3` | Legacy: DeepSeek-only analyst output |

`db/schema.sql` is the authoritative definition — see [AGENTS.md](AGENTS.md) for the schema
contract.

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
| `FEDFUNDS` | Effective Fed Funds Rate | `policy_rate` |

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

## User Guide

Installation instructions, usage examples, the FakeFed test site, and the full FastAPI
endpoint reference now live in a dedicated [User Guide](USER_GUIDE.md).

## Course Criteria Coverage

| Criterion | How FedWatcher satisfies it |
|---|---|
| Advanced LLM | weight-aware `AnalystAgent` extracts per-section structured Fed policy tone; `app/narrative.py` generates dashboard copy |
| Advanced ML/statistics | ordered-probit nowcast over rate-move buckets with EWMA tone smoothing, backtested via `/api/accountability` |
| Real-time/data processing | cron-scheduled Fed/FakeFed monitoring and FRED refresh pipeline |
| Non-trivial database | SQLite with multiple related tables (`documents`, `sentiment_w`, `weights`, `macro_data`, `signals`) |
| Own API | FastAPI backend for dashboard, accountability, narrative, and FakeFed admin access |
| Advanced visualization | dashboard for tone, macro, probabilities, divergence, document explorer |
| Agentic project | `AGENTS.md`, runtime agents, regular GitHub process, AI-authored contributions |

## References

- Gürkaynak, Sack, and Swanson on monetary policy surprises.
- Lucca and Trebbi on automated FOMC communication measurement.
- Hansen and McMahon on Fed communication text analysis.
- Shapiro and Wilson on text-based measures of central-bank communication.
- Taylor on policy-rule benchmarks.
