"""MonitorFredAgent — FRED macro/rate ingestion.

Fetches CPILFESL, UNRATE, DGS2 monthly average, and FEDFUNDS from FRED,
aligns them into monthly rows, and upserts into macro_data.

Proxy policy:
- FRED data is released with a delay.
- The latest real FRED month may be partial.
- Missing numeric values in the latest real month are copied from the previous month.
- Exactly one proxy month is created after the latest real FRED month.
- Existing over-forward proxy rows are removed.
- Proxy values are non-permanent because real FRED values overwrite them later.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


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


DB_PROXY_CANDIDATE_COLUMNS = (
    "core_cpi_index",
    "core_cpi_yoy",
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


def get_macro_data_columns(db_path: Path) -> set[str]:
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute("PRAGMA table_info(macro_data)").fetchall()

    return {row[1] for row in rows}


def delete_macro_rows_before(db_path: Path, start_month: str) -> int:
    """Remove macro_data rows earlier than the requested fetch window."""

    with sqlite3.connect(db_path) as conn:
        cursor = conn.execute(
            "DELETE FROM macro_data WHERE observation_month < ?",
            (start_month,),
        )
        conn.commit()
        return cursor.rowcount


def add_one_month(month: str) -> str:
    """Return next YYYY-MM month."""

    dt = datetime.strptime(month, "%Y-%m")

    if dt.month == 12:
        return f"{dt.year + 1:04d}-01"

    return f"{dt.year:04d}-{dt.month + 1:02d}"


def is_numeric_value(value: Any) -> bool:
    """Return True only for usable numeric values."""

    if value is None:
        return False

    if isinstance(value, bool):
        return False

    if isinstance(value, int | float):
        return True

    if isinstance(value, str):
        cleaned = value.strip().replace(",", ".")

        if cleaned in {"", "-", "—", "–", "None", "NULL", "null", "nan", "NaN"}:
            return False

        try:
            float(cleaned)
            return True
        except ValueError:
            return False

    return False


def normalize_numeric_value(value: Any) -> float | None:
    """Convert numeric-looking values to float; otherwise return None."""

    if not is_numeric_value(value):
        return None

    if isinstance(value, int | float):
        return float(value)

    return float(str(value).strip().replace(",", "."))


def has_any_source_value(row: dict[str, float | str | None]) -> bool:
    """True if the row contains at least one real numeric source value."""

    return any(is_numeric_value(row.get(field)) for field in SOURCE_VALUE_FIELDS)


def find_latest_real_fred_month(rows: list[dict[str, float | str | None]]) -> str | None:
    """
    Find the latest month returned from FRED with at least one real numeric value.

    This is the key fix:
    the proxy base must come from fetched FRED rows, not from the latest database row.
    """

    if not rows:
        return None

    sorted_rows = sorted(rows, key=lambda row: str(row["observation_month"]))

    for row in reversed(sorted_rows):
        if has_any_source_value(row):
            return str(row["observation_month"])

    return None


def fill_latest_partial_and_add_next_month_proxy(
    rows: list[dict[str, float | str | None]],
) -> list[dict[str, float | str | None]]:
    """
    Source-level proxy preparation before SQLite upsert.

    This fills missing source fields in the latest real fetched FRED month,
    then adds exactly one next-month proxy row.
    """

    if not rows:
        return rows

    rows = deepcopy(rows)
    rows.sort(key=lambda row: str(row["observation_month"]))

    latest_idx: int | None = None

    for idx in range(len(rows) - 1, -1, -1):
        if has_any_source_value(rows[idx]):
            latest_idx = idx
            break

    if latest_idx is None:
        return rows

    latest = rows[latest_idx]

    if latest_idx > 0:
        previous = rows[latest_idx - 1]
        filled_fields: list[str] = []

        for field in SOURCE_VALUE_FIELDS:
            if not is_numeric_value(latest.get(field)) and is_numeric_value(previous.get(field)):
                latest[field] = normalize_numeric_value(previous.get(field))
                filled_fields.append(field)

        if filled_fields:
            existing_note = latest.get("interpolated_fields")
            proxy_note = "proxy_filled_from_previous_month:" + ",".join(filled_fields)
            latest["interpolated_fields"] = (
                f"{existing_note}; {proxy_note}" if existing_note else proxy_note
            )

    latest_month = str(latest["observation_month"])
    next_month = add_one_month(latest_month)

    existing_months = {str(row["observation_month"]) for row in rows}

    if next_month not in existing_months:
        proxy_row = deepcopy(latest)
        proxy_row["observation_month"] = next_month

        existing_note = proxy_row.get("interpolated_fields")
        proxy_note = f"proxy_month_from_previous_month:{latest_month}"
        proxy_row["interpolated_fields"] = (
            f"{existing_note}; {proxy_note}" if existing_note else proxy_note
        )

        rows.append(proxy_row)

    rows.sort(key=lambda row: str(row["observation_month"]))
    return rows


def patch_macro_data_one_month_proxy(db_path: Path, latest_real_month: str) -> None:
    """
    Apply database-level proxy logic.

    Critical rule:
    latest_real_month comes from the fresh FRED fetch, not from macro_data.
    Therefore, if latest real data is 2026-05, only 2026-06 may be proxied.
    2026-07 and beyond are deleted.
    """

    existing_columns = get_macro_data_columns(db_path)

    if "observation_month" not in existing_columns:
        raise RuntimeError("macro_data.observation_month column is missing.")

    proxy_columns = [
        col for col in DB_PROXY_CANDIDATE_COLUMNS if col in existing_columns
    ]

    if not proxy_columns:
        print("No proxy candidate columns found in macro_data. Skipping proxy patch.")
        return

    allowed_proxy_month = add_one_month(latest_real_month)

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row

        # Remove over-forward proxy rows.
        # Example: if latest real month is 2026-05, allowed proxy is 2026-06.
        # Anything after 2026-06 must not exist.
        deleted_future = conn.execute(
            """
            DELETE FROM macro_data
            WHERE observation_month > ?
            """,
            (allowed_proxy_month,),
        ).rowcount

        if deleted_future:
            print(
                f"Deleted {deleted_future} over-forward macro_data row(s) after "
                f"{allowed_proxy_month}."
            )

        latest_row = conn.execute(
            """
            SELECT *
            FROM macro_data
            WHERE observation_month = ?
            """,
            (latest_real_month,),
        ).fetchone()

        if latest_row is None:
            print(f"Latest real month {latest_real_month} not found in macro_data.")
            conn.commit()
            return

        previous_row = conn.execute(
            """
            SELECT *
            FROM macro_data
            WHERE observation_month < ?
            ORDER BY observation_month DESC
            LIMIT 1
            """,
            (latest_real_month,),
        ).fetchone()

        if previous_row is not None:
            latest_updates = {}

            for col in proxy_columns:
                latest_value = latest_row[col]
                previous_value = previous_row[col]

                if not is_numeric_value(latest_value) and is_numeric_value(previous_value):
                    latest_updates[col] = normalize_numeric_value(previous_value)

            if latest_updates:
                set_clause = ", ".join([f"{col} = ?" for col in latest_updates])
                params = list(latest_updates.values()) + [latest_real_month]

                conn.execute(
                    f"""
                    UPDATE macro_data
                    SET {set_clause}
                    WHERE observation_month = ?
                    """,
                    params,
                )

                print(
                    f"Filled latest real month {latest_real_month} from previous month "
                    f"for: {', '.join(latest_updates.keys())}"
                )

        # Re-read latest real row after filling missing values.
        latest_row = conn.execute(
            """
            SELECT *
            FROM macro_data
            WHERE observation_month = ?
            """,
            (latest_real_month,),
        ).fetchone()

        proxy_row = conn.execute(
            """
            SELECT *
            FROM macro_data
            WHERE observation_month = ?
            """,
            (allowed_proxy_month,),
        ).fetchone()

        if proxy_row is None:
            insert_columns = ["observation_month"] + proxy_columns
            insert_values = [allowed_proxy_month]

            for col in proxy_columns:
                insert_values.append(normalize_numeric_value(latest_row[col]))

            placeholders = ", ".join(["?"] * len(insert_columns))
            column_sql = ", ".join(insert_columns)

            conn.execute(
                f"""
                INSERT INTO macro_data ({column_sql})
                VALUES ({placeholders})
                """,
                insert_values,
            )

            print(
                f"Inserted one-month proxy row {allowed_proxy_month} "
                f"from latest real month {latest_real_month}."
            )

        else:
            proxy_updates = {}

            for col in proxy_columns:
                latest_value = latest_row[col]
                proxy_value = proxy_row[col]

                if not is_numeric_value(proxy_value) and is_numeric_value(latest_value):
                    proxy_updates[col] = normalize_numeric_value(latest_value)

            if proxy_updates:
                set_clause = ", ".join([f"{col} = ?" for col in proxy_updates])
                params = list(proxy_updates.values()) + [allowed_proxy_month]

                conn.execute(
                    f"""
                    UPDATE macro_data
                    SET {set_clause}
                    WHERE observation_month = ?
                    """,
                    params,
                )

                print(
                    f"Filled proxy month {allowed_proxy_month} "
                    f"for: {', '.join(proxy_updates.keys())}"
                )

        conn.commit()


def print_latest_macro_rows(db_path: Path, limit: int = 8) -> None:
    existing_columns = get_macro_data_columns(db_path)

    preferred_columns = [
        "observation_month",
        "us2y_yield",
        "core_cpi_yoy",
        "core_cpi_index",
        "unemployment_rate",
        "policy_rate",
    ]

    selected_columns = [col for col in preferred_columns if col in existing_columns]

    if not selected_columns:
        return

    column_sql = ", ".join(selected_columns)

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            f"""
            SELECT {column_sql}
            FROM macro_data
            ORDER BY observation_month DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

    print("Latest macro_data rows after update:")

    for row in rows:
        values = " | ".join([f"{col}={row[col]}" for col in selected_columns])
        print(f"  {values}")


