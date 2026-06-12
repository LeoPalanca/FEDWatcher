# FedWatcher User Guide

Welcome to FedWatcher. This guide walks you through setting the project up on your own machine, running the data pipeline, exercising the FakeFed test site, and talking to the HTTP API. It assumes no prior knowledge of the codebase — if you can run a terminal and edit a text file, you can follow along.

For the conceptual background — what FedWatcher does, how the agents fit together, the data model, and the finance model behind the nowcast — see [the README](README.md). This guide is the hands-on companion to that overview.

---

## Contents

1. [Before you begin](#1-before-you-begin)
2. [Installing FedWatcher](#2-installing-fedwatcher)
3. [Your first run](#3-your-first-run)
4. [Running the pipeline day to day](#4-running-the-pipeline-day-to-day)
5. [Working with the FakeFed test site](#5-working-with-the-fakefed-test-site)
6. [Using the API](#6-using-the-api)
7. [Command reference](#7-command-reference)

---

## 1. Before you begin

Make sure you have the following ready before installing:

| Requirement | Why you need it |
|---|---|
| **Python 3.10 or newer** | Runs the agents, the pipeline, and the FastAPI backend. |
| **A FRED API key** | Lets `MonitorFredAgent` download macro data (CPI, unemployment, yields, Fed Funds). Free from [fred.stlouisfed.org](https://fred.stlouisfed.org/docs/api/api_key.html). |
| **An OpenRouter API key** | Lets the analyst call the LLM that scores Fed-statement tone. |

You only need the two API keys for the full pipeline. You can install and explore the codebase without them, but the analyst and macro steps will not produce data until they are set.

---

## 2. Installing FedWatcher

### Step 1 — Get the code and dependencies

Clone the repository, create a virtual environment, and install the Python packages:

```bash
git clone https://github.com/LeoPalanca/FEDWatcher.git
cd FEDWatcher
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### Step 2 — Add your API keys

Copy the example environment file and fill in your two keys:

```bash
cp .env.example .env
```

Open `.env` and set:

```text
OPENROUTER_API_KEY=your-openrouter-key
FRED_API_KEY=your-fred-key
```

> **Server-only extras.** If you are deploying the FakeFed publish/delete admin endpoints, also set `FAKEFED_PUBLISH_PASSWORD` (required by `app/main.py`) and, optionally, `FAKEFED_ROOT` — the directory statements are written to. `FAKEFED_ROOT` defaults to `/var/www/fakefed` on the server and falls back to the local `fakefed/` directory. You do **not** need these for normal local development.

### Step 3 (optional) — Let the guided setup do it for you

Instead of editing files by hand, you can run the guided installer. It writes `.env`, creates the database from `db/schema.sql`, configures the analyst's section weights, and can kick off the local Monitor → Analyst → Strategist loop and dashboard:

```bash
python run.py setup
```

This is the fastest path to a working install and is recommended for first-time setup.

---

## 3. Your first run

Once installed, here is the shortest path to seeing FedWatcher produce data.

### Step 1 — Create the database

```bash
python scripts/init_db.py
```

This builds `fedwatcher.db` with all the tables defined in `db/schema.sql`. (If you ran `python run.py setup` above, this has already happened.)

### Step 2 — Pull in macro data

```bash
python -m agents.monitor_fred
```

`MonitorFredAgent` downloads the FRED series and fills the `macro_data` table.

### Step 3 — Ingest some Fed statements

```bash
python -m agents.monitor_fed --mode recent
```

This scrapes recent FOMC statements into the `documents` table. To load history instead, see the [command reference](#7-command-reference).

### Step 4 — Score tone and generate signals

```bash
python agents/w_agent.py --db fedwatcher.db --limit 5
python agents/strategist.py --db fedwatcher.db
```

The first command runs the weight-aware analyst (tone scoring); the second runs the strategist, which produces the rate-move nowcast in the `signals` table.

### Step 5 — Start the backend and look at the results

```bash
uvicorn app.main:app --reload
```

The API is now live on `http://127.0.0.1:8000`. Try `http://127.0.0.1:8000/api/health` to confirm it is up, then explore the other [endpoints](#6-using-the-api).

> **Shortcut.** Steps 2–4 can be run as a single cycle with `python run.py pipeline --once`, and `python run.py dev` starts the API together with a local dashboard proxy.

---

## 4. Running the pipeline day to day

For ongoing use, you typically do not run each agent by hand — you let the pipeline cycle through them. The `run.py` launcher wraps the common workflows:

```bash
python run.py pipeline --once    # run one Monitor → Analyst → Strategist cycle
python run.py pipeline           # repeat continuously (every 24h by default)
```

To control the cadence of the continuous mode, pass `--refresh-hours`.

Need to run just one stage? These map to individual steps:

```bash
python run.py analyze --limit 5  # score tone on unprocessed statements only
python run.py macro              # refresh FRED macro data only
python run.py weights            # print the analyst's current section weights
```

In production these same agents run unattended via cron on the server — see the Automation table in [the README](README.md) for the schedule.

---

## 5. Working with the FakeFed test site

`fakefed/` is a synthetic, static copy of the Fed website. It mirrors the real Fed URL structure so you can test the scraper and the downstream pipeline end to end **without touching the live federalreserve.gov site**.

The live test site is served at:

```text
https://fakefed.ellep.it/monetarypolicy/fomccalendars.htm
```

It currently hosts 4 synthetic statements (March, May ×2, and June 2026). Note that `MonitorFakeFedAgent` only ingests statements released **after 2026-05-21**.

### Ingesting from FakeFed

Point the FakeFed monitor at the test site to pull its statements into the same `documents` table the real monitor uses:

```bash
python -m agents.monitor_fakefed
```

From here the analyst and strategist treat the synthetic statements exactly like real ones, so you can validate the whole pipeline on predictable, controllable input.

### Publishing and removing test statements

The FastAPI backend can add or delete FakeFed statements on the fly:

- `POST /api/fakefed/statements` — publish a new synthetic statement.
- `DELETE /api/fakefed/statements/{filename}` — remove one.

Both are admin-only. They are guarded by the `FAKEFED_PUBLISH_PASSWORD` environment variable, which you supply with each request via the `X-Fakefed-Password` header. The files are written into the directory named by `FAKEFED_ROOT`.

> Deployment details for the test site live in `docs/fakefed_deployment.md` and `deploy/nginx/fakefed.ellep.it.conf`.

---

## 6. Using the API

With the backend running (`uvicorn app.main:app --reload`), the following endpoints are available. The read endpoints are open; the FakeFed write endpoints require the admin header described above.

| Endpoint | What it returns |
|---|---|
| `GET /api/health` | Liveness check — use this to confirm the backend is up. |
| `GET /api/tables` | The list of database tables. |
| `GET /api/tables/{table}` | Rows from one table, with pagination and search (`?limit=100&offset=0&search=...`). |
| `GET /api/documents` | Fed and FakeFed documents, with pagination and search. |
| `GET /api/snapshot` | A combined latest-state payload built for the dashboard. |
| `GET /api/accountability` | Track-record metrics — hit rate, MAE (bps), and Brier score against realized FOMC outcomes. |
| `GET /api/narrative` | AI-generated dashboard copy, cached per latest `signals` row. |
| `POST /api/fakefed/statements` | **Admin.** Publish a synthetic FakeFed statement. Requires `X-Fakefed-Password`. |
| `DELETE /api/fakefed/statements/{filename}` | **Admin.** Remove a synthetic FakeFed statement. Requires `X-Fakefed-Password`. |

A quick way to confirm everything works:

```bash
curl http://127.0.0.1:8000/api/health
curl http://127.0.0.1:8000/api/snapshot
```

---

## 7. Command reference

A consolidated list of the commands used throughout this guide.

### Setup and database

```bash
python run.py setup                 # guided install: .env, DB, weights, optional pipeline
python scripts/init_db.py           # create fedwatcher.db from db/schema.sql
```

### Data ingestion

```bash
python -m agents.monitor_fred                                            # FRED macro data
python -m agents.monitor_fed --mode recent                               # recent FOMC statements
python -m agents.monitor_fed --mode calendar                             # scrape the FOMC calendar
python -m agents.monitor_fed --mode historical --start-year 2015 --end-year 2020  # historical backfill
python -m agents.monitor_fakefed                                         # ingest from the FakeFed test site
```

### Analysis and signals

```bash
python agents/w_agent.py --db fedwatcher.db --limit 5   # score tone (weight-aware analyst)
python agents/strategist.py --db fedwatcher.db          # generate rate-move nowcast signals
```

### Pipeline launcher (`run.py`)

```bash
python run.py dev                # FastAPI + local dashboard proxy on 127.0.0.1:8080
python run.py pipeline --once    # one Monitor → Analyst → Strategist cycle
python run.py pipeline           # repeat every 24h (configurable with --refresh-hours)
python run.py analyze --limit 5  # run the analyst on unprocessed statements
python run.py macro              # download/update FRED macro data
python run.py weights            # show current analyst section weights
```

### Backend

```bash
uvicorn app.main:app --reload    # start the FastAPI backend on 127.0.0.1:8000
```
