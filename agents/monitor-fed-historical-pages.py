"""Backfill FOMC statements/minutes from official Fed historical pages.

FedTools currently misses the 2015-2020 document range in this project. This script reads
the official Federal Reserve pages such as:

    https://www.federalreserve.gov/monetarypolicy/fomchistorical2015.htm

and inserts the statement and minutes HTML documents into SQLite.
"""

from __future__ import annotations

import argparse
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup, Tag


BASE_URL = "https://www.federalreserve.gov"
DEFAULT_DB_PATH = Path("fedwatcher.db")
DEFAULT_START_YEAR = 2015
DEFAULT_END_YEAR = 2020


@dataclass(frozen=True)
class FedDocument:
    doc_type: str
    release_date: str
    url: str
    raw_text: str


def extract_date_from_url(url: str) -> str | None:
    match = re.search(r"((?:19|20)\d{6})", url)
    if not match:
        return None
    try:
        return datetime.strptime(match.group(1), "%Y%m%d").strftime("%Y-%m-%d")
    except ValueError:
        return None


def clean_html_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header", "noscript"]):
        tag.decompose()

    main = soup.find("main") or soup.find("div", id="content") or soup
    text = main.get_text(separator=" ", strip=True)
    return " ".join(text.split())


def clean_policy_text(raw_text: str, doc_type: str) -> str:
    text = " ".join(raw_text.split())
    if not text:
        return ""

    start_markers = {
        "statement": [
            "For release at",
            "Information received since",
            "Recent indicators suggest",
            "The Committee seeks to achieve",
        ],
        "minutes": [
            "Developments in Financial Markets and Open Market Operations",
            "Staff Review of the Economic Situation",
            "A meeting of the Federal Open Market Committee",
            "Minutes of the Federal Open Market Committee",
        ],
    }.get(doc_type, [])

    start = None
    for marker in start_markers:
        pos = text.find(marker)
        if pos != -1 and (start is None or pos < start):
            start = pos
    if start is not None:
        text = text[start:]

    end_markers = [
        "Last Update:",
        "Board of Governors of the Federal Reserve System",
        "Back to Top",
        "Stay Connected",
        "Accessibility",
        "FOIA",
        "No FEAR Act",
        "Privacy Program",
    ]
    end = None
    for marker in end_markers:
        pos = text.find(marker)
        if pos != -1 and (end is None or pos < end):
            end = pos
    if end is not None:
        text = text[:end]

    return text.strip()


def fetch_html(url: str, timeout: int) -> str:
    response = requests.get(url, timeout=timeout)
    response.raise_for_status()
    return response.text


def meeting_panels(history_html: str) -> list[Tag]:
    soup = BeautifulSoup(history_html, "html.parser")
    headings = soup.select("h5.panel-heading")
    return [heading.parent for heading in headings if isinstance(heading.parent, Tag)]


def discover_documents_for_year(year: int, timeout: int) -> list[tuple[str, str]]:
    page_url = f"{BASE_URL}/monetarypolicy/fomchistorical{year}.htm"
    html = fetch_html(page_url, timeout=timeout)
    discovered: list[tuple[str, str]] = []

    for panel in meeting_panels(html):
        for paragraph in panel.find_all("p"):
            text = paragraph.get_text(" ", strip=True)

            for link in paragraph.find_all("a", href=True):
                label = link.get_text(" ", strip=True)
                full_url = urljoin(BASE_URL, str(link["href"]))

                if label == "Statement":
                    discovered.append(("statement", full_url))
                    continue

                if "Minutes" in text and label == "HTML":
                    discovered.append(("minutes", full_url))

    deduped = []
    seen = set()
    for doc_type, url in discovered:
        key = (doc_type, url)
        if key in seen:
            continue
        seen.add(key)
        deduped.append((doc_type, url))
    return deduped


def fetch_document(doc_type: str, url: str, timeout: int) -> FedDocument | None:
    release_date = extract_date_from_url(url)
    if not release_date:
        print(f"[skip] Could not infer date from {url}")
        return None

    raw_text = clean_policy_text(clean_html_text(fetch_html(url, timeout)), doc_type)
    if not raw_text:
        print(f"[skip] Empty text for {release_date} {doc_type}: {url}")
        return None

    return FedDocument(
        doc_type=doc_type,
        release_date=release_date,
        url=url,
        raw_text=raw_text,
    )


def fetch_documents(start_year: int, end_year: int, timeout: int) -> list[FedDocument]:
    documents: list[FedDocument] = []
    for year in range(start_year, end_year + 1):
        links = discover_documents_for_year(year, timeout=timeout)
        print(f"{year}: discovered {len(links)} statement/minutes HTML links")
        for doc_type, url in links:
            document = fetch_document(doc_type, url, timeout=timeout)
            if document:
                documents.append(document)

    documents.sort(key=lambda doc: (doc.release_date, doc.doc_type), reverse=True)
    return documents


def save_documents(db_path: Path, documents: list[FedDocument]) -> int:
    with sqlite3.connect(db_path) as conn:
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
                ("FED", doc.doc_type, doc.release_date, doc.url, doc.raw_text, 0),
            )
            count += cursor.rowcount
        conn.commit()
        return count


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backfill FOMC statements/minutes from official Fed historical pages."
    )
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH), help="SQLite database path.")
    parser.add_argument("--start-year", type=int, default=DEFAULT_START_YEAR)
    parser.add_argument("--end-year", type=int, default=DEFAULT_END_YEAR)
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        raise SystemExit(f"Database file not found: {db_path}")

    documents = fetch_documents(args.start_year, args.end_year, timeout=args.timeout)
    print(f"Prepared {len(documents)} documents.")
    for doc in documents[:12]:
        print(f"{doc.release_date} | {doc.doc_type} | {len(doc.raw_text)} chars | {doc.url}")
    if len(documents) > 12:
        print(f"... skipped printing {len(documents) - 12} additional rows.")

    if args.dry_run:
        return

    changed = save_documents(db_path, documents)
    print(f"Inserted/updated {changed} documents in {db_path}.")


if __name__ == "__main__":
    main()
