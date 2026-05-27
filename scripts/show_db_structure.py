import sqlite3
from pathlib import Path


DB_PATH = "fedwatcher.db"


def show_database_structure():
    if not Path(DB_PATH).exists():
        raise FileNotFoundError(f"Database file not found: {DB_PATH}")

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT name
        FROM sqlite_master
        WHERE type = 'table'
          AND name NOT LIKE 'sqlite_%'
        ORDER BY name;
    """)

    tables = [row[0] for row in cursor.fetchall()]

    if not tables:
        print("No tables found.")
        conn.close()
        return

    print(f"Database: {DB_PATH}")
    print("=" * 80)

    for table in tables:
        print(f"\nTABLE: {table}")
        print("-" * 80)

        cursor.execute(f"PRAGMA table_info({table});")
        columns = cursor.fetchall()

        print(f"{'Column':<25} {'Type':<15} {'Not Null':<10} {'Default':<20} {'PK'}")
        print("-" * 80)

        for col in columns:
            cid, name, col_type, not_null, default_value, pk = col
            print(
                f"{name:<25} "
                f"{col_type:<15} "
                f"{str(bool(not_null)):<10} "
                f"{str(default_value):<20} "
                f"{pk}"
            )

        cursor.execute(f"SELECT COUNT(*) FROM {table};")
        count = cursor.fetchone()[0]
        print(f"\nRows: {count}")

    conn.close()


if __name__ == "__main__":
    show_database_structure()
