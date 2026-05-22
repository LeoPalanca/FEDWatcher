from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from urllib.parse import unquote


DEFAULT_DB_PATH = Path(__file__).resolve().parents[1] / "fedwatcher.db"


def database_path() -> Path:
    """Resolve the SQLite database path from environment configuration."""

    configured = os.getenv("FEDWATCHER_DB_PATH") or os.getenv("DATABASE_URL")
    if not configured:
        return DEFAULT_DB_PATH

    if configured.startswith("sqlite:///"):
        path = unquote(configured.removeprefix("sqlite:///"))
        if not path:
            return DEFAULT_DB_PATH
        resolved = Path(path)
    else:
        resolved = Path(configured)

    if not resolved.is_absolute():
        resolved = DEFAULT_DB_PATH.parent / resolved
    return resolved


def connect() -> sqlite3.Connection:
    db_path = database_path()
    if not db_path.exists():
        raise FileNotFoundError(f"SQLite database not found: {db_path}")

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def row_to_dict(row: sqlite3.Row) -> dict:
    return {key: row[key] for key in row.keys()}
