"""Incremental FedTools downloader for recent FOMC statements and minutes."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from FedTools import MonetaryPolicyCommittee
from FedTools import FederalReserveMins


DB_PATH = Path("fedwatcher.db")
LOOKBACK_DAYS = 90


def get_db_connection():
    if not DB_PATH.exists():
        raise FileNotFoundError(
            f"Database file not found: {DB_PATH}. Run python scripts/init_db.py first."
        )

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def clean_fed_text(raw_text: str, doc_type: str) -> str:
    """Remove Federal Reserve website boilerplate and keep policy-relevant body."""

    if not raw_text:
        return ""

    text = " ".join(raw_text.split())

    if doc_type == "statement":
        start_markers = [
            "For release at",
            "Recent indicators suggest",
            "Recent indicators",
            "The Committee seeks to achieve",
        ]
    elif doc_type == "minutes":
        start_markers = [
            "A meeting of the Federal Open Market Committee",
            "Minutes of the Federal Open Market Committee",
            "Developments in Financial Markets and Open Market Operations",
            "Staff Review of the Economic Situation",
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


def extract_raw_text_from_row(row_dict: dict) -> str:
    """Extract raw document text from FedTools DataFrame row."""

    return (
        row_dict.get("FOMC_Statements")
        or row_dict.get("Federal_Reserve_Mins")
        or row_dict.get("text")
        or row_dict.get("Text")
        or row_dict.get("contents")
        or row_dict.get("Contents")
        or row_dict.get("statement")
        or row_dict.get("Statement")
        or row_dict.get("minutes")
        or row_dict.get("Minutes")
        or ""
    )


def normalize_release_date(release_date) -> str | None:
    """Convert FedTools index date into YYYY-MM-DD text for SQLite."""

    if release_date is None:
        return None

    release_date_str = str(release_date)[:10]

    try:
        datetime.strptime(release_date_str, "%Y-%m-%d")
        return release_date_str
    except ValueError:
        return None


def get_cutoff_date(lookback_days: int = LOOKBACK_DAYS) -> str:
    """Return YYYY-MM-DD cutoff date for recent document filtering."""

    cutoff = datetime.today().date() - timedelta(days=lookback_days)
    return cutoff.isoformat()


def normalize_recent_fedtools_dataframe(
    df,
    doc_type: str,
    cutoff_date: str,
):
    """
    Convert FedTools DataFrame output into SQLite document rows,
    keeping only records with release_date >= cutoff_date.
    """

    documents = []

    if df is None or df.empty:
        print(f"No {doc_type} documents returned by FedTools.")
        return documents

    df = df.copy()
    df = df.sort_index(ascending=False)

    for release_date, row in df.iterrows():
        release_date_str = normalize_release_date(release_date)

        if not release_date_str:
            continue

        if release_date_str < cutoff_date:
            continue

        row_dict = row.to_dict()

        raw_text = extract_raw_text_from_row(row_dict)
        clean_text = clean_fed_text(raw_text, doc_type)

        if not clean_text:
            print(f"Skipped {doc_type} {release_date_str}: no clean text found.")
            continue

        url = f"fedtools://FED/{doc_type}/{release_date_str}"

        documents.append(
            {
                "central_bank": "FED",
                "doc_type": doc_type,
                "release_date": release_date_str,
                "url": url,
                "raw_text": clean_text,
                "processed": 0,
            }
        )

    return documents


def fetch_recent_fed_documents(lookback_days: int = LOOKBACK_DAYS):
    """
    Fetch recent FOMC statements and minutes through FedTools.

    Statements and minutes are handled independently.
    If one FedTools source fails with 403, the other source can still be saved.
    """

    cutoff_date = get_cutoff_date(lookback_days)
    print(f"Fetching Fed documents with release_date >= {cutoff_date}")

    documents = []

    try:
        statement_df = MonetaryPolicyCommittee(
            verbose=True,
            thread_num=1,
        ).find_statements()

        statement_documents = normalize_recent_fedtools_dataframe(
            statement_df,
            doc_type="statement",
            cutoff_date=cutoff_date,
        )

        documents.extend(statement_documents)

    except Exception as exc:
        print(f"WARNING: Failed to fetch FOMC statements via FedTools: {exc}")

    try:
        minutes_df = FederalReserveMins(
            verbose=True,
            thread_num=1,
        ).find_minutes()

        minutes_documents = normalize_recent_fedtools_dataframe(
            minutes_df,
            doc_type="minutes",
            cutoff_date=cutoff_date,
        )

        documents.extend(minutes_documents)

    except Exception as exc:
        print(f"WARNING: Failed to fetch FOMC minutes via FedTools: {exc}")

    documents.sort(
        key=lambda doc: doc["release_date"],
        reverse=True,
    )

    return documents


def save_documents(documents):
    """
    Insert new documents or update existing recent documents.

    processed is reset to 0 only when raw_text is refreshed.
    """

    conn = get_db_connection()
    cursor = conn.cursor()

    inserted_or_updated = 0

    for doc in documents:
        sql = """
            INSERT INTO documents
            (central_bank, doc_type, release_date, url, raw_text, processed)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(url) DO UPDATE SET
                central_bank = excluded.central_bank,
                doc_type = excluded.doc_type,
                release_date = excluded.release_date,
                raw_text = excluded.raw_text,
                processed = excluded.processed
        """

        values = (
            doc["central_bank"],
            doc["doc_type"],
            doc["release_date"],
            doc["url"],
            doc["raw_text"],
            doc["processed"],
        )

        cursor.execute(sql, values)
        inserted_or_updated += cursor.rowcount

    conn.commit()
    cursor.close()
    conn.close()

    return inserted_or_updated


def print_summary(documents, inserted_or_updated):
    statement_count = sum(1 for doc in documents if doc["doc_type"] == "statement")
    minutes_count = sum(1 for doc in documents if doc["doc_type"] == "minutes")

    print(f"Prepared {len(documents)} recent clean Fed documents.")
    print(f"Statements: {statement_count}")
    print(f"Minutes: {minutes_count}")
    print(f"Inserted/updated {inserted_or_updated} documents in database.")

    if not documents:
        print("No recent Fed documents found.")
        return

    for doc in documents:
        preview = doc["raw_text"][:140].replace("\n", " ")
        print(
            f'{doc["release_date"]} | '
            f'{doc["doc_type"]} | '
            f'{len(doc["raw_text"])} chars | '
            f'{doc["url"]}'
        )
        print(f"Preview: {preview}")
        print("-" * 80)


if __name__ == "__main__":
    documents = fetch_recent_fed_documents(lookback_days=LOOKBACK_DAYS)
    inserted_or_updated = save_documents(documents)
    print_summary(documents, inserted_or_updated)
