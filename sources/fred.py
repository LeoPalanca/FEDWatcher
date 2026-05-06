"""FRED data client and monthly macro/rate transformations."""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import requests


FRED_OBSERVATIONS_URL = "https://api.stlouisfed.org/fred/series/observations"
DEFAULT_START_DATE = "2000-01-01"
SERIES_CORE_CPI = "CPILFESL"
SERIES_UNEMPLOYMENT = "UNRATE"
SERIES_US2Y = "DGS2"


@dataclass(frozen=True)
class FredObservation:
    """Single FRED observation normalized for downstream joins."""

    date: str
    value: float | None


def month_key(observation_date: str) -> str:
    """Return YYYY-MM for a FRED observation date."""

    return observation_date[:7]


def parse_fred_value(value: str) -> float | None:
    """FRED uses "." for missing observations."""

    if value == ".":
        return None
    return float(value)


def percent_change(current: float | None, previous: float | None) -> float | None:
    if current is None or previous in (None, 0):
        return None
    return 100 * (current / previous - 1)


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

        response = requests.get(FRED_OBSERVATIONS_URL, params=params, timeout=30)
        response.raise_for_status()
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
) -> list[dict[str, float | str | None]]:
    """Align CPILFESL, UNRATE, and monthly-average DGS2 into one row per month."""

    cpi_by_month = {month_key(obs.date): obs.value for obs in core_cpi}
    unemployment_by_month = {month_key(obs.date): obs.value for obs in unemployment}
    us2y_by_month = {month_key(obs.date): obs.value for obs in us2y}

    months = sorted(set(cpi_by_month) | set(unemployment_by_month) | set(us2y_by_month))
    cpi_months = sorted(cpi_by_month)
    previous_cpi_month = {
        month: cpi_months[index - 1] if index > 0 else None
        for index, month in enumerate(cpi_months)
    }
    rows: list[dict[str, float | str | None]] = []

    for month in months:
        cpi_value = cpi_by_month.get(month)
        previous_month = previous_cpi_month.get(month)
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
            }
        )

    return rows


def fetch_monthly_macro_rows(
    client: FredClient,
    observation_start: str = DEFAULT_START_DATE,
    observation_end: str | None = None,
) -> list[dict[str, float | str | None]]:
    """Fetch and align the core monthly FedWatcher FRED inputs."""

    if observation_end is None:
        observation_end = datetime.today().date().isoformat()

    core_cpi = client.observations(
        SERIES_CORE_CPI,
        observation_start=observation_start,
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

    return build_monthly_macro_rows(core_cpi, unemployment, us2y)


def upsert_macro_rows(db_path: Path, rows: list[dict[str, float | str | None]]) -> int:
    """Insert or update rows in the SQLite macro_data table."""

    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.cursor()
        cursor.executemany(
            """
            INSERT INTO macro_data (
                observation_month,
                core_cpi_index,
                core_cpi_mom,
                core_cpi_yoy,
                unemployment_rate,
                us2y_yield
            )
            VALUES (
                :observation_month,
                :core_cpi_index,
                :core_cpi_mom,
                :core_cpi_yoy,
                :unemployment_rate,
                :us2y_yield
            )
            ON CONFLICT(observation_month) DO UPDATE SET
                core_cpi_index = excluded.core_cpi_index,
                core_cpi_mom = excluded.core_cpi_mom,
                core_cpi_yoy = excluded.core_cpi_yoy,
                unemployment_rate = excluded.unemployment_rate,
                us2y_yield = excluded.us2y_yield,
                updated_at = CURRENT_TIMESTAMP
            """,
            rows,
        )
        conn.commit()
        return cursor.rowcount
    finally:
        conn.close()
