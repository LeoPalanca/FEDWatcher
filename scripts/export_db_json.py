import argparse
import json
import sqlite3
from pathlib import Path


DEFAULT_DB = Path("fedwatcher.db")
DEFAULT_OUTPUT = Path("fedwatcher/assets/data.json")


def table_names(conn: sqlite3.Connection) -> list[str]:
    cursor = conn.execute(
        """
        SELECT name
        FROM sqlite_master
        WHERE type = 'table'
          AND name NOT LIKE 'sqlite_%'
        ORDER BY name
        """
    )
    return [row[0] for row in cursor.fetchall()]


def table_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    cursor = conn.execute(f'PRAGMA table_info("{table}")')
    return [row[1] for row in cursor.fetchall()]


def table_rows(conn: sqlite3.Connection, table: str, columns: list[str]) -> list[dict]:
    cursor = conn.execute(f'SELECT * FROM "{table}"')
    rows = []
    for row in cursor.fetchall():
        rows.append({column: row[column] for column in columns})
    return rows


def export_database(db_path: Path) -> dict:
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        tables = {}
        for table in table_names(conn):
            columns = table_columns(conn, table)
            rows = table_rows(conn, table, columns)
            tables[table] = {
                "columns": columns,
                "rows": rows,
            }
        return tables


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Export the FedWatcher SQLite database to the static website JSON asset."
    )
    parser.add_argument("--db", default=str(DEFAULT_DB), help=f"SQLite database path. Default: {DEFAULT_DB}")
    parser.add_argument(
        "--output",
        default=str(DEFAULT_OUTPUT),
        help=f"Output JSON path. Default: {DEFAULT_OUTPUT}",
    )
    args = parser.parse_args()

    db_path = Path(args.db)
    output_path = Path(args.output)
    if not db_path.exists():
        raise SystemExit(f"Database file not found: {db_path}")

    data = export_database(db_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Exported {len(data)} tables to {output_path}")


if __name__ == "__main__":
    main()