def print_row_audit(rows: list[dict[str, float | str | None]]) -> None:
    print(f"Fetched/prepared {len(rows)} monthly FRED rows.")

    if not rows:
        return

    first = rows[0]["observation_month"]
    latest = rows[-1]

    print(f"Range: {first} -> {latest['observation_month']}.")

    interpolated = [row for row in rows if row.get("interpolated_fields")]
    print(f"Interpolated/proxy rows: {len(interpolated)}.")

    for row in interpolated[:10]:
        print(f"  {row['observation_month']}: {row['interpolated_fields']}")

    if len(interpolated) > 10:
        print(f"  ... {len(interpolated) - 10} more")

    missing_by_field = {
        field: [
            str(row["observation_month"])
            for row in rows
            if not is_numeric_value(row.get(field))
        ]
        for field in SOURCE_VALUE_FIELDS
    }

    print("Remaining source-input non-numeric/null values:")

    for field, months in missing_by_field.items():
        if not months:
            print(f"  {field}: 0")
            continue

        examples = ", ".join(months[:10])
        suffix = f" ... {len(months) - 10} more" if len(months) > 10 else ""
        print(f"  {field}: {len(months)} ({examples}{suffix})")

    print(
        "Latest fetched/prepared row: "
        f"{latest['observation_month']} | "
        f"CPILFESL={latest.get('core_cpi_index')} | "
        f"UNRATE={latest.get('unemployment_rate')} | "
        f"DGS2 monthly avg={latest.get('us2y_yield')} | "
        f"FEDFUNDS={latest.get('policy_rate')}"
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

        raw_rows = fetch_monthly_macro_rows(
            client,
            observation_start=self.start,
            observation_end=self.end,
        )

        latest_real_month = find_latest_real_fred_month(raw_rows)

        if latest_real_month is None:
            print("No real FRED rows found. Nothing to update.")
            return raw_rows

        print(f"Latest real FRED month: {latest_real_month}")
        print(f"Allowed one-month proxy: {add_one_month(latest_real_month)}")

        rows = fill_latest_partial_and_add_next_month_proxy(raw_rows)

        if self.dry_run:
            print_row_audit(rows)
            print("Dry run only. SQLite was not modified.")
            return rows

        start_month = self.start[:7]
        deleted = delete_macro_rows_before(db_path, start_month)
        written = upsert_macro_rows(db_path, rows)

        patch_macro_data_one_month_proxy(
            db_path=db_path,
            latest_real_month=latest_real_month,
        )

        print_row_audit(rows)

        if deleted:
            print(f"Deleted {deleted} macro_data rows before {start_month}.")

        print(f"Inserted/updated {written} rows in {db_path}.")

        print_latest_macro_rows(db_path)

        return rows


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Fetch CPILFESL, UNRATE, DGS2, and FEDFUNDS from FRED into macro_data."
        )
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