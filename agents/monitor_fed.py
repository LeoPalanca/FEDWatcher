"""MonitorFedAgent — Federal Reserve document ingestion.

Three fetch modes (one entrypoint, --mode flag selects path):

- ``recent`` (default, used by cron): FedTools 90-day lookback for FOMC
  statements and minutes. Replaces the old scripts/update_fed_documents.py.
- ``calendar``: live FOMC calendar HTML scraping
  (fomccalendars.htm). Replaces the old agents/monitor.py path.
- ``historical``: combined backfill — FedTools all-time statements + minutes
  from 2015 onward, plus HTML scraping of the legacy fomchistorical{year}.htm
  pages (1994-2014 by default). Replaces the old
  agents/monitor-fed-historical-pages.py and scripts/initial_data_download.py.

Run as: python -m agents.monitor_fed [--db ...] [--mode recent|calendar|historical]
"""

from __future__ import annotations

import argparse
import os
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup, Tag

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in os.sys.path:
    os.sys.path.insert(0, str(PROJECT_ROOT))


DEFAULT_BASE_URL = "https://www.federalreserve.gov"
DEFAULT_CALENDAR_PATH = "/monetarypolicy/fomccalendars.htm"
DEFAULT_DB_PATH = "fedwatcher.db"
DEFAULT_TIMEOUT = 30

RECENT_LOOKBACK_DAYS = 90
HISTORICAL_HTML_START_YEAR = 1994
HISTORICAL_HTML_END_YEAR = 2014

Mode = Literal["recent", "calendar", "historical"]


# ---------------------------------------------------------------------------
# shared helpers
# ---------------------------------------------------------------------------


def clean_fed_text(raw_text: str, doc_type: str) -> str:
    """Strip Fed website boilerplate from raw document text."""

    if not raw_text:
        return ""

    text = " ".join(raw_text.split())

    if doc_type == "statement":
        start_markers = [
            "For release at",
            "Information received since",
            "Recent indicators suggest",
            "Recent indicators",
            "The Committee seeks to achieve",
        ]
    else:
        start_markers = []

    best_start = None
    for marker in start_markers:
        pos = text.find(marker)
        if pos != -1 and (best_start is None or pos < best_start):
            best_start = pos
    if best_start is not None:
        text = text[best_start:]

    end_markers = [
        "Last Update:",
        "Board of Governors of the Federal Reserve System",
        "Back to Top",
        "Stay Connected",
        "Contact",
        "Accessibility",
        "FOIA",
        "No FEAR Act",
        "Privacy Program",
    ]

    best_end = None
    for marker in end_markers:
        pos = text.find(marker)
        if pos != -1 and (best_end is None or pos < best_end):
            best_end = pos
    if best_end is not None:
        text = text[:best_end]

    return text.strip()


def clean_html_text(html: str) -> str:
    """Extract readable text from a Fed HTML page."""

    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header", "noscript"]):
        tag.decompose()

    main = soup.find("main") or soup.find("div", id="content") or soup
    text = main.get_text(separator=" ", strip=True)
    return " ".join(text.split())


def extract_release_date_from_url(url: str) -> str | None:
    """Extract YYYY-MM-DD from a Fed-style YYYYMMDD-in-URL pattern."""

    match = re.search(r"((?:19|20)\d{6})", url)
    if not match:
        return None
    try:
        return datetime.strptime(match.group(1), "%Y%m%d").strftime("%Y-%m-%d")
    except ValueError:
        return None


def normalize_release_date(release_date: Any) -> str | None:
    """Convert FedTools index date (or anything date-like) into YYYY-MM-DD."""

    if release_date is None:
        return None

    release_date_str = str(release_date)[:10]
    try:
        datetime.strptime(release_date_str, "%Y-%m-%d")
        return release_date_str
    except ValueError:
        return None


def extract_raw_text_from_fedtools_row(row_dict: dict) -> str:
    """FedTools columns vary — try the known column names in order."""

    return (
        row_dict.get("FOMC_Statements")
        or row_dict.get("Federal_Reserve_Mins")
        or row_dict.get("text")
        or row_dict.get("Text")
        or row_dict.get("contents")
        or row_dict.get("Contents")
        or row_dict.get("statement")
        or row_dict.get("Statement")
        or ""
    )


def is_html(url: str) -> bool:
    path = urlparse(url).path.lower()
    return path.endswith(".htm") or path.endswith(".html")


def ensure_database_exists(db_path: Path) -> None:
    if not db_path.exists():
        raise FileNotFoundError(
            f"Database file not found: {db_path}. Run python scripts/init_db.py first."
        )


