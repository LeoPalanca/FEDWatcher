import sqlite3
from pathlib import Path
from datetime import datetime

from FedTools import MonetaryPolicyCommittee
from FedTools import FederalReserveMins


DB_PATH = "fedwatcher.db"
MINUTES_START_YEAR = 2015


def get_db_connection():
    if not Path(DB_PATH).exists():
        raise FileNotFoundError(
            f"Database file not found: {DB_PATH}. Run your init_db.py script first."
        )

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def clean_fed_text(raw_text: str, doc_type: str) -> str:
    """
    Remove Federal Reserve website boilerplate and keep the policy-relevant body.
    """

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
        if pos != -1:
            if best_start is None or pos < best_start:
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
        if pos != -1:
            if best_end is None or pos < best_end:
                best_end = pos

    if best_end is not None:
        text = text[:best_end]

    return text.strip()


def extract_raw_text_from_row(row_dict: dict) -> str:
    """
    FedTools columns observed:
    - Statements: FOMC_Statements
    - Minutes: Federal_Reserve_Mins
    """

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
    """
    Convert FedTools index date into YYYY-MM-DD text for SQLite.
    """

    if release_date is None:
        return None

    release_date_str = str(release_date)[:10]

    try:
        datetime.strptime(release_date_str, "%Y-%m-%d")
        return release_date_str
    except ValueError:
        return None


def normalize_fedtools_dataframe(
    df,
    doc_type: str,
    start_year: int | None = None,
):
    """
    Convert FedTools DataFrame output into rows matching the SQLite documents table.

    start_year:
    - None = keep all years
    - 2015 = keep only documents from 2015 onward
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

        release_year = int(release_date_str[:4])

        if start_year is not None and release_year < start_year:
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


def fetch_fedtools_documents():
    """
    Fetch:
    - all FOMC statements
    - FOMC minutes from 2015 onward only
    """

    documents = []

    statement_df = MonetaryPolicyCommittee(
        verbose=True,
        thread_num=1,
    ).find_statements()

    statement_documents = normalize_fedtools_dataframe(
        statement_df,
        doc_type="statement",
        start_year=None,
    )

    documents.extend(statement_documents)

    minutes_df = FederalReserveMins(
        verbose=True,
        thread_num=1,
    ).find_minutes()

    minutes_documents = normalize_fedtools_dataframe(
        minutes_df,
        doc_type="minutes",
        start_year=MINUTES_START_YEAR,
    )

    documents.extend(minutes_documents)

    documents.sort(
        key=lambda doc: doc["release_date"],
        reverse=True,
    )

    return documents


def save_documents(documents):
    """
    Insert new documents or update existing ones.

    ON CONFLICT(url) updates previously inserted dirty/old raw_text.
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

    print(f"Prepared {len(documents)} clean Fed documents.")
    print(f"Statements: {statement_count}")
    print(f"Minutes from {MINUTES_START_YEAR} onward: {minutes_count}")
    print(f"Inserted/updated {inserted_or_updated} documents in database.")

    for doc in documents[:20]:
        preview = doc["raw_text"][:140].replace("\n", " ")
        print(
            f'{doc["release_date"]} | '
            f'{doc["doc_type"]} | '
            f'{len(doc["raw_text"])} chars | '
            f'{doc["url"]}'
        )
        print(f"Preview: {preview}")
        print("-" * 80)

    if len(documents) > 20:
        print(f"... skipped printing {len(documents) - 20} additional rows.")


if __name__ == "__main__":
    documents = fetch_fedtools_documents()
    inserted_or_updated = save_documents(documents)
    print_summary(documents, inserted_or_updated)