"""Backfill monthly FRED macro/rate data into SQLite."""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sources.fred import (
    DEFAULT_START_DATE,
    FredClient,
    fetch_monthly_macro_rows,
    load_dotenv_if_available,
    upsert_macro_rows,
)


DEFAULT_DB_PATH = Path("fedwatcher.db")


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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fetch CPILFESL, UNRATE, and DGS2 from FRED into macro_data."
    )
    parser.add_argument(
        "--db",
        default=str(DEFAULT_DB_PATH),
        help="SQLite database path. Default: fedwatcher.db",
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
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    db_path = Path(args.db)

    load_dotenv_if_available()
    ensure_database_exists(db_path)

    client = FredClient()
    rows = fetch_monthly_macro_rows(
        client,
        observation_start=args.start,
        observation_end=args.end,
    )
    written = upsert_macro_rows(db_path, rows)

    print(f"Fetched {len(rows)} monthly FRED rows.")
    print(f"Inserted/updated {written} rows in {db_path}.")

    if rows:
        latest = rows[-1]
        print(
            "Latest: "
            f"{latest['observation_month']} | "
            f"CPILFESL={latest['core_cpi_index']} | "
            f"UNRATE={latest['unemployment_rate']} | "
            f"DGS2 monthly avg={latest['us2y_yield']}"
        )


if __name__ == "__main__":
    main()
