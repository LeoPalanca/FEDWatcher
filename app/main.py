from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from .db import connect, database_path, row_to_dict


MAX_LIMIT = 1_000
SNAPSHOT_ROW_LIMIT = 10_000
SYSTEM_TABLE_PREFIXES = ("sqlite_",)

app = FastAPI(
    title="FedWatcher API",
    version="0.1.0",
    description="Read-only API for the FedWatcher SQLite database.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)


def quote_identifier(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def list_table_names() -> list[str]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type = 'table'
            ORDER BY name
            """
        ).fetchall()
    return [
        row["name"]
        for row in rows
        if not row["name"].startswith(SYSTEM_TABLE_PREFIXES)
    ]


def ensure_table(table: str) -> None:
    if table not in list_table_names():
        raise HTTPException(status_code=404, detail=f"Unknown table: {table}")


def get_columns(conn, table: str) -> list[str]:
    rows = conn.execute(
        f"PRAGMA table_info({quote_identifier(table)})").fetchall()
    return [row["name"] for row in rows]


def get_row_count(conn, table: str) -> int:
    row = conn.execute(
        f"SELECT COUNT(*) AS n FROM {quote_identifier(table)}").fetchone()
    return int(row["n"])


def has_column(conn, table: str, column: str) -> bool:
    return column in get_columns(conn, table)


def default_order_clause(conn, table: str) -> str:
    for column in ("release_date", "observation_month", "meeting_date", "created_at", "timestamp", "run_at", "id"):
        if has_column(conn, table, column):
            direction = "DESC" if column != "observation_month" else "ASC"
            return f"ORDER BY {quote_identifier(column)} {direction}"
    return ""


def fetch_rows(
    conn,
    table: str,
    columns: list[str],
    limit: int,
    offset: int = 0,
    search: str | None = None,
) -> list[dict[str, Any]]:
    params: list[Any] = []
    where = ""

    if search:
        searchable = [
            f"CAST({quote_identifier(column)} AS TEXT) LIKE ?" for column in columns]
        where = "WHERE " + " OR ".join(searchable)
        params.extend([f"%{search}%"] * len(columns))

    order_clause = default_order_clause(conn, table)
    params.extend([limit, offset])
    rows = conn.execute(
        f"""
        SELECT *
        FROM {quote_identifier(table)}
        {where}
        {order_clause}
        LIMIT ? OFFSET ?
        """,
        params,
    ).fetchall()
    return [row_to_dict(row) for row in rows]


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "database": str(database_path())}


@app.get("/api/tables")
def tables() -> dict[str, Any]:
    with connect() as conn:
        payload = []
        for table in list_table_names():
            columns = get_columns(conn, table)
            payload.append(
                {
                    "name": table,
                    "columns": columns,
                    "row_count": get_row_count(conn, table),
                }
            )
    return {"tables": payload}


@app.get("/api/tables/{table}")
def table_rows(
    table: str,
    limit: int = Query(100, ge=1, le=MAX_LIMIT),
    offset: int = Query(0, ge=0),
    search: str | None = Query(None, min_length=1),
) -> dict[str, Any]:
    ensure_table(table)
    with connect() as conn:
        columns = get_columns(conn, table)
        return {
            "table": table,
            "columns": columns,
            "row_count": get_row_count(conn, table),
            "limit": limit,
            "offset": offset,
            "rows": fetch_rows(conn, table, columns, limit=limit, offset=offset, search=search),
        }


@app.get("/api/documents")
def documents(
    limit: int = Query(100, ge=1, le=MAX_LIMIT),
    offset: int = Query(0, ge=0),
    search: str | None = Query(None, min_length=1),
) -> dict[str, Any]:
    ensure_table("documents")
    with connect() as conn:
        columns = get_columns(conn, "documents")
        rows = fetch_rows(conn, "documents", columns,
                          limit=limit, offset=offset, search=search)
        return {
            "table": "documents",
            "columns": columns,
            "row_count": get_row_count(conn, "documents"),
            "limit": limit,
            "offset": offset,
            "rows": rows,
        }


@app.get("/api/snapshot")
def snapshot(limit_per_table: int = Query(SNAPSHOT_ROW_LIMIT, ge=1, le=SNAPSHOT_ROW_LIMIT)) -> dict[str, Any]:
    """Return all public SQLite tables in the static explorer JSON shape."""

    with connect() as conn:
        data: dict[str, Any] = {}
        for table in list_table_names():
            columns = get_columns(conn, table)
            data[table] = {
                "columns": columns,
                "rows": fetch_rows(conn, table, columns, limit=limit_per_table),
            }
    return data
