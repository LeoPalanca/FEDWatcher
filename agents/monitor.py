"""MonitorAgent for Federal Reserve and FakeFed document discovery.

The historical backfill path uses FedTools in ``scripts/inital_data_download.py``.
This monitor is the source-aware live fetcher used for official Fed pages and the
FakeFed fixture site.
"""

from __future__ import annotations

import os
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv


load_dotenv()

DEFAULT_BASE_URL = "https://www.federalreserve.gov"
DEFAULT_CALENDAR_PATH = "/monetarypolicy/fomccalendars.htm"
DEFAULT_DB_PATH = "fedwatcher.db"


def extract_release_date(url: str) -> datetime | None:
    """Extract a Fed-style YYYYMMDD release date from a URL."""

    match = re.search(r"((?:19|20)\d{6})", url)
    if not match:
        return None

    try:
        return datetime.strptime(match.group(1), "%Y%m%d")
    except ValueError:
        return None


def classify_doc_type(url: str, link_text: str = "") -> str | None:
    """Classify supported FOMC document links."""

    url_lower = url.lower()
    text_lower = link_text.lower()

    if "implementation note" in text_lower or "implementationnote" in url_lower:
        return None
    if "press conference" in text_lower or "presconf" in url_lower:
        return None

    if "minutes" in url_lower or "minutes" in text_lower:
        return "minutes"

    if "fomcstatement" in url_lower or "fomc statement" in text_lower:
        return "statement"
    if "/newsevents/pressreleases/monetary" in url_lower:
        return "statement"
    if re.search(r"/monetarypolicy/fomc.*statement", url_lower):
        return "statement"

    return None


def is_html(url: str) -> bool:
    path = urlparse(url).path.lower()
    return path.endswith(".htm") or path.endswith(".html")


def canonical_key(document: dict[str, Any]) -> tuple[str, str, str]:
    release_date = document.get("release_date")
    if isinstance(release_date, datetime):
        date_part = release_date.strftime("%Y-%m-%d")
    else:
        date_part = str(release_date or document.get("url") or "unknown")

    return (
        str(document.get("central_bank") or "FED"),
        str(document.get("doc_type") or "unknown"),
        date_part,
    )


def deduplicate_documents(documents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Deduplicate by source/date/type, preferring HTML over PDF."""

    deduped: dict[tuple[str, str, str], dict[str, Any]] = {}

    for document in documents:
        key = canonical_key(document)
        existing = deduped.get(key)
        if existing is None:
            deduped[key] = document
            continue

        if is_html(document.get("url", "")) and not is_html(existing.get("url", "")):
            deduped[key] = document

    return sorted(
        deduped.values(),
        key=lambda doc: doc.get("release_date") or datetime.min,
        reverse=True,
    )


def clean_html_text(html: str) -> str:
    """Extract readable document text from a Fed-style HTML page."""

    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header", "noscript"]):
        tag.decompose()

    text = soup.get_text(separator=" ", strip=True)
    return " ".join(text.split())


@dataclass
class MonitorAgent:
    """Discover, fetch, and persist new Fed/FakeFed documents."""

    base_url: str | None = None
    calendar_path: str | None = None
    db_path: str | Path = DEFAULT_DB_PATH
    timeout: int = 30

    def __post_init__(self) -> None:
        self.base_url = (self.base_url or os.getenv("FED_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
        self.calendar_path = (
            self.calendar_path
            or os.getenv("FED_CALENDAR_PATH")
            or DEFAULT_CALENDAR_PATH
        )

    @property
    def calendar_url(self) -> str:
        return urljoin(f"{self.base_url}/", self.calendar_path.lstrip("/"))

    def run(self) -> list[dict[str, Any]]:
        """Return fetched documents after upserting them into SQLite."""

        candidates = self.fetch_candidate_documents()
        documents = deduplicate_documents(candidates)
        fetched_documents = self.fetch_document_texts(documents)
        return self.save_documents(fetched_documents)

    def fetch_candidate_documents(self) -> list[dict[str, Any]]:
        response = requests.get(self.calendar_url, timeout=self.timeout)
        response.raise_for_status()

        soup = BeautifulSoup(response.text, "html.parser")
        candidates: list[dict[str, Any]] = []

        for link in soup.find_all("a", href=True):
            href = str(link["href"])
            full_url = urljoin(f"{self.base_url}/", href)
            link_text = link.get_text(" ", strip=True)
            doc_type = classify_doc_type(full_url, link_text)
            release_date = extract_release_date(full_url)

            if doc_type is None or release_date is None:
                continue

            candidates.append(
                {
                    "central_bank": "FED",
                    "doc_type": doc_type,
                    "release_date": release_date,
                    "url": full_url,
                    "raw_text": None,
                    "processed": 0,
                }
            )

        return candidates

    def fetch_document_texts(self, documents: list[dict[str, Any]]) -> list[dict[str, Any]]:
        fetched: list[dict[str, Any]] = []

        for document in documents:
            doc = dict(document)
            if is_html(doc["url"]):
                response = requests.get(doc["url"], timeout=self.timeout)
                response.raise_for_status()
                doc["raw_text"] = clean_html_text(response.text)
            fetched.append(doc)

        return fetched

    def save_documents(self, documents: list[dict[str, Any]]) -> list[dict[str, Any]]:
        db_path = Path(self.db_path)
        if not db_path.exists():
            raise FileNotFoundError(
                f"Database file not found: {db_path}. Run python scripts/init_db.py first."
            )

        saved: list[dict[str, Any]] = []

        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON;")

            for document in documents:
                release_date = document.get("release_date")
                if isinstance(release_date, datetime):
                    release_date_value = release_date.strftime("%Y-%m-%d")
                else:
                    release_date_value = str(release_date or "")

                conn.execute(
                    """
                    INSERT INTO documents
                    (central_bank, doc_type, release_date, url, raw_text, processed)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(url) DO UPDATE SET
                        central_bank = excluded.central_bank,
                        doc_type = excluded.doc_type,
                        release_date = excluded.release_date,
                        raw_text = excluded.raw_text,
                        processed = excluded.processed
                    """,
                    (
                        document.get("central_bank", "FED"),
                        document["doc_type"],
                        release_date_value,
                        document["url"],
                        document.get("raw_text"),
                        int(bool(document.get("processed", 0))),
                    ),
                )

                row = conn.execute(
                    """
                    SELECT id, central_bank, doc_type, release_date, url, raw_text, processed
                    FROM documents
                    WHERE url = ?
                    """,
                    (document["url"],),
                ).fetchone()
                if row is not None:
                    saved.append(dict(row))

            conn.commit()

        return saved


def main() -> None:
    agent = MonitorAgent()
    documents = agent.run()
    print(f"Fetched and saved {len(documents)} documents from {agent.calendar_url}.")
    for document in documents[:20]:
        print(
            f"{document['release_date']} | {document['doc_type']} | "
            f"{len(document.get('raw_text') or '')} chars | {document['url']}"
        )


if __name__ == "__main__":
    main()