def save_documents(db_path: Path, documents: list[dict[str, Any]]) -> int:
    """Upsert documents into SQLite documents table on URL conflict."""

    if not documents:
        return 0

    ensure_database_exists(db_path)

    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA foreign_keys = ON;")
        cursor = conn.cursor()
        count = 0
        for doc in documents:
            cursor.execute(
                """
                INSERT INTO documents
                    (central_bank, doc_type, release_date, url, raw_text, processed)
                VALUES
                    (?, ?, ?, ?, ?, ?)
                ON CONFLICT(url) DO UPDATE SET
                    central_bank = excluded.central_bank,
                    doc_type = excluded.doc_type,
                    release_date = excluded.release_date,
                    raw_text = excluded.raw_text,
                    processed = excluded.processed
                """,
                (
                    doc.get("central_bank", "FED"),
                    doc["doc_type"],
                    doc["release_date"],
                    doc["url"],
                    doc.get("raw_text", ""),
                    int(bool(doc.get("processed", 0))),
                ),
            )
            count += cursor.rowcount
        conn.commit()
        return count


# ---------------------------------------------------------------------------
# mode: recent (FedTools 90-day)
# ---------------------------------------------------------------------------


def _normalize_fedtools_dataframe(
    df,
    doc_type: str,
    cutoff_date: str | None = None,
    start_year: int | None = None,
) -> list[dict[str, Any]]:
    documents: list[dict[str, Any]] = []

    if df is None or df.empty:
        print(f"No {doc_type} documents returned by FedTools.")
        return documents

    df = df.copy().sort_index(ascending=False)

    for release_date, row in df.iterrows():
        release_date_str = normalize_release_date(release_date)
        if not release_date_str:
            continue
        if cutoff_date is not None and release_date_str < cutoff_date:
            continue
        if start_year is not None and int(release_date_str[:4]) < start_year:
            continue

        raw_text = extract_raw_text_from_fedtools_row(row.to_dict())
        clean_text = clean_fed_text(raw_text, doc_type)
        if not clean_text:
            print(f"Skipped {doc_type} {release_date_str}: no clean text found.")
            continue

        documents.append(
            {
                "central_bank": "FED",
                "doc_type": doc_type,
                "release_date": release_date_str,
                "url": f"fedtools://FED/{doc_type}/{release_date_str}",
                "raw_text": clean_text,
                "processed": 0,
            }
        )

    return documents


def fetch_via_fedtools(
    cutoff_date: str | None = None,
) -> list[dict[str, Any]]:
    """Fetch statements through FedTools.

    cutoff_date: keep only release_date >= cutoff (YYYY-MM-DD). None = no cutoff.
    """

    from FedTools import MonetaryPolicyCommittee

    documents: list[dict[str, Any]] = []

    try:
        statement_df = MonetaryPolicyCommittee(
            verbose=True, thread_num=1
        ).find_statements()
        documents.extend(
            _normalize_fedtools_dataframe(
                statement_df,
                doc_type="statement",
                cutoff_date=cutoff_date,
            )
        )
    except Exception as exc:
        print(f"WARNING: Failed to fetch FOMC statements via FedTools: {exc}")

    documents.sort(key=lambda doc: doc["release_date"], reverse=True)
    return documents


# ---------------------------------------------------------------------------
# mode: calendar (live FOMC calendar scrape)
# ---------------------------------------------------------------------------


def classify_doc_type_from_url(url: str, link_text: str = "") -> str | None:
    url_lower = url.lower()
    text_lower = link_text.lower()

    if "implementation note" in text_lower or "implementationnote" in url_lower:
        return None
    if "press conference" in text_lower or "presconf" in url_lower:
        return None

    if "minutes" in url_lower or "minutes" in text_lower:
        return None

    if "fomcstatement" in url_lower or "fomc statement" in text_lower:
        return "statement"
    if "/newsevents/pressreleases/monetary" in url_lower:
        return "statement"
    if re.search(r"/monetarypolicy/fomc.*statement", url_lower):
        return "statement"

    return None


def discover_calendar_candidates(
    base_url: str,
    calendar_path: str,
    timeout: int,
) -> list[dict[str, Any]]:
    """Scrape an FOMC calendar page and return candidate document rows.

    Body text is NOT fetched. Caller chains into ``deduplicate_documents``
    and then ``fetch_calendar_body_text`` (or runs the agent's full pipeline).
    """

    calendar_url = urljoin(f"{base_url}/", calendar_path.lstrip("/"))
    response = requests.get(calendar_url, timeout=timeout)
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")
    candidates: list[dict[str, Any]] = []

    for link in soup.find_all("a", href=True):
        href = str(link["href"])
        full_url = urljoin(f"{base_url}/", href)
        link_text = link.get_text(" ", strip=True)
        doc_type = classify_doc_type_from_url(full_url, link_text)
        release_date_str = extract_release_date_from_url(full_url)
        if doc_type is None or release_date_str is None:
            continue

        candidates.append(
            {
                "central_bank": "FED",
                "doc_type": doc_type,
                "release_date": release_date_str,
                "url": full_url,
                "raw_text": None,
                "processed": 0,
            }
        )

    return candidates


