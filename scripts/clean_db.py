import sqlite3
from pathlib import Path


DB_PATH = "fedwatcher.db"

TABLES = [
    "signals",
    "sentiment",
    "documents",
    "macro_data",
]


def clean_database():
    if not Path(DB_PATH).exists():
        raise FileNotFoundError(
            f"Database file not found: {DB_PATH}. Run this script from the project root."
        )

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    try:
        # Temporarily disable FK checks so child/parent cleanup is safe
        cursor.execute("PRAGMA foreign_keys = OFF;")

        for table in TABLES:
            cursor.execute(f"DELETE FROM {table};")

        # Reset AUTOINCREMENT counters, if they exist
        cursor.execute(
            """
            DELETE FROM sqlite_sequence
            WHERE name IN ('signals', 'sentiment', 'documents', 'macro_data');
            """
        )

        conn.commit()

        # Compact database file after deletion
        cursor.execute("VACUUM;")

    finally:
        cursor.close()
        conn.close()

    print("Database cleaned successfully. Structure kept, all data deleted.")


if __name__ == "__main__":
    clean_database()