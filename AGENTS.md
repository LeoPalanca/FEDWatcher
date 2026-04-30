# FedWatcher Agent Workflow

This file defines how AI agents and human collaborators should work in this repository.
It exists because the course explicitly requires an agentic project organization and a
transparent GitHub development process.

This is a contributor/programming instruction file. The project itself should also contain
runtime agents in the codebase, especially `MonitorAgent`, `AnalystAgent`, and
`StrategistAgent`, because FedWatcher is being presented as an agentic financial application.

## Project Context

FedWatcher is a Programming in Finance II 2026 project. The goal is to deliver one
working solution: an agentic system that monitors Federal Reserve communications, extracts
monetary-policy tone with an LLM, combines it with macro and market data, and displays a
nowcast/dashboard for policy-rate decisions.

Before making substantial changes, review:

- `README.md` for the public project description, setup, usage, and criteria coverage.
- `ARCHITECTURE_SIMPLIFIED.md` for the preferred lean scope.
- `/Users/leonardo/FEDWatcher_Hide` for teacher/course guidelines and slides.

The hidden folder is local-only context. Do not commit files from `FEDWatcher_Hide`.

## Teacher Expectations

Course requirements and grading signals from the project brief:

- Deliver one working solution. Complexity is rewarded only when it works.
- Keep the full development process on GitHub.
- Commit regularly with meaningful messages that show project evolution.
- Organize the repository as an agentic project, with this `AGENTS.md` as the minimum.
- Include a comprehensive `README.md` with overview, installation, usage examples, and API
  or interface documentation.
- Provide a user guide and enough documentation for installation and deployment.
- Use issues or a project board to track work.
- Include additional technical details in a wiki or docs folder if the README becomes too large.
- Make at least one pull request created by an AI agent.
- Acknowledge generative AI usage in the academic PDF documentation.
- Cite external code, libraries, tutorials, datasets, and web sources correctly.
- Do not reuse the same project for another course.

Generic project criteria require at least three elements, with at least one advanced element.
FedWatcher should focus on these:

- Advanced LLM component: policy-tone extraction and evidence generation.
- Advanced ML component: multinomial or ordered nowcasting model for cut/hold/hike outcomes.
- Real-time data processing: polling Fed documents and market/macro data.
- Optional fourth element: non-trivial database with multiple tables.
- Optional fifth element: advanced visualization with an interactive dashboard.

## Scope Rules

Prefer the lean architecture unless the user explicitly asks to expand scope:

- Keep `MonitorAgent`, `AnalystAgent`, and `StrategistAgent` as the core named agents.
- Prefer SQLite for course delivery unless deployment requirements change.
- Prefer Streamlit for the dashboard unless there is a strong reason to build a larger frontend.
- Avoid claiming access to data sources that are not actually available.
- Use FRED and public data first: Fed target rates, effective Fed Funds, CPI, core CPI,
  unemployment, SOFR, and Treasury yields.
- Treat CME FedWatch, Fed Funds futures, OIS, and social media data carefully because API
  availability and terms can change.

Important model guardrails:

- Do not use a binary hike model for the final nowcast. Use multinomial or ordered outcomes:
  cut, hold, hike, or basis-point buckets such as `-50`, `-25`, `0`, `+25`, `+50`, `+75`.
- Handle irregular document timing when smoothing tone scores.
- Do not rely on LLM self-reported confidence as the only confidence measure.
- Backtest the full pipeline before presenting results.
- Keep economic claims precise and cite the correct literature.

## Sync Workflow

Use `/Users/leonardo/FEDWatcher` as the active working copy unless the user says otherwise.
There may be another clone at `/Users/leonardo/Documents/GitHub/FEDWatcher`; check before
copying or committing work across folders.

At the start of a work session:

1. Check repository state with `git status --short --branch`.
2. Check remotes with `git remote -v` if there is any doubt about which clone is active.
3. Check whether the branch is behind GitHub before committing.
4. Read the relevant project docs before changing architecture, data sources, or modeling.

Before committing:

