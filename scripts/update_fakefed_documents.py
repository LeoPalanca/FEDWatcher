#!/usr/bin/env python3
"""
Download new FakeFed FOMC statements from:
https://fakefed.ellep.it/monetarypolicy/fomccalendars.htm

and insert them into the existing SQLite documents table.

Default behavior:
- Uses SQLite database: fedwatcher.db
- Downloads only statement pages linked from the FakeFed FOMC calendar
- Inserts only statements with release_date > 2026-05-21
- Skips duplicates by URL
- Inserts into the real documents table
- Leaves processed = 0 so your analyst pipeline can process it next

Run:
    python scripts/update_fakefed_documents.py

Optional:
    python scripts/update_fakefed_documents.py --db fedwatcher.db
    python scripts/update_fakefed_documents.py --dry-run
"""

import argparse
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, date
from html import unescape
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup


BASE_URL = "https://fakefed.ellep.it/"
CALENDAR_URL = "https://fakefed.ellep.it/monetarypolicy/fomccalendars.htm"

# Only insert documents created after 21.05.2026
CUTOFF_DATE = date(2026, 5, 21)

HEADERS = {
    "User-Agent": "FedWatcher-FakeFed-Ingestion/1.0",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


@dataclass(frozen=True)
class FakeFedStatement:
    title: str
    release_date: datetime
    url: str
    raw_text: str


def fetch_html(url: str) -> str | None:
    try:
        response = requests.get(url, headers=HEADERS, timeout=20)
        if response.status_code == 404:
            print(f"[WARN] 404 not found: {url}")
            return None

        response.raise_for_status()
        return response.text

    except requests.RequestException as exc:
        print(f"[WARN] Could not fetch {url}: {exc}")
        return None


def clean_text(text: str) -> str:
    text = unescape(text or "")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def parse_date_any(value: str) -> datetime | None:
    """
    Supported examples:
    - 2026-05-22
    - 22.05.2026
    - May 22, 2026
    - Press Release - May 22, 2026
    - May 22 Synthetic hawkish test statement
    """
    if not value:
        return None

    value = clean_text(value)
    value = value.replace("·", " ")
    value = value.replace("Press Release -", "")
    value = re.sub(r"\bFOMC\b", "", value, flags=re.I).strip()

    formats = [
        "%Y-%m-%d",
        "%Y/%m/%d",
        "%d.%m.%Y",
        "%d/%m/%Y",
        "%m/%d/%Y",
        "%B %d %Y",
        "%b %d %Y",
        "%B %d, %Y",
        "%b %d, %Y",
    ]

    for fmt in formats:
        try:
            return datetime.strptime(value.title(), fmt)
        except ValueError:
            pass

    # Match month-name dates inside longer text.
    # Example: "May 22 Synthetic hawkish test statement"
    match = re.search(
        r"\b("
        r"January|Jan|February|Feb|March|Mar|April|Apr|May|June|Jun|"
        r"July|Jul|August|Aug|September|Sep|October|Oct|November|Nov|December|Dec"
        r")\s+(\d{1,2})(?:,\s*|\s+)(20\d{2})?\b",
        value,
        flags=re.I,
    )

    if match:
        month_name, day_value, year_value = match.groups()
        if year_value is None:
            # Calendar rows on the 2026 page often show only "May 22".
            year_value = "2026"

        for fmt in ("%B %d %Y", "%b %d %Y"):
            try:
                return datetime.strptime(
                    f"{month_name.title()} {day_value} {year_value}",
                    fmt,
                )
            except ValueError:
                pass

    # Match ISO date inside longer text.
    match = re.search(r"\b(20\d{2})[-/](\d{1,2})[-/](\d{1,2})\b", value)
    if match:
        y, m, d = map(int, match.groups())
        return datetime(y, m, d)

    # Match European date inside longer text.
    match = re.search(r"\b(\d{1,2})[.](\d{1,2})[.](20\d{2})\b", value)
    if match:
        d, m, y = map(int, match.groups())
        return datetime(y, m, d)

    return None


def is_same_domain(url: str) -> bool:
    return urlparse(url).netloc == urlparse(BASE_URL).netloc


def is_statement_link(href: str, label: str) -> bool:
    href_l = href.lower()
    label_l = label.lower()

    return (
        "pressreleases/monetary" in href_l
        or "fomc" in href_l
        or "statement" in href_l
        or "statement" in label_l
    )


def discover_statement_links_from_calendar() -> list[str]:
    """
    Reads the FakeFed FOMC calendar and returns candidate statement HTML links.
    """
    html = fetch_html(CALENDAR_URL)
    if html is None:
        return []

    soup = BeautifulSoup(html, "html.parser")
    links: set[str] = set()

    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        label = clean_text(a.get_text(" "))

        absolute_url = urljoin(CALENDAR_URL, href)

        if not is_same_domain(absolute_url):
            continue

        if not is_statement_link(absolute_url, label):
            continue

        # We only want actual pages, not PDFs, anchors, mail links, etc.
        parsed = urlparse(absolute_url)
        if parsed.scheme not in {"http", "https"}:
            continue

        if absolute_url.lower().endswith(".pdf"):
            continue

        links.add(absolute_url.split("#")[0])

    return sorted(links)


def extract_title(soup: BeautifulSoup) -> str:
    h1 = soup.find("h1")
    if h1:
        return clean_text(h1.get_text(" "))

    title = soup.find("title")
    if title:
        return clean_text(title.get_text(" "))

    return "Federal Reserve issues FOMC statement"


def extract_release_date(soup: BeautifulSoup, page_text: str, url: str) -> datetime | None:
    """
    Priority:
    1. Page text like "Press Release - May 22, 2026"
    2. Any date in page text
    3. Date encoded in URL like monetary20260522a.htm
    """
    release_match = re.search(
        r"Press Release\s*-\s*([A-Za-z]+\s+\d{1,2},\s+20\d{2})",
        page_text,
        flags=re.I,
    )
    if release_match:
        parsed = parse_date_any(release_match.group(1))
        if parsed:
            return parsed

    parsed = parse_date_any(page_text)
    if parsed:
        return parsed

    url_match = re.search(r"monetary(20\d{2})(\d{2})(\d{2})[a-z]?\.htm", url, flags=re.I)
    if url_match:
        y, m, d = map(int, url_match.groups())
        return datetime(y, m, d)

    return None


def extract_main_statement_text(soup: BeautifulSoup) -> str:
    """
    Removes navigation and footer noise, then keeps the page body text.
    This is enough for the FakeFed pages because the statement content is plain HTML text.
    """
    for tag in soup(["script", "style", "nav", "footer", "header"]):
        tag.decompose()

    text = clean_text(soup.get_text(" "))
    return text


def scrape_statement_page(url: str) -> FakeFedStatement | None:
    html = fetch_html(url)
    if html is None:
        return None

    soup = BeautifulSoup(html, "html.parser")
    title = extract_title(soup)
    page_text = extract_main_statement_text(soup)
    release_date = extract_release_date(soup, page_text, url)

    if release_date is None:
        print(f"[WARN] Could not parse release date from: {url}")
        return None

    if "statement" not in f"{title} {page_text}".lower():
        print(f"[SKIP] Page does not look like a statement: {url}")
        return None

    raw_text = f"{title}\n\n{page_text}"

    return FakeFedStatement(
        title=title,
        release_date=release_date,
        url=url,
        raw_text=raw_text,
    )


def scrape_fakefed_statements() -> list[FakeFedStatement]:
    statement_links = discover_statement_links_from_calendar()

    if not statement_links:
        print("[WARN] No statement links found on FakeFed calendar.")
        return []

    statements: list[FakeFedStatement] = []

    for url in statement_links:
        statement = scrape_statement_page(url)
        if statement:
            statements.append(statement)

    # Deduplicate by URL.
    by_url: dict[str, FakeFedStatement] = {}
    for statement in statements:
        by_url[statement.url] = statement

    return sorted(by_url.values(), key=lambda s: s.release_date, reverse=True)


def ensure_documents_table_exists(conn: sqlite3.Connection) -> None:
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT name
        FROM sqlite_master
        WHERE type = 'table'
          AND name = 'documents'
        """
    )

    if cursor.fetchone() is None:
        raise RuntimeError(
            "Table 'documents' does not exist. Run your existing DB initializer first."
        )


def get_documents_columns(conn: sqlite3.Connection) -> set[str]:
    cursor = conn.cursor()
    cursor.execute("PRAGMA table_info(documents)")
    rows = cursor.fetchall()
    return {row[1] for row in rows}


def document_already_exists(conn: sqlite3.Connection, url: str) -> bool:
    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM documents WHERE url = ? LIMIT 1", (url,))
    return cursor.fetchone() is not None


def insert_document(conn: sqlite3.Connection, statement: FakeFedStatement) -> None:
    """
    Inserts using the standard FedWatcher documents columns:
    central_bank, doc_type, release_date, url, raw_text, processed

    If your local table has an autoincrement id column, SQLite will handle it automatically.
    """
    columns = get_documents_columns(conn)

    required_columns = {
        "central_bank",
        "doc_type",
        "release_date",
        "url",
        "raw_text",
        "processed",
    }

    missing_columns = required_columns - columns
    if missing_columns:
        raise RuntimeError(
            f"documents table is missing required columns: {sorted(missing_columns)}"
        )

    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO documents (
            central_bank,
            doc_type,
            release_date,
            url,
            raw_text,
            processed
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            "FED",
            "statement",
            statement.release_date.strftime("%Y-%m-%d %H:%M:%S"),
            statement.url,
            statement.raw_text,
            0,
        ),
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--db",
        default="fedwatcher.db",
        help="Path to SQLite database file. Default: fedwatcher.db",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be inserted, but do not write to DB.",
    )
    args = parser.parse_args()

    statements = scrape_fakefed_statements()

    eligible = [
        statement
        for statement in statements
        if statement.release_date.date() > CUTOFF_DATE
    ]

    if not eligible:
        print("[OK] No FakeFed statements found after 2026-05-21.")
        return

    conn = sqlite3.connect(args.db)

    try:
        ensure_documents_table_exists(conn)

        inserted = 0
        skipped = 0

        for statement in eligible:
            if document_already_exists(conn, statement.url):
                skipped += 1
                print(
                    f"[SKIP] Already in DB: "
                    f"{statement.release_date.date()} | {statement.url}"
                )
                continue

            print(
                f"[NEW] {statement.release_date.date()} | "
                f"{statement.title} | {statement.url}"
            )

            if not args.dry_run:
                insert_document(conn, statement)
                inserted += 1

        if args.dry_run:
            print(
                f"[DRY RUN] Would insert {len(eligible) - skipped} new document(s). "
                f"Skipped {skipped} duplicate(s)."
            )
        else:
            conn.commit()
            print(
                f"[DONE] Inserted {inserted} new FakeFed statement(s). "
                f"Skipped {skipped} duplicate(s)."
            )

    except Exception:
        conn.rollback()
        raise

    finally:
        conn.close()


if __name__ == "__main__":
    main()