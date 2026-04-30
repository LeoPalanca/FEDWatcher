# 🦅 FedWatcher — Agentic Sentiment Analysis and Monetary Policy Nowcasting

> **MSc Economics — Programming in Finance II, 2026**  
> USI Università della Svizzera italiana  
> GitHub repository: 'https://github.com/LeoPalanca/FEDWatcher'

Project workflow: [agent instructions](AGENTS.md)

---

## Table of Contents

1. [Project Overview](#1-project-overview)
2. [Economic and Theoretical Motivation](#2-economic-and-theoretical-motivation)
3. [Agentic Architecture](#3-agentic-architecture)
4. [Data Sources](#4-data-sources)
5. [LLM Sentiment Analysis Pipeline](#5-llm-sentiment-analysis-pipeline)
6. [Nowcasting Model](#6-nowcasting-model)
7. [Real-Time Data Processing](#7-real-time-data-processing)
8. [Dashboard](#8-dashboard)
9. [Known Risks and Mitigations](#9-known-risks-and-mitigations)
10. [Installation](#10-installation)
11. [Usage](#11-usage)
12. [API Documentation](#12-api-documentation)
13. [AGENTS.md Summary](#13-agentsmd-summary)
14. [Project Criteria Coverage](#14-project-criteria-coverage)
15. [Academic References](#15-academic-references)

---

## 1. Project Overview

**FedWatcher** is an agentic AI system that continuously monitors central bank communications — primarily from the Federal Reserve (FOMC minutes, speeches, press conference transcripts) and, as an extension, the ECB — extracts the monetary policy tone using a large language model, compares that tone against market rate expectations, and visualizes the resulting signals on an interactive real-time dashboard.

The system is organized as a network of specialized AI agents:

- **Monitor Agent** — watches for new document releases from Fed/ECB websites and news wires; triggers the pipeline when new material is detected
- **Analyst Agent** — reads each document and uses an LLM to extract a structured sentiment score (hawkish / neutral / dovish) with evidence, across multiple policy dimensions (inflation outlook, labor market assessment, forward guidance, balance sheet policy)
- **Strategist Agent** — compares the extracted sentiment against market-implied rate expectations (OIS forward curve, Fed Funds futures) to identify sentiment/market divergences and generate a signal
- **Publisher Agent** — writes results to the database and updates the live dashboard; sends alerts when significant divergences are detected

The project is designed to replicate the workflow of a rates strategy desk: systematic, real-time processing of central bank communication to generate tradeable insights on monetary policy direction.

---

## 2. Economic and Theoretical Motivation

### The Problem

Central bank communication has become one of the most impactful drivers of interest rate markets. The shift toward forward guidance — pioneered by the Fed after the 2008 crisis — means that what the Fed *says* often matters more for market pricing than what it *does*. A surprise hawkish phrase in an FOMC statement can move 2-year Treasury yields by 10–15 basis points within seconds.

The academic literature on central bank communication identifies three key empirical regularities:

1. **Systematic tone → policy outcomes**: the language of FOMC minutes is predictive of subsequent rate decisions. Hawk/dove scores derived from textual analysis outperform simple Taylor rule forecasts at horizons of 1–3 meetings (Apel & Grimaldi, 2012; Lucca & Trebbi, 2009).
2. **Tone → asset prices**: tight/loose language in FOMC statements generates statistically significant and economically large returns in Treasury, equity, and FX markets in the hours surrounding releases (Gürkaynak, Sack & Swanson, 2005).
3. **Market misreaction**: markets do not fully and instantly incorporate all information in central bank documents. Residual drift in rates over 1–5 days following releases suggests that full parsing of lengthy documents takes time — creating a window for systematic extraction (Swanson, 2021).

### The Economic Question

The central question this project addresses is: **can a systematic, LLM-powered analysis of central bank communications detect policy tone shifts that are not yet fully reflected in market-implied rate expectations, and how large are the discrepancies?**

This connects directly to:

- **Nowcasting**: estimating the probability distribution of the next policy rate decision before the official announcement, using textual signals as inputs alongside macro data
- **Relative value in rates**: identifying mispricings between the OIS forward curve and the tone-implied policy path
- **Risk management**: detecting early signals of a policy pivot — hawkish or dovish — before they are priced into equities and credit spreads

### Why NLP/LLMs and Not Simple Bag-of-Words?

Earlier approaches to Fed communication analysis used simple frequency counts of words from hawk/dove dictionaries (Loughran-McDonald, 2011). These methods fail in two ways. First, context matters: "inflation is *not* above target" is scored hawkishly by a bag-of-words model that weights "inflation" positively. Second, Fed language evolves: the exact phrasing of forward guidance changes across rate cycles, making static dictionaries stale.

LLMs solve both problems. They understand negation and context, and their training on large corpora of economic text makes them robust to linguistic evolution. Recent work (Shah et al., 2023; López-Lira & Tang, 2023) demonstrates that GPT-class models substantially outperform dictionary methods in predicting market reactions to FOMC communications.

---

## 3. Agentic Architecture

The system is organized as a multi-agent pipeline. Agents are implemented as Python classes with a standard interface (`run()`, `on_trigger()`, `report_status()`), coordinated by a lightweight orchestrator. See `AGENTS.md` for the full agent specification used by AI contributors.

```
┌──────────────────────────────────────────────────────────────────────┐
│                        MONITOR AGENT                                 │
│                                                                      │
│  Scheduled polling (cron): federalreserve.gov/monetarypolicy/        │
│                             ecb.europa.eu/press/                     │
│  News wire monitoring: Reuters, Bloomberg RSS, FedWatch API          │
│  Trigger condition: new document URL detected                        │
│  Output: document URL + metadata → queue                             │
└──────────────────────────────────────────────────────────────────────┘
                              ↓ (document queue)
┌──────────────────────────────────────────────────────────────────────┐
│                        ANALYST AGENT                                 │
│                                                                      │
│  Input: document URL                                                 │
│  Step 1: fetch + clean document text (PDF parsing / HTML scraping)  │
│  Step 2: chunk document into sections (Statement, Minutes, Q&A)     │
│  Step 3: LLM call per section → structured JSON output:             │
│    {                                                                 │
│      "overall_tone": "hawkish" | "neutral" | "dovish",              │
│      "tone_score": float ∈ [-1, 1],  // -1 = very dovish            │
│      "inflation_assessment": str,                                    │
│      "labor_market_assessment": str,                                 │
│      "forward_guidance": str,                                        │
│      "key_phrases": [str],          // evidence sentences            │
│      "confidence": float ∈ [0, 1]                                   │
│    }                                                                 │
│  Step 4: aggregate section scores → document-level score            │
│  Output: sentiment record → database                                 │
└──────────────────────────────────────────────────────────────────────┘
                              ↓ (sentiment record)
┌──────────────────────────────────────────────────────────────────────┐
│                       STRATEGIST AGENT                               │
│                                                                      │
│  Input: sentiment record + live market data                          │
│  Step 1: fetch OIS forward curve (market-implied rate path)          │
│  Step 2: compute tone-implied policy path via nowcasting model       │
│  Step 3: compute divergence = market_implied_rate - tone_implied     │
│  Step 4: classify signal:                                            │
│    - Large positive divergence → market too hawkish vs. Fed tone     │
│    - Large negative divergence → market too dovish vs. Fed tone      │
│  Step 5: generate signal record + plain-language narrative           │
│  Output: signal record + narrative → database                        │
└──────────────────────────────────────────────────────────────────────┘
                              ↓ (signal record)
┌──────────────────────────────────────────────────────────────────────┐
│                       PUBLISHER AGENT                                │
│                                                                      │
│  Input: signal record                                                │
│  Action 1: write to PostgreSQL (signals table)                       │
│  Action 2: push update to dashboard (via WebSocket or polling)       │
│  Action 3: if |divergence| > threshold → send alert                 │
│             (email / Slack webhook)                                  │
│  Action 4: generate GitHub issue with signal summary (AI PR)        │
└──────────────────────────────────────────────────────────────────────┘
```

**Orchestrator:** A lightweight FastAPI-based coordinator manages agent state, handles retries on failure, and exposes the `/agents/status` endpoint. Agents are stateless between runs; all state is persisted in the database.

---

## 4. Data Sources

### 4.1 Federal Reserve Documents

**Source:** federalreserve.gov (scraped via `requests` + `BeautifulSoup` and `pdfplumber` for PDF parsing).

Document types monitored:

| Document Type | Release Schedule | Monetary Signal Strength |
|---|---|---|
| FOMC Statement | ~8× per year, post-meeting | Very High — contains explicit rate decision and forward guidance |
| FOMC Minutes | ~3 weeks post-meeting | High — full deliberation record, includes dissents and alternatives considered |
| Chair Press Conference Transcript | Same day as statement | High — Q&A often reveals nuance not in written statement |
| Chair Speeches | Ad hoc | Medium — signals evolving thinking between meetings |
| Beige Book | ~8× per year | Medium — economic conditions, informs rate decision |

**Parsing logic:** FOMC statements are short (<1,000 words) and fully parsed in a single LLM call. Minutes are longer (7,000–12,000 words) and are chunked into sections: *Developments in Financial Markets*, *Staff Review of Economic Situation*, *Participants' Views*, *Committee Policy Action*. The final section receives higher weight in the aggregate score.

### 4.2 ECB Documents

**Source:** ecb.europa.eu/press (scraped similarly). Documents: Monetary Policy Statement, Account of Monetary Policy Meeting (equivalent to FOMC minutes), President speeches. Processed identically to Fed documents; output stored in the same database schema with a `central_bank` field.

### 4.3 Market Rate Expectations

**Source:** `yfinance` for Fed Funds Futures (30-day contracts, CBOT). The implied Fed Funds rate is extracted as `100 - futures_price`.

OIS (Overnight Index Swap) forward rates for meeting-date tenors are sourced from the CME FedWatch Tool API (free access) and from FRED (`fredapi`): series `SOFR`, `DFF`, and SOFR futures.

| Series | Description |
|---|---|
| SOFR futures | Market-implied SOFR for each future date |
| CME FedWatch probabilities | Probability distribution over rate outcomes at each meeting |
| 2-year Treasury yield | Key market benchmark for rate expectations at 2-year horizon |

### 4.4 Database Schema

All data is stored in a PostgreSQL database with four tables:

```sql
-- Central bank documents
CREATE TABLE documents (
    id SERIAL PRIMARY KEY,
    central_bank VARCHAR(10),       -- 'FED' | 'ECB'
    doc_type VARCHAR(50),           -- 'statement' | 'minutes' | 'speech'
    release_date TIMESTAMP,
    url TEXT,
    raw_text TEXT,
    processed BOOLEAN DEFAULT FALSE
);

-- Sentiment records (output of Analyst Agent)
CREATE TABLE sentiment (
    id SERIAL PRIMARY KEY,
    document_id INTEGER REFERENCES documents(id),
    overall_tone VARCHAR(10),       -- 'hawkish' | 'neutral' | 'dovish'
    tone_score FLOAT,               -- ∈ [-1, 1]
    inflation_assessment TEXT,
    labor_market_assessment TEXT,
    forward_guidance TEXT,
    key_phrases TEXT[],
    confidence FLOAT,
    created_at TIMESTAMP
);

-- Market data snapshots
CREATE TABLE market_data (
    id SERIAL PRIMARY KEY,
    timestamp TIMESTAMP,
    sofr_rate FLOAT,
    ff_futures_implied FLOAT[],     -- array of implied rates by meeting date
    ois_1m FLOAT, ois_3m FLOAT, ois_6m FLOAT, ois_1y FLOAT, ois_2y FLOAT,
    us2y_yield FLOAT
);

-- Strategist signals
CREATE TABLE signals (
    id SERIAL PRIMARY KEY,
    document_id INTEGER REFERENCES documents(id),
    market_snapshot_id INTEGER REFERENCES market_data(id),
    tone_implied_next_rate FLOAT,
    market_implied_next_rate FLOAT,
    divergence FLOAT,
    signal_direction VARCHAR(20),   -- 'market_too_hawkish' | 'aligned' | 'market_too_dovish'
    narrative TEXT,
    created_at TIMESTAMP
);
```

---

## 5. LLM Sentiment Analysis Pipeline

### 5.1 Prompt Design

The Analyst Agent constructs a structured prompt for each document section. The system prompt establishes the analytical framework; the user prompt provides the text.

**System prompt:**
```
You are an expert monetary policy analyst specializing in central bank communication.
Your task is to analyze the following text from a central bank document and extract
the monetary policy tone. Respond ONLY in valid JSON matching the schema provided.
Do not add any preamble or explanation outside the JSON.

Schema:
{
  "overall_tone": "<hawkish|neutral|dovish>",
  "tone_score": <float between -1.0 (very dovish) and 1.0 (very hawkish)>,
  "inflation_assessment": "<brief description of how the CB characterizes inflation>",
  "labor_market_assessment": "<brief description of the CB's labor market view>",
  "forward_guidance": "<explicit or implicit guidance on future rate path>",
  "key_phrases": ["<phrase 1>", "<phrase 2>", ...],
  "confidence": <float between 0 and 1>
}

Definitions:
- Hawkish: language suggesting concern about inflation, openness to rate increases,
  or reluctance to cut rates.
- Dovish: language suggesting concern about growth/employment, openness to rate cuts,
  or reluctance to raise rates.
- Neutral: balanced language, no clear directional bias.
```

### 5.2 Tone Score Aggregation

Each document section produces a tone score `s_i ∈ [-1, 1]`. Document-level score:

```
tone_document = Σ_i (w_i · s_i) / Σ_i w_i
```

where weights `w_i` reflect the information content of each section:

| Section | Weight |
|---|---|
| Committee Policy Action | 0.40 |
| Participants' Views on Monetary Policy | 0.30 |
| Staff Review of Economic Situation | 0.20 |
| Other sections | 0.10 |

### 5.3 Temporal Smoothing

The raw per-document score is smoothed via an exponentially weighted moving average (EWMA) to produce a sentiment time series:

```
tone_t = λ · score_t + (1 - λ) · tone_{t-1},   λ = 0.3
```

This smoothed series is what the Strategist Agent uses and what is displayed on the dashboard.

### 5.4 Multi-Model Validation

To guard against model-specific biases, the Analyst Agent can optionally run the same prompt through two models (e.g., Claude claude-sonnet-4-20250514 and GPT-4o) and report the ensemble mean and agreement score. A large disagreement between models is flagged as a low-confidence signal.

---

## 6. Nowcasting Model

### 6.1 Tone-Implied Rate Path

The Strategist Agent translates the smoothed tone score into a probability distribution over the next meeting's rate decision. The mapping is estimated via logistic regression on historical data (2007–2024):

```
P(hike | tone_score, Δcpi, unemployment_gap) = σ(β₀ + β₁ · tone + β₂ · Δcpi + β₃ · u_gap)
```

Inputs:
- `tone_score`: smoothed LLM sentiment score
- `Δcpi`: 3-month change in headline CPI
- `u_gap`: unemployment rate minus Congressional Budget Office natural rate estimate (FRED: `NROU`)

This model is estimated on a dataset of all FOMC meetings with labeled outcomes (hike / hold / cut). The estimated probability `P(hike)` is converted to an implied rate:

```
implied_rate = current_rate + P(hike) · 0.25 - P(cut) · 0.25
```

### 6.2 Divergence Computation

```
divergence = market_implied_rate - tone_implied_rate
```

A positive divergence means the market is pricing a more hawkish outcome than the tone analysis suggests — a potential opportunity to position for a dovish surprise. A negative divergence suggests the market is too dovish.

Significance threshold: |divergence| > 10 basis points triggers a signal alert.

---

## 7. Real-Time Data Processing

### 7.1 Document Monitoring

The Monitor Agent runs as a scheduled job (cron via `APScheduler`, every 15 minutes). It maintains a cache of known document URLs; any new URL triggers the full pipeline.

**Release detection logic:**
```python
def check_for_new_documents(known_urls: set) -> list[dict]:
    """
    Scrape the Fed calendar page and FOMC publications index.
    Return list of new documents not in known_urls.
    """
    page = requests.get("https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm")
    soup = BeautifulSoup(page.content, 'html.parser')
    links = [a['href'] for a in soup.find_all('a', href=True) if is_fomc_document(a['href'])]
    return [{'url': url, 'metadata': extract_metadata(url)} for url in links if url not in known_urls]
```

### 7.2 Market Data Streaming

Market data (SOFR rate, FF futures prices, 2Y yield) is pulled every 5 minutes via the FRED API and cached in the `market_data` table. The dashboard reads from this cache rather than querying live.

For real-time intraday updates on FOMC announcement days (when markets move instantly), the system switches to a tighter polling interval of 30 seconds.

---

## 8. Dashboard

The Streamlit dashboard displays all system outputs on a single interactive interface with four panels:

### Panel 1: Live Sentiment Gauge

- Circular gauge showing the current EWMA tone score (−1 = very dovish → +1 = very hawkish)
- Color-coded: red (hawkish) → white (neutral) → blue (dovish)
- Last update timestamp and source document linked

### Panel 2: Sentiment Time Series

- Line chart: EWMA tone score over time (all available FOMC meetings)
- Overlaid: actual rate decisions (hike = upward arrow, cut = downward arrow, hold = dot)
- Secondary axis: US 2-year yield, to show co-movement between tone and market pricing

### Panel 3: Divergence Signal

- Bar chart: divergence (market-implied minus tone-implied next rate) per FOMC meeting
- Color: green bars = market more hawkish than tone (potential dovish surprise); red bars = market more dovish than tone
- Current divergence prominently displayed with signal direction label

### Panel 4: Document Explorer

- Searchable table of all processed documents
- Click any document → view key phrases extracted by LLM + section-level tone scores + full LLM narrative
- Download sentiment data as CSV

---

## 9. Known Risks and Mitigations

### Risk 1: LLM Hallucination in Sentiment Extraction

The LLM may mischaracterize the tone of a document, particularly for nuanced or ambiguous language in FOMC minutes.

**Mitigation:** Three-layer validation: (a) JSON schema validation ensures the output is structurally correct; (b) the `confidence` field is required and scores below 0.6 are flagged for manual review; (c) key phrase extraction provides an audit trail — a hawkish score with no hawkish key phrases is flagged as inconsistent.

### Risk 2: Website Scraping Fragility

The Fed and ECB may change their website structure, breaking the document scraper.

**Mitigation:** The scraper is built with multiple fallback strategies: primary HTML parsing → RSS feed → news wire monitoring. The scraping logic is isolated in `agents/monitor.py` to make updates easy. An alert is triggered if no new documents are detected within 40 days (impossible under the FOMC calendar).

### Risk 3: Nowcasting Model Overfitting

The logistic regression nowcasting model is estimated on a relatively short sample (2007–2024, ~140 meetings). Overfitting, particularly to the unique zero-lower-bound period (2009–2015), could produce unreliable probability estimates.

**Mitigation:** Cross-validation with a rolling window (train on meetings 1 to T, test on meeting T+1). The model is regularized (L2 penalty, tuned via CV). Out-of-sample performance metrics are reported in the academic documentation.

### Risk 4: Scope — Real-Time + Agents + LLM + Dashboard

**Mitigation:** MVP-first development protocol:

| Phase | Deliverable | Deadline |
|---|---|---|
| Week 1–2 | Document scraper + database populated with historical FOMC docs | [date] |
| Week 3–4 | Analyst Agent working: LLM sentiment extraction + historical backfill | [date] |
| Week 5 | Nowcasting model estimated, Strategist Agent running | [date] |
| Week 6–7 | Dashboard v1: sentiment time series + document explorer | [date] |
| Week 8 | Monitor Agent: live polling operational | [date] |
| Week 9 | Publisher Agent: alerts + GitHub issue automation | [date] |
| Week 10–12 | ECB extension, multi-model validation, testing, documentation | [date] |

ECB integration and multi-model LLM validation are stretch goals.

---

## 10. Installation

### Prerequisites

- Python 3.10+
- API key for Claude (Anthropic) or OpenAI
- PostgreSQL 14+
- FRED API key (free at fred.stlouisfed.org)

### Setup

```bash
# Clone the repository
git clone https://github.com/[username]/fedwatcher
cd fedwatcher

# Create virtual environment
python -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Configure environment
cp .env.example .env
# Edit .env: ANTHROPIC_API_KEY, FRED_API_KEY, POSTGRES_URL

# Initialize database
python scripts/init_db.py

# Backfill historical FOMC documents (2015–2024)
python agents/monitor.py --backfill --start-year 2015

# Run Analyst Agent on historical corpus
python agents/analyst.py --run-all

# Estimate nowcasting model
python models/nowcast.py --train

# Launch dashboard
streamlit run dashboard/app.py

# Launch orchestrator (starts live monitoring)
python orchestrator.py
```

### Docker

```bash
docker-compose up --build
# Dashboard: http://localhost:8501
# API: http://localhost:8000
# Orchestrator runs automatically
```

---

## 11. Usage

### Running the full pipeline manually

```python
from agents.monitor import MonitorAgent
from agents.analyst import AnalystAgent
from agents.strategist import StrategistAgent
from agents.publisher import PublisherAgent

# Initialize agents
monitor    = MonitorAgent()
analyst    = AnalystAgent(model='claude-sonnet-4-20250514')
strategist = StrategistAgent()
publisher  = PublisherAgent()

# Detect new documents
new_docs = monitor.run()

for doc in new_docs:
    # Extract sentiment
    sentiment = analyst.run(doc)
    print(f"Tone: {sentiment['overall_tone']} ({sentiment['tone_score']:.2f})")
    print(f"Forward guidance: {sentiment['forward_guidance']}")

    # Generate signal
    signal = strategist.run(sentiment)
    print(f"Signal: {signal['signal_direction']}")
    print(f"Divergence: {signal['divergence'] * 100:.1f} bps")
    print(f"Narrative: {signal['narrative']}")

    # Publish
    publisher.run(signal)
```

### Querying sentiment history

```python
from db import get_sentiment_history

history = get_sentiment_history(central_bank='FED', start_date='2022-01-01')
# Returns DataFrame: date | overall_tone | tone_score | divergence
history.plot(x='date', y='tone_score', title='Fed Tone Score: 2022–2026')
```

---

## 12. API Documentation

### `GET /sentiment/latest`

Returns the most recent sentiment record.

**Response:**
```json
{
  "document_type": "minutes",
  "release_date": "2026-04-09",
  "overall_tone": "hawkish",
  "tone_score": 0.42,
  "ewma_score": 0.31,
  "inflation_assessment": "Participants noted that inflation remained above the 2% target...",
  "forward_guidance": "Most participants favored maintaining the current target range...",
  "key_phrases": ["inflation remains elevated", "patient approach to policy normalization"],
  "confidence": 0.87
}
```

### `GET /signal/latest`

Returns the most recent Strategist Agent signal.

**Response:**
```json
{
  "signal_direction": "market_too_hawkish",
  "divergence_bps": 12.3,
  "tone_implied_next_rate": 4.50,
  "market_implied_next_rate": 4.625,
  "narrative": "The most recent FOMC minutes display a moderately hawkish tone...",
  "timestamp": "2026-04-09T18:30:00Z"
}
```

### `GET /sentiment/history`

Returns time series of sentiment scores.

**Parameters:**
- `central_bank`: `FED` | `ECB` (default: `FED`)
- `start_date`, `end_date`: ISO date strings
- `doc_types`: comma-separated list (default: `statement,minutes`)

### Authentication

```bash
curl -H "Authorization: Bearer <token>" http://localhost:8000/sentiment/latest
```

---

## 13. AGENTS.md Summary

The full `AGENTS.md` file specifies the four agents for AI contributor use. Key contents:

```markdown
# AGENTS.md — FedWatcher Agent Specification

## MonitorAgent
- **Trigger:** Scheduled cron (every 15 min)
- **Input:** Known URL cache (database)
- **Output:** New document URLs → document queue
- **Constraint:** Must not re-process already-parsed documents

## AnalystAgent
- **Trigger:** New item in document queue
- **Input:** Document URL
- **Output:** Sentiment JSON → `sentiment` table
- **LLM call spec:** See `prompts/analyst_system.txt` and `prompts/analyst_user.txt`
- **Validation:** JSON schema check; confidence < 0.6 → flag for review

## StrategistAgent
- **Trigger:** New record in `sentiment` table
- **Input:** Sentiment record + latest `market_data` row
- **Output:** Signal record → `signals` table
- **Model:** `models/nowcast.pkl` (logistic regression, pre-trained)

## PublisherAgent
- **Trigger:** New record in `signals` table
- **Input:** Signal record
- **Actions:** Update dashboard cache; if |divergence| > 10bps → send alert;
              if significant signal → open GitHub issue via GitHub API
- **AI PR requirement:** The GitHub issue opening and PR creation must be
              performed by the AI agent autonomously, satisfying the course
              requirement for at least one AI-authored pull request.
```

---

## 14. Project Criteria Coverage

| Criterion | Implementation | Status |
|---|---|---|
| *Advanced LLM component | Claude / GPT-4o for semantic sentiment extraction | ✅ Core |
| Real-time data processing | Live polling of Fed/ECB sites + SOFR/futures every 5 min | ✅ Core |
| *Advanced data visualization | Interactive Streamlit dashboard: sentiment gauge, divergence chart, vol timeline | ✅ Core |
| Non-trivial database | PostgreSQL: documents, sentiment, market_data, signals | ✅ Core |
| Agentic project | 4 specialized agents; AGENTS.md; AI-authored GitHub PR | ✅ Required |
| API with authentication | FastAPI with Bearer token auth | ✅ Core |
| GitHub process | Commit history, issues, project board | ✅ Required |
| Academic documentation | 7-page LaTeX PDF on iCorsi | ✅ Required |

**Criterion coverage count: 3 elements (LLM advanced + real-time data processing + advanced visualization), satisfying the minimum of 3 with at least 1 advanced.**

---

## 15. Academic References

- Apel, M., & Grimaldi, M. B. (2012). The information content of central bank minutes. *Riksbank Research Paper Series*, No. 92.
- Gürkaynak, R., Sack, B., & Swanson, E. (2005). Do actions speak louder than words? The response of asset prices to monetary policy actions and statements. *International Journal of Central Banking*, 1(1), 55–93.
- Lucca, D. O., & Trebbi, F. (2009). Measuring central bank communication: An automated approach with application to FOMC statements. *NBER Working Paper* No. 15367.
- Swanson, E. T. (2021). Measuring the effects of federal reserve forward guidance and asset purchases on financial markets. *Journal of Monetary Economics*, 118, 32–53.
- López-Lira, A., & Tang, Y. (2023). Can ChatGPT forecast stock price movements? Return predictability and large language models. *SSRN Working Paper* No. 4412788.
- Shah, D., Misra, H., Bhatt, S., & Chaudhary, A. (2023). Zero-shot financial sentiment analysis using LLMs. *arXiv preprint* arXiv:2305.01105.
- Loughran, T., & McDonald, B. (2011). When is a liability not a liability? Textual analysis, dictionaries, and 10-Ks. *Journal of Finance*, 66(1), 35–65.
- Taylor, J. B. (1993). Discretion versus policy rules in practice. *Carnegie-Rochester Conference Series on Public Policy*, 39, 195–214.
- Hagan, P., & Woodward, D. (2022). The forward guidance puzzle. *Journal of Applied Econometrics*, 37(3), 445–468.
- Nelson, C. R., & Siegel, A. F. (1987). Parsimonious modeling of yield curves. *Journal of Business*, 60(4), 473–489.

---

*Last updated: April 2026*  
*Course: Programming in Finance II — USI Università della Svizzera italiana*  
*Instructor: Prof. Peter H. Gruber*
