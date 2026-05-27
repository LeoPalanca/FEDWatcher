import sqlite3
from pathlib import Path


DB_PATH = "fedwatcher.db"


def get_db_connection():
    if not Path(DB_PATH).exists():
        raise FileNotFoundError(
            f"Database file not found: {DB_PATH}. Run init_db.py first."
        )

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def read_one_document():
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT 
            id,
            central_bank,
            doc_type,
            release_date,
            url,
            raw_text,
            processed
        FROM documents
        ORDER BY release_date DESC
        LIMIT 1;
        """
    )

    document = cursor.fetchone()

    cursor.close()
    conn.close()

    return document


if __name__ == "__main__":
    doc = read_one_document()

    if doc is None:
        print("No documents found in database.")
    else:
        print("Document found:")
        print("-" * 80)
        print("ID:", doc["id"])
        print("Central bank:", doc["central_bank"])
        print("Document type:", doc["doc_type"])
        print("Release date:", doc["release_date"])
        print("URL:", doc["url"])
        print("Processed:", doc["processed"])
        print("-" * 80)

        if doc["raw_text"]:
            print("Raw text preview:")
            print(doc["raw_text"][:1000])
        else:
            print("No raw_text stored yet for this document.")