def deduplicate_documents(
    documents: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Dedupe by (bank, doc_type, release_date), preferring HTML over PDF."""

    deduped: dict[tuple[str, str, str], dict[str, Any]] = {}
    for doc in documents:
        key = (doc["central_bank"], doc["doc_type"], doc["release_date"])
        existing = deduped.get(key)
        if existing is None:
            deduped[key] = doc
            continue
        if is_html(doc["url"]) and not is_html(existing.get("url", "")):
            deduped[key] = doc

    return sorted(
        deduped.values(), key=lambda d: d["release_date"], reverse=True
    )


def fetch_calendar_body_text(
    documents: list[dict[str, Any]], timeout: int
) -> list[dict[str, Any]]:
    """Fetch HTML body for each calendar candidate and clean it."""

    out: list[dict[str, Any]] = []
    for doc in documents:
        if is_html(doc["url"]):
            response = requests.get(doc["url"], timeout=timeout)
            response.raise_for_status()
            doc["raw_text"] = clean_fed_text(
                clean_html_text(response.text), doc["doc_type"]
            )
        out.append(doc)
    return out


def fetch_from_calendar(
    base_url: str,
    calendar_path: str,
    timeout: int,
) -> list[dict[str, Any]]:
    candidates = discover_calendar_candidates(base_url, calendar_path, timeout=timeout)
    deduped = deduplicate_documents(candidates)
    return fetch_calendar_body_text(deduped, timeout=timeout)


# ---------------------------------------------------------------------------
# mode: historical (legacy fomchistorical{year}.htm HTML scraping)
# ---------------------------------------------------------------------------


def _meeting_panels(history_html: str) -> list[Tag]:
    soup = BeautifulSoup(history_html, "html.parser")
    headings = soup.select("h5.panel-heading")
    return [heading.parent for heading in headings if isinstance(heading.parent, Tag)]


def _discover_documents_for_year(
    base_url: str, year: int, timeout: int
) -> list[tuple[str, str]]:
    page_url = f"{base_url}/monetarypolicy/fomchistorical{year}.htm"
    response = requests.get(page_url, timeout=timeout)
    response.raise_for_status()
    discovered: list[tuple[str, str]] = []

    for panel in _meeting_panels(response.text):
        for paragraph in panel.find_all("p"):
            paragraph_text = paragraph.get_text(" ", strip=True)
            for link in paragraph.find_all("a", href=True):
                label = link.get_text(" ", strip=True)
                full_url = urljoin(base_url, str(link["href"]))
                if label == "Statement":
                    discovered.append(("statement", full_url))
                    continue
                if "Minutes" in paragraph_text and label == "HTML":
                    pass  # minutes removed

    deduped = []
    seen: set[tuple[str, str]] = set()
    for doc_type, url in discovered:
        key = (doc_type, url)
        if key in seen:
            continue
        seen.add(key)
        deduped.append((doc_type, url))
    return deduped


def fetch_historical_html_pages(
    base_url: str,
    start_year: int,
    end_year: int,
    timeout: int,
) -> list[dict[str, Any]]:
    """Scrape fomchistorical{year}.htm for legacy 1994-2014 documents."""

    documents: list[dict[str, Any]] = []
    for year in range(start_year, end_year + 1):
        try:
            links = _discover_documents_for_year(base_url, year, timeout=timeout)
        except Exception as exc:
            print(f"[skip] {year}: failed to load history page: {exc}")
            continue
        print(f"{year}: discovered {len(links)} statement/minutes HTML links")

        for doc_type, url in links:
            release_date_str = extract_release_date_from_url(url)
            if not release_date_str:
                print(f"[skip] Could not infer date from {url}")
                continue
            try:
                response = requests.get(url, timeout=timeout)
                response.raise_for_status()
            except Exception as exc:
                print(f"[skip] Failed to fetch {url}: {exc}")
                continue
            raw_text = clean_fed_text(clean_html_text(response.text), doc_type)
            if not raw_text:
                print(f"[skip] Empty text for {release_date_str} {doc_type}: {url}")
                continue
            documents.append(
                {
                    "central_bank": "FED",
                    "doc_type": doc_type,
                    "release_date": release_date_str,
                    "url": url,
                    "raw_text": raw_text,
                    "processed": 0,
                }
            )

    documents.sort(
        key=lambda doc: (doc["release_date"], doc["doc_type"]), reverse=True
    )
    return documents


# ---------------------------------------------------------------------------
# agent
# ---------------------------------------------------------------------------


@dataclass
class MonitorFedAgent:
    """Federal Reserve document monitor with recent/calendar/historical modes."""

    db_path: str | Path = DEFAULT_DB_PATH
    mode: Mode = "recent"
    base_url: str = DEFAULT_BASE_URL
    calendar_path: str = DEFAULT_CALENDAR_PATH
    timeout: int = DEFAULT_TIMEOUT
    lookback_days: int = RECENT_LOOKBACK_DAYS
    historical_start_year: int = HISTORICAL_HTML_START_YEAR
    historical_end_year: int = HISTORICAL_HTML_END_YEAR
    dry_run: bool = False
    extras: dict[str, Any] = field(default_factory=dict)

    def run(self) -> list[dict[str, Any]]:
        documents = self._fetch()
        if self.dry_run:
            print(f"Prepared {len(documents)} documents. Dry run, no DB write.")
        else:
            count = save_documents(Path(self.db_path), documents)
            print(f"Inserted/updated {count} documents in {self.db_path}.")
        self._print_summary(documents)
        return documents

    def _fetch(self) -> list[dict[str, Any]]:
        if self.mode == "recent":
            cutoff = (
                datetime.today().date() - timedelta(days=self.lookback_days)
            ).isoformat()
            print(f"Fetching Fed documents with release_date >= {cutoff}")
            return fetch_via_fedtools(cutoff_date=cutoff)

        if self.mode == "calendar":
            return fetch_from_calendar(
                self.base_url, self.calendar_path, timeout=self.timeout
            )

        if self.mode == "historical":
            html_docs = fetch_historical_html_pages(
                self.base_url,
                start_year=self.historical_start_year,
                end_year=self.historical_end_year,
                timeout=self.timeout,
            )
            fedtools_docs = fetch_via_fedtools(cutoff_date=None)

            merged: dict[str, dict[str, Any]] = {}
            for doc in html_docs + fedtools_docs:
                merged.setdefault(doc["url"], doc)
            return sorted(
                merged.values(),
                key=lambda d: (d["release_date"], d["doc_type"]),
                reverse=True,
            )

        raise ValueError(f"Unknown mode: {self.mode}")

    @staticmethod
    def _print_summary(documents: list[dict[str, Any]]) -> None:
        statement_count = sum(1 for d in documents if d["doc_type"] == "statement")
        print(
            f"Prepared {len(documents)} documents "
            f"(statements: {statement_count})."
        )
        for doc in documents[:20]:
            preview = (doc.get("raw_text") or "")[:140].replace("\n", " ")
            print(
                f"{doc['release_date']} | {doc['doc_type']} | "
                f"{len(doc.get('raw_text') or '')} chars | {doc['url']}"
            )
            if preview:
                print(f"Preview: {preview}")
            print("-" * 80)
        if len(documents) > 20:
            print(f"... skipped printing {len(documents) - 20} additional rows.")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Monitor Federal Reserve documents (recent / calendar / historical)."
    )
    parser.add_argument(
        "--db",
        default=DEFAULT_DB_PATH,
        help=f"SQLite database path. Default: {DEFAULT_DB_PATH}",
    )
    parser.add_argument(
        "--mode",
        choices=("recent", "calendar", "historical"),
        default="recent",
        help="Fetch mode. recent=FedTools 90d (cron default); "
        "calendar=live FOMC calendar scrape; "
        "historical=fomchistorical pages + full FedTools backfill.",
    )
    parser.add_argument(
        "--lookback-days",
        type=int,
        default=RECENT_LOOKBACK_DAYS,
        help=f"recent-mode lookback window. Default: {RECENT_LOOKBACK_DAYS}",
    )
    parser.add_argument(
        "--start-year",
        type=int,
        default=HISTORICAL_HTML_START_YEAR,
        help=f"historical-mode HTML start year. Default: {HISTORICAL_HTML_START_YEAR}",
    )
    parser.add_argument(
        "--end-year",
        type=int,
        default=HISTORICAL_HTML_END_YEAR,
        help=f"historical-mode HTML end year. Default: {HISTORICAL_HTML_END_YEAR}",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_TIMEOUT,
        help=f"HTTP timeout (seconds). Default: {DEFAULT_TIMEOUT}",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch and summarize without writing to SQLite.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    agent = MonitorFedAgent(
        db_path=args.db,
        mode=args.mode,
        lookback_days=args.lookback_days,
        historical_start_year=args.start_year,
        historical_end_year=args.end_year,
        timeout=args.timeout,
        dry_run=args.dry_run,
    )
    agent.run()


if __name__ == "__main__":
    main()
