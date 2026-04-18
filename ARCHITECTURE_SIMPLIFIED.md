# FedWatcher — Simplified Architecture

## Why Simplify

Original README design is over-engineered for course scope. Rule: **everything handed in must work**. Complexity that adds ops burden without grade benefit is risk, not reward.

The rubric rewards:
- Working solution
- 3 criteria elements (≥1 advanced)
- Agentic = AGENTS.md + ≥1 AI PR — not 4 rigid OOP agent classes
- Process on GitHub (commits, issues)

---

## What to Cut

| Component | Reason to cut |
|-----------|---------------|
| `PublisherAgent` class | 5 lines of code — write to DB + send webhook. Not an agent. Inline in `pipeline.py` |
| FastAPI orchestrator + `/agents/status` | "Own API with auth" is a criterion element but FedWatcher already covers 3 without it. Cut unless adding a 4th criterion intentionally |
| PostgreSQL | SQLite handles ~140 FOMC rows fine. Multiple tables still = non-trivial DB. Postgres adds ops overhead with zero grade benefit |
| Docker | Not required. `requirements.txt` + `.env.example` sufficient for install instructions |
| APScheduler as separate service | Run as a simple loop in `pipeline.py` with `time.sleep()` or a single scheduler call |

---

## What to Keep

| Component | Why |
|-----------|-----|
| `MonitorAgent`, `AnalystAgent`, `StrategistAgent` as named classes | Satisfies agentic framing for AGENTS.md |
| LLM sentiment pipeline | Core advanced criterion (*LLM) |
| Logistic regression nowcast | Second advanced criterion (*ML) |
| Real-time market data polling | Third criterion (real-time data) |
| Streamlit dashboard | Strong visual output for presentation |
| SQLite with 4 tables | Non-trivial DB criterion if needed as 4th |

---

## Revised File Structure

```
fedwatcher/
├── AGENTS.md                   # required by rubric
├── README.md                   # required by rubric
├── .env.example
├── requirements.txt
│
├── sources/                    # data ingestion (no agent class needed)
│   ├── fed.py                  # scrape federalreserve.gov
│   ├── ecb.py                  # scrape ecb.europa.eu
│   └── twitter.py              # experimental — see X_IMPLEMENTATION.md
│
├── agents/
│   ├── analyst.py              # AnalystAgent: LLM sentiment extraction
│   └── strategist.py           # StrategistAgent: nowcast + divergence signal
│
├── models/
│   └── nowcast.py              # logistic regression training + inference
│
├── market.py                   # fetch FF futures / SOFR from FRED + yfinance
├── db.py                       # SQLite interface, all 4 tables
├── pipeline.py                 # orchestrator: ties sources → agents → db → dashboard
│
└── dashboard/
    └── app.py                  # Streamlit: gauge, time series, divergence, doc explorer
```

---

## Agent Interface (for AGENTS.md)

Each agent exposes a minimal interface. No `on_trigger()`, no `report_status()` — just `run()`:

```python
class AnalystAgent:
    def run(self, document: dict) -> dict:
        """
        Input:  document dict with keys: url, raw_text, doc_type, central_bank
        Output: sentiment dict (tone_score, overall_tone, key_phrases, confidence, ...)
        """

class StrategistAgent:
    def run(self, sentiment: dict, market: dict) -> dict:
        """
        Input:  sentiment dict from AnalystAgent + market snapshot dict
        Output: signal dict (divergence, signal_direction, tone_implied_rate, narrative)
        """
```

`MonitorAgent` stays a class only because it holds state (known URL cache). It wraps `sources/fed.py` and `sources/ecb.py`:

```python
class MonitorAgent:
    def __init__(self, db):
        self.known_urls = db.get_known_urls()

    def run(self) -> list[dict]:
        """Returns list of new documents not yet in DB."""
```

---

## Orchestrator (`pipeline.py`)

Replaces FastAPI orchestrator + Publisher Agent. Single entry point:

```python
def run_pipeline():
    monitor    = MonitorAgent(db)
    analyst    = AnalystAgent(model=LLM_MODEL)
    strategist = StrategistAgent()

    new_docs = monitor.run()
    for doc in new_docs:
        sentiment = analyst.run(doc)
        db.save_sentiment(sentiment)

        market = market.get_latest()
        signal = strategist.run(sentiment, market)
        db.save_signal(signal)

        if abs(signal["divergence"]) > ALERT_THRESHOLD:
            send_alert(signal)          # email / Slack webhook — 3 lines

if __name__ == "__main__":
    schedule.every(15).minutes.do(run_pipeline)
    while True:
        schedule.run_pending()
        time.sleep(60)
```

---

## Database: SQLite

Same 4-table schema as README. Switch connection string only:

```python
# db.py
import sqlite3
DB_PATH = "fedwatcher.db"
```

No migration files, no pgbouncer, no Docker volume mounts.

---

## Criteria Coverage (Lean Version)

| Criterion | How satisfied | Advanced? |
|-----------|---------------|-----------|
| *LLM | AnalystAgent: Claude/GPT-4o sentiment extraction | ✅ Advanced |
| *ML model | Logistic regression nowcasting in `models/nowcast.py` | ✅ Advanced |
| Real-time data | 15-min polling of Fed site + FRED market data | ✅ |

Three elements, two advanced. Minimum requirement met. Everything else is scope.

---

## AI Pull Request (Criterion 5)

Simplest path: once AGENTS.md exists, instruct Claude Code to:
1. Add a new `sources/` scraper function
2. Open a GitHub PR with the change

This satisfies "at least one pull request made by an AI agent" without building a Publisher Agent that auto-opens issues.
