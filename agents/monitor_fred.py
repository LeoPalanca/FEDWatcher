"""MonitorFredAgent — FRED macro/rate ingestion.

Fetches CPILFESL, UNRATE, DGS2 (monthly avg), and FEDFUNDS from FRED,
aligns them into monthly rows, and upserts into macro_data.

Run as: python -m agents.monitor_fred [--db ...] [--start ...] [--end ...] [--dry-run]
"""

from __future__ import annotations

import argparse
import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in os.sys.path:
    os.sys.path.insert(0, str(PROJECT_ROOT))

from sources.fred import (
    DEFAULT_START_DATE,
    FredClient,
    fetch_monthly_macro_rows,
    load_dotenv_if_available,
    upsert_macro_rows,
)


DEFAULT_DB_PATH = "fedwatcher.db"
SOURCE_VALUE_FIELDS = (
    "core_cpi_index",
    "unemployment_rate",
    "us2y_yield",
    "policy_rate",
)


def ensure_database_exists(db_path: Path) -> None:
    if not db_path.exists():
        raise FileNotFoundError(
            f"Database file not found: {db_path}. Run python scripts/init_db.py first."
        )

    with sqlite3.connect(db_path) as conn:
        table = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'macro_data'"
        ).fetchone()

    if table is None:
        raise RuntimeError(
            "macro_data table is missing. Run python scripts/init_db.py with the latest schema."
        )


def delete_macro_rows_before(db_path: Path, start_month: str) -> int:
    """Remove macro_data rows earlier than the requested fetch window."""

    with sqlite3.connect(db_path) as conn:
        cursor = conn.execute(
            "DELETE FROM macro_data WHERE observation_month < ?",
            (start_month,),
        )
        conn.commit()
        return cursor.rowcount


def print_row_audit(rows: list[dict[str, float | str | None]]) -> None:
    print(f"Fetched {len(rows)} monthly FRED rows.")

    if not rows:
        return

    first = rows[0]["observation_month"]
    latest = rows[-1]
    print(f"Range: {first} -> {latest['observation_month']}.")

    interpolated = [row for row in rows if row.get("interpolated_fields")]
    print(f"Interpolated one-month gaps: {len(interpolated)} rows.")
    for row in interpolated[:10]:
        print(f"  {row['observation_month']}: {row['interpolated_fields']}")
    if len(interpolated) > 10:
        print(f"  ... {len(interpolated) - 10} more")

    missing_by_field = {
        field: [
            str(row["observation_month"])
            for row in rows
            if row.get(field) is None
        ]
        for field in SOURCE_VALUE_FIELDS
    }
    print("Remaining source-input nulls:")
    for field, months in missing_by_field.items():
        if not months:
            print(f"  {field}: 0")
            continue
        examples = ", ".join(months[:10])
        suffix = f" ... {len(months) - 10} more" if len(months) > 10 else ""
        print(f"  {field}: {len(months)} ({examples}{suffix})")

    print(
        "Latest: "
        f"{latest['observation_month']} | "
        f"CPILFESL={latest['core_cpi_index']} | "
        f"UNRATE={latest['unemployment_rate']} | "
        f"DGS2 monthly avg={latest['us2y_yield']} | "
        f"FEDFUNDS={latest['policy_rate']}"
    )


@dataclass
class MonitorFredAgent:
    """Fetch and persist monthly FRED macro/rate rows."""

    db_path: str | Path = DEFAULT_DB_PATH
    start: str = DEFAULT_START_DATE
    end: str | None = None
    dry_run: bool = False

    def run(self) -> list[dict[str, float | str | None]]:
        load_dotenv_if_available()
        db_path = Path(self.db_path)

        if not self.dry_run:
            ensure_database_exists(db_path)

        client = FredClient()
        rows = fetch_monthly_macro_rows(
            client,
            observation_start=self.start,
            observation_end=self.end,
        )

        if self.dry_run:
            print_row_audit(rows)
            print("Dry run only. SQLite was not modified.")
            return rows

        start_month = self.start[:7]
        deleted = delete_macro_rows_before(db_path, start_month)
        written = upsert_macro_rows(db_path, rows)
        print_row_audit(rows)
        if deleted:
            print(f"Deleted {deleted} macro_data rows before {start_month}.")
        print(f"Inserted/updated {written} rows in {db_path}.")
        return rows


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fetch CPILFESL, UNRATE, DGS2, and FEDFUNDS from FRED into macro_data."
    )
    parser.add_argument(
        "--db",
        default=DEFAULT_DB_PATH,
        help=f"SQLite database path. Default: {DEFAULT_DB_PATH}",
    )
    parser.add_argument(
        "--start",
        default=DEFAULT_START_DATE,
        help=f"Observation start date. Default: {DEFAULT_START_DATE}",
    )
    parser.add_argument(
        "--end",
        default=None,
        help="Observation end date, YYYY-MM-DD. Default: today.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch and audit historical rows without writing to SQLite.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    agent = MonitorFredAgent(
        db_path=args.db,
        start=args.start,
        end=args.end,
        dry_run=args.dry_run,
    )
    agent.run()


if __name__ == "__main__":
    main()