1. Review `git diff`.
2. Run the smallest relevant verification command available.
3. Update `README.md` if the change affects setup, usage, architecture, data sources,
   project scope, dashboard behavior, model assumptions, or deliverables.
4. Update the relevant planning document, issue, or project board if the change affects
   the project plan.
5. Do not commit `.DS_Store`, secrets, local databases, API keys, or files from
   `/Users/leonardo/FEDWatcher_Hide`.

Commit messages should be meaningful and specific, for example:

- `Add FRED macro data ingestion plan`
- `Implement ordered nowcast baseline`
- `Document teacher requirements in AGENTS workflow`
- `Add dashboard user guide`

After committing:

1. Confirm the branch status.
2. Push only when the user asks or when the workflow explicitly requires it.
3. If pushing, make sure the target remote is `https://github.com/LeoPalanca/FEDWatcher.git`.

## README Update Rule

Every important project change must be reflected in `README.md`.

Important changes include:

- New install or run steps.
- New dependencies or environment variables.
- New data sources or API assumptions.
- New agents, model classes, database tables, scripts, dashboard views, or CLI commands.
- Changed project scope, grading-criteria coverage, or deliverables.
- Known limitations that affect what can be demonstrated.

Small internal refactors do not require README updates unless they change user-facing behavior.

## Data Source Suggestions

Core historical data for the nowcast:

- Fed target upper/lower bounds: FRED `DFEDTARU`, `DFEDTARL`.
- Effective Fed Funds Rate: FRED `DFF`.
- Headline CPI index: FRED `CPIAUCSL`.
- Core CPI index: FRED `CPILFESL`.
- Unemployment rate: FRED `UNRATE`.
- Natural unemployment rate or long-run unemployment proxy: FRED `NROU` or `NROUST`.
- 2-year Treasury yield: FRED `DGS2`.
- SOFR: FRED `SOFR`.

Useful transformations:

- `cpi_yoy = 100 * (CPI_t / CPI_t-12 - 1)`
- `core_cpi_yoy = 100 * (CoreCPI_t / CoreCPI_t-12 - 1)`
- `cpi_mom = 100 * (CPI_t / CPI_t-1 - 1)`
- `unemployment_gap = UNRATE_t - NROU_t`
- `policy_gap = DGS2 - fed_funds_current`

Political-pressure proxy, optional:

- Use Trump tweets/statements only as an exogenous policy-noise feature, not as a core Fed
  communication variable.
- For historical Twitter data, prefer a cited static archive.
- For Truth Social or current statements, prefer reproducible archives and document the source.
- Aggregate keyword counts and sentiment over 7, 14, and 30 days before each FOMC meeting.

## Suggested Repository Improvements

Prioritize these additions:

- `docs/teacher_requirements.md`: concise course requirements and how FedWatcher satisfies them.
- `docs/data_sources.md`: source URLs, API notes, transformations, and citation status.
- `docs/project_diary.md`: running log for the academic PDF.
- `docs/user_guide.md`: dashboard walkthrough and examples.
- GitHub issues or a project board matching the one-month plan.
- A branch and pull request explicitly created by an AI agent.
- `.gitignore` rules for `.DS_Store`, `.env`, local databases, caches, and generated outputs.
- A minimal test suite for data transforms, scraper parsing, and nowcast feature construction.

## Agent Implementation Contract

Core agents should expose simple, testable interfaces:

```python
class MonitorAgent:
    def run(self) -> list[dict]:
        """Return new documents not already stored."""

class AnalystAgent:
    def run(self, document: dict) -> dict:
        """Return policy-tone scores, evidence, and metadata."""

class StrategistAgent:
    def run(self, sentiment: dict, market: dict, macro: dict) -> dict:
        """Return policy probabilities, implied move, divergence, and explanation."""
```

Keep agent outputs structured and database-ready. Prefer dictionaries or typed dataclasses
over free-form text when data is consumed by another step.

## Local Tooling Notes

In this workspace, shell commands should be run with the `rtk` prefix as instructed by the
parent `/Users/leonardo/AGENTS.md`.

Examples:

```bash
rtk git status --short --branch
rtk pytest -q
rtk python scripts/init_db.py
```
