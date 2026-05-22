"""FRED data client and monthly macro/rate transformations."""

from __future__ import annotations

import os
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import requests


FRED_OBSERVATIONS_URL = "https://api.stlouisfed.org/fred/series/observations"
DEFAULT_START_DATE = "1994-01-01"
SERIES_CORE_CPI = "CPILFESL"
SERIES_UNEMPLOYMENT = "UNRATE"
SERIES_US2Y = "DGS2"
SERIES_FEDFUNDS = "FEDFUNDS"
MACRO_VALUE_FIELDS = (
    "core_cpi_index",
    "unemployment_rate",
    "us2y_yield",
    "policy_rate",
)


@dataclass(frozen=True)
class FredObservation:
    """Single FRED observation normalized for downstream joins."""

    date: str
    value: float | None


def month_key(observation_date: str) -> str:
    """Return YYYY-MM for a FRED observation date."""

    return observation_date[:7]


def next_month(month: str) -> str:
    """Return the month after a YYYY-MM key."""

    year = int(month[:4])
    month_number = int(month[5:7])
    if month_number == 12:
        return f"{year + 1}-01"
    return f"{year}-{month_number + 1:02d}"


def previous_year_date(date_text: str) -> str:
    """Return the same YYYY-MM-DD date one year earlier."""

    date = datetime.strptime(date_text, "%Y-%m-%d").date()
    try:
        previous = date.replace(year=date.year - 1)
    except ValueError:
        previous = date.replace(year=date.year - 1, day=28)
    return previous.isoformat()


def complete_month_range(months: list[str]) -> list[str]:
    """Return a continuous monthly range spanning the provided YYYY-MM keys."""

    if not months:
        return []

    month = min(months)
    end = max(months)
    out: list[str] = []
    while month <= end:
        out.append(month)
        month = next_month(month)
    return out


def parse_fred_value(value: str) -> float | None:
    """FRED uses "." for missing observations."""

    if value == ".":
        return None
    return float(value)


def percent_change(current: float | None, previous: float | None) -> float | None:
    if current is None or previous in (None, 0):
        return None
    return 100 * (current / previous - 1)


def interpolate_single_month_gaps(
    values_by_month: dict[str, float | None],
    months: list[str],
) -> tuple[dict[str, float | None], set[str]]:
    """Fill one-month gaps by averaging the adjacent monthly observations."""

    filled = dict(values_by_month)
    interpolated_months: set[str] = set()

    for index in range(1, len(months) - 1):
        month = months[index]
        if filled.get(month) is not None:
            continue

        previous_value = values_by_month.get(months[index - 1])
        next_value = values_by_month.get(months[index + 1])
        if previous_value is None or next_value is None:
            continue

        filled[month] = (previous_value + next_value) / 2
        interpolated_months.add(month)

    return filled, interpolated_months


def load_dotenv_if_available(path: Path = Path(".env")) -> None:
    """Load simple KEY=VALUE pairs without requiring python-dotenv at runtime."""

    if not path.exists():
        return

    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue

        key, value = stripped.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


class FredClient:
    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key or os.environ.get("FRED_API_KEY")
        if not self.api_key:
            raise ValueError("FRED_API_KEY is required. Add it to .env or the environment.")

    def observations(
        self,
        series_id: str,
        observation_start: str = DEFAULT_START_DATE,
        observation_end: str | None = None,
        frequency: str | None = None,
        aggregation_method: str | None = None,
    ) -> list[FredObservation]:
        params: dict[str, Any] = {
            "series_id": series_id,
            "api_key": self.api_key,
            "file_type": "json",
            "observation_start": observation_start,
        }

        if observation_end:
            params["observation_end"] = observation_end
        if frequency:
            params["frequency"] = frequency
        if aggregation_method:
            params["aggregation_method"] = aggregation_method

        response = None
        for attempt in range(3):
            response = requests.get(FRED_OBSERVATIONS_URL, params=params, timeout=30)
            if response.status_code not in {500, 502, 503, 504}:
                break
            if attempt < 2:
                time.sleep(0.5 * (attempt + 1))

        if response is None:
            raise RuntimeError(f"FRED observations request failed for {series_id}")
        if not response.ok:
            raise RuntimeError(
                f"FRED observations request failed for {series_id}: HTTP {response.status_code}"
            )
        payload = response.json()

        if "error_message" in payload:
            raise RuntimeError(payload["error_message"])

        return [
            FredObservation(
                date=item["date"],
                value=parse_fred_value(item["value"]),
            )
            for item in payload.get("observations", [])
        ]


def build_monthly_macro_rows(
    core_cpi: list[FredObservation],
    unemployment: list[FredObservation],
    us2y: list[FredObservation],
    fed_funds: list[FredObservation] | None = None,
    output_start: str | None = None,
) -> list[dict[str, float | str | None]]:
    """Align CPILFESL, UNRATE, monthly-average DGS2, and FEDFUNDS into one row per month."""

    fed_funds = fed_funds or []

    cpi_by_month = {month_key(obs.date): obs.value for obs in core_cpi}
    unemployment_by_month = {month_key(obs.date): obs.value for obs in unemployment}
    us2y_by_month = {month_key(obs.date): obs.value for obs in us2y}
    fed_funds_by_month = {month_key(obs.date): obs.value for obs in fed_funds}

    observed_months = sorted(
        set(cpi_by_month)
        | set(unemployment_by_month)
        | set(us2y_by_month)
        | set(fed_funds_by_month)
    )
    months = complete_month_range(observed_months)
    interpolated_by_month = {month: set() for month in months}

    cpi_by_month, cpi_interpolated = interpolate_single_month_gaps(cpi_by_month, months)
    unemployment_by_month, unemployment_interpolated = interpolate_single_month_gaps(
        unemployment_by_month,
        months,
    )
    us2y_by_month, us2y_interpolated = interpolate_single_month_gaps(us2y_by_month, months)
    fed_funds_by_month, fed_funds_interpolated = interpolate_single_month_gaps(
        fed_funds_by_month,
        months,
    )

    for month in cpi_interpolated:
        interpolated_by_month[month].add("core_cpi_index")
    for month in unemployment_interpolated:
        interpolated_by_month[month].add("unemployment_rate")
    for month in us2y_interpolated:
        interpolated_by_month[month].add("us2y_yield")
    for month in fed_funds_interpolated:
        interpolated_by_month[month].add("policy_rate")

    rows: list[dict[str, float | str | None]] = []

    for index, month in enumerate(months):
        cpi_value = cpi_by_month.get(month)
        previous_month = months[index - 1] if index > 0 else None
        previous_cpi = cpi_by_month.get(previous_month) if previous_month else None
        prior_year_month = f"{int(month[:4]) - 1}{month[4:]}"
        prior_year_cpi = cpi_by_month.get(prior_year_month)

        rows.append(
            {
                "observation_month": month,
                "core_cpi_index": cpi_value,
                "core_cpi_mom": percent_change(cpi_value, previous_cpi),
                "core_cpi_yoy": percent_change(cpi_value, prior_year_cpi),
                "unemployment_rate": unemployment_by_month.get(month),
                "us2y_yield": us2y_by_month.get(month),
                "policy_rate": fed_funds_by_month.get(month),
                "interpolated_fields": ",".join(
                    field for field in MACRO_VALUE_FIELDS if field in interpolated_by_month[month]
                ),
            }
        )

    if output_start is None:
        return rows

    output_start_month = month_key(output_start)
    return [
        row
        for row in rows
        if str(row["observation_month"]) >= output_start_month
    ]


def fetch_monthly_macro_rows(
    client: FredClient,
    observation_start: str = DEFAULT_START_DATE,
    observation_end: str | None = None,
) -> list[dict[str, float | str | None]]:
    """Fetch and align the core monthly FedWatcher FRED inputs."""

    if observation_end is None:
        observation_end = datetime.today().date().isoformat()

    cpi_start = previous_year_date(observation_start)
    core_cpi = client.observations(
        SERIES_CORE_CPI,
        observation_start=cpi_start,
        observation_end=observation_end,
    )
    unemployment = client.observations(
        SERIES_UNEMPLOYMENT,
        observation_start=observation_start,
        observation_end=observation_end,
    )
    us2y = client.observations(
        SERIES_US2Y,
        observation_start=observation_start,
        observation_end=observation_end,
        frequency="m",
        aggregation_method="avg",
    )
    fed_funds = client.observations(
        SERIES_FEDFUNDS,
        observation_start=observation_start,
        observation_end=observation_end,
    )

    return build_monthly_macro_rows(
        core_cpi,
        unemployment,
        us2y,
        fed_funds=fed_funds,
        output_start=observation_start,
    )


def upsert_macro_rows(db_path: Path, rows: list[dict[str, float | str | None]]) -> int:
    """Insert or update rows in the SQLite macro_data table."""

    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.cursor()
        ensure_macro_data_columns(cursor)
        cursor.executemany(
            """
            INSERT INTO macro_data (
                observation_month,
                core_cpi_index,
                core_cpi_mom,
                core_cpi_yoy,
                unemployment_rate,
                us2y_yield,
                policy_rate,
                interpolated_fields
            )
            VALUES (
                :observation_month,
                :core_cpi_index,
                :core_cpi_mom,
                :core_cpi_yoy,
                :unemployment_rate,
                :us2y_yield,
                :policy_rate,
                :interpolated_fields
            )
            ON CONFLICT(observation_month) DO UPDATE SET
                core_cpi_index = excluded.core_cpi_index,
                core_cpi_mom = excluded.core_cpi_mom,
                core_cpi_yoy = excluded.core_cpi_yoy,
                unemployment_rate = excluded.unemployment_rate,
                us2y_yield = excluded.us2y_yield,
                policy_rate = excluded.policy_rate,
                interpolated_fields = excluded.interpolated_fields,
                updated_at = CURRENT_TIMESTAMP
            """,
            rows,
        )
        conn.commit()
        return cursor.rowcount
    finally:
        conn.close()


def ensure_macro_data_columns(cursor: sqlite3.Cursor) -> None:
    """Add lightweight macro_data columns needed by newer fetchers."""

    columns = {
        row[1]
        for row in cursor.execute("PRAGMA table_info(macro_data)").fetchall()
    }
    if "interpolated_fields" not in columns:
        cursor.execute("ALTER TABLE macro_data ADD COLUMN interpolated_fields TEXT")
    if "policy_rate" not in columns:
        cursor.execute("ALTER TABLE macro_data ADD COLUMN policy_rate REAL")
