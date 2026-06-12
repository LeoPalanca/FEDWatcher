from __future__ import annotations

import html
import os
import re
import secrets
from datetime import date
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, field_validator

from .db import connect, database_path, row_to_dict
from .narrative import router as narrative_router


MAX_LIMIT = 1_000
SNAPSHOT_ROW_LIMIT = 10_000
SYSTEM_TABLE_PREFIXES = ("sqlite_",)
DEFAULT_FAKEFED_ROOT = Path("/var/www/fakefed")
LOCAL_FAKEFED_ROOT = Path(__file__).resolve().parents[1] / "fakefed"

app = FastAPI(
    title="FedWatcher API",
    version="0.1.0",
    description="Read-only API for the FedWatcher SQLite database.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["*"],
)

# AI-generated dashboard copy (hero + §02 Breakdown summary).
app.include_router(narrative_router)


class FakeFedStatementRequest(BaseModel):
    release_date: date
    statement_text: str = Field(min_length=20, max_length=20_000)

    @field_validator("release_date")
    @classmethod
    def validate_release_date(cls, v: date) -> date:
        if v > date(2026, 5, 31):
            raise ValueError("Release date cannot be after May 31, 2026.")
        return v


def fakefed_root() -> Path:
    configured = os.getenv("FAKEFED_ROOT")
    if configured:
        return Path(configured)
    if DEFAULT_FAKEFED_ROOT.exists():
        return DEFAULT_FAKEFED_ROOT
    return LOCAL_FAKEFED_ROOT


def require_publish_password(password: str | None) -> None:
    expected = os.getenv("FAKEFED_PUBLISH_PASSWORD")
    if not expected:
        raise HTTPException(
            status_code=503,
            detail="FAKEFED_PUBLISH_PASSWORD is not configured.",
        )

    if not password:
        raise HTTPException(status_code=401, detail="Missing publish password.")
    if not secrets.compare_digest(password, expected):
        raise HTTPException(status_code=403, detail="Invalid publish password.")


def fed_release_date(value: date) -> str:
    return value.strftime("%B %-d, %Y")


def fakefed_statement_filename(value: date) -> str:
    return f"monetary{value:%Y%m%d}a.htm"


def validate_fakefed_statement_filename(filename: str) -> str:
    if not re.fullmatch(r"monetary\d{8}a\.htm", filename):
        raise HTTPException(status_code=400, detail="Invalid FakeFed statement filename.")
    return filename


def fakefed_statement_href(filename: str) -> str:
    return f"/newsevents/pressreleases/{validate_fakefed_statement_filename(filename)}"


def statement_paragraphs(text: str) -> list[str]:
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", text)]
    return [paragraph for paragraph in paragraphs if paragraph]


def render_fakefed_statement(payload: FakeFedStatementRequest) -> str:
    body = "\n".join(
        f"        <p>{html.escape(paragraph)}</p>"
        for paragraph in statement_paragraphs(payload.statement_text)
    )
    release_date = html.escape(fed_release_date(payload.release_date))
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Federal Reserve Board - Federal Reserve issues FOMC statement</title>
  <link rel="stylesheet" href="/assets/fakefed.css">
</head>
<body>
  <div class="usa-banner">
    <div class="usa-banner__inner"><span class="flag-dot"></span> An educational test website for FedWatcher</div>
  </div>
  <div class="top-links">
    <a href="#">Recent Postings</a>
    <a href="#">Calendar</a>
    <a href="#">Publications</a>
    <a href="#">Contact</a>
  </div>
  <header class="masthead">
    <div class="masthead__inner">
      <div class="seal">FF</div>
      <div class="brand">Board of Governors of the Federal Reserve System <span>Synthetic FakeFed fixture for FedWatcher</span></div>
    </div>
  </header>
  <nav class="main-nav">
    <div class="main-nav__inner">
      <a href="#">About the Fed</a>
      <a class="active" href="#">News &amp; Events</a>
      <a href="/monetarypolicy/fomccalendars.htm">Monetary Policy</a>
      <a href="#">Supervision &amp; Regulation</a>
      <a href="#">Data</a>
    </div>
  </nav>
  <main class="page">
    <div class="breadcrumb"><a href="/">Home</a> &gt; <a href="#">News &amp; Events</a> &gt; Press Releases</div>
    <div class="layout">
      <article class="content press-release">
        <h1>Federal Reserve issues FOMC statement</h1>
        <div class="press-meta">Press Release - {release_date}</div>
        <div class="notice">Synthetic FakeFed fixture. This is fake test content for FedWatcher and is not a real Federal Reserve communication.</div>

{body}
        <p>Voting for the monetary policy action were synthetic committee members created only for this educational fixture.</p>

        <div class="release-tools">
          <a href="/monetarypolicy/fomccalendars.htm">FOMC calendar</a> |
          <a href="#">Implementation Note</a> |
          <a href="#">Press Conference</a>
        </div>
      </article>
      <aside class="sidebox">
        <h2>Related Information</h2>
        <ul>
          <li><a href="/monetarypolicy/fomccalendars.htm">Meeting calendars and information</a></li>
          <li><a href="#">Monetary Policy Principles and Practice</a></li>
          <li><a href="#">Policy Tools</a></li>
        </ul>
      </aside>
    </div>
  </main>
  <footer class="footer">
    <div class="footer__inner">
      <div>
        <h2>Board of Governors of the Federal Reserve System</h2>
        <p>Synthetic educational fixture hosted for FedWatcher.</p>
      </div>
      <div>
        <h2>Stay Connected</h2>
        <p>Fake links are present for visual parity only.</p>
      </div>
    </div>
  </footer>
</body>
</html>
"""


def latest_statement_href(root: Path, exclude: str | None = None) -> str | None:
    statement_dir = root / "newsevents" / "pressreleases"
    if not statement_dir.exists():
        return None

    filenames = sorted(
        (
            path.name
            for path in statement_dir.glob("monetary????????a.htm")
            if path.name != exclude
        ),
        reverse=True,
    )
    if not filenames:
        return None
    return fakefed_statement_href(filenames[0])


def update_fakefed_homepage_links(root: Path, href: str | None) -> None:
    index_path = root / "index.html"
    index_html = index_path.read_text(encoding="utf-8")

    if href:
        index_html = re.sub(
            r'href="/newsevents/pressreleases/monetary\d{8}a\.htm">Latest Statement',
            f'href="{href}">Latest Statement',
            index_html,
            count=1,
        )
        index_html = re.sub(
            r'href="/newsevents/pressreleases/monetary\d{8}a\.htm">Federal Reserve issues FOMC statement',
            f'href="{href}">Federal Reserve issues FOMC statement',
            index_html,
            count=1,
        )
    else:
        index_html = re.sub(
            r'\s*<a href="/newsevents/pressreleases/monetary\d{8}a\.htm">Latest Statement</a>',
            "",
            index_html,
            count=1,
        )
        index_html = re.sub(
            r'\s*<li><a href="/newsevents/pressreleases/monetary\d{8}a\.htm">Federal Reserve issues FOMC statement</a></li>',
            "",
            index_html,
            count=1,
        )

    index_path.write_text(index_html, encoding="utf-8")


def calendar_synthetic_row(payload: FakeFedStatementRequest, href: str) -> str:
    return f"""            <tr data-statement-href="{href}">
              <td class="month">{payload.release_date.strftime("%B")}</td>
              <td class="date">{payload.release_date.day}</td>
              <td class="doc-links">
                Synthetic test statement: <a href="{href}">HTML</a>
                <button type="button" class="remove-statement" data-statement-href="{href}">Remove</button><br>
                Educational FakeFed upload for scraper and dashboard testing.
              </td>
            </tr>"""


def upsert_calendar_statement(root: Path, payload: FakeFedStatementRequest, href: str) -> None:
    calendar_path = root / "monetarypolicy" / "fomccalendars.htm"
    calendar_html = calendar_path.read_text(encoding="utf-8")
    row = calendar_synthetic_row(payload, href)
    escaped_href = re.escape(href)
    specific_pattern = (
        r'            <tr data-statement-href="' + escaped_href + r'">\n'
        r'              <td class="month">[^<]+</td>\n'
        r'              <td class="date">\d+</td>\n'
        r'              <td class="doc-links">\n'
        r'                Synthetic test statement: <a href="' + escaped_href + r'">HTML</a>.*?\n'
        r'                Educational FakeFed upload for scraper and dashboard testing\.\n'
        r'              </td>\n'
        r'            </tr>'
    )

    if re.search(specific_pattern, calendar_html, flags=re.DOTALL):
        calendar_html = re.sub(specific_pattern, row, calendar_html, count=1, flags=re.DOTALL)
    else:
        calendar_html = calendar_html.replace(
            "            <tr>\n              <td class=\"month\">June</td>\n              <td class=\"date\">16-17*</td>",
            row + "\n            <tr>\n              <td class=\"month\">June</td>\n              <td class=\"date\">16-17*</td>",
        )
    calendar_path.write_text(calendar_html, encoding="utf-8")


def remove_calendar_statement(root: Path, href: str) -> bool:
    calendar_path = root / "monetarypolicy" / "fomccalendars.htm"
    calendar_html = calendar_path.read_text(encoding="utf-8")
    escaped_href = re.escape(href)
    row_pattern = (
        r'\n?            <tr(?: data-statement-href="' + escaped_href + r'")?>\n'
        r'              <td class="month">[^<]+</td>\n'
        r'              <td class="date">[^<]+</td>\n'
        r'              <td class="doc-links">\n'
        r'                Synthetic test statement: <a href="' + escaped_href + r'">HTML</a>.*?\n'
        r'                Educational FakeFed upload for scraper and dashboard testing\.\n'
        r'              </td>\n'
        r'            </tr>'
    )
    updated_html, removed = re.subn(row_pattern, "", calendar_html, count=1, flags=re.DOTALL)
    if removed:
        calendar_path.write_text(updated_html, encoding="utf-8")
    return bool(removed)


def update_fakefed_links(root: Path, payload: FakeFedStatementRequest, href: str) -> None:
    update_fakefed_homepage_links(root, href)
    upsert_calendar_statement(root, payload, href)


@app.get("/")
def root() -> dict[str, Any]:
    return {
        "service": "FedWatcher API",
        "message": "The local dashboard is served by run.py on http://127.0.0.1:8080. API endpoints live under /api/.",
        "local_dashboard": "http://127.0.0.1:8080",
        "health": "/api/health",
        "snapshot": "/api/snapshot",
        "documents": "/api/documents",
        "accountability": "/api/accountability",
        "narrative": "/api/narrative",
    }


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


@app.post("/api/fakefed/statements")
def publish_fakefed_statement(
    payload: FakeFedStatementRequest,
    x_fakefed_password: str | None = Header(default=None),
) -> dict[str, Any]:
    require_publish_password(x_fakefed_password)

    root = fakefed_root()
    if not root.exists():
        raise HTTPException(status_code=500, detail=f"FakeFed root does not exist: {root}")

    filename = fakefed_statement_filename(payload.release_date)
    statement_dir = root / "newsevents" / "pressreleases"
    statement_dir.mkdir(parents=True, exist_ok=True)
    statement_path = statement_dir / filename
    statement_path.write_text(render_fakefed_statement(payload), encoding="utf-8")

    href = f"/newsevents/pressreleases/{filename}"
    update_fakefed_links(root, payload, href)

    return {
        "status": "published",
        "release_date": payload.release_date.isoformat(),
        "filename": filename,
        "url": f"https://fakefed.ellep.it{href}",
    }


@app.delete("/api/fakefed/statements/{filename}")
def delete_fakefed_statement(
    filename: str,
    x_fakefed_password: str | None = Header(default=None),
) -> dict[str, Any]:
    require_publish_password(x_fakefed_password)

    root = fakefed_root()
    if not root.exists():
        raise HTTPException(status_code=500, detail=f"FakeFed root does not exist: {root}")

    safe_filename = validate_fakefed_statement_filename(filename)
    href = fakefed_statement_href(safe_filename)
    statement_path = root / "newsevents" / "pressreleases" / safe_filename
    file_existed = statement_path.exists()
    if file_existed:
        statement_path.unlink()

    calendar_removed = remove_calendar_statement(root, href)
    update_fakefed_homepage_links(root, latest_statement_href(root, exclude=safe_filename))

    if not file_existed and not calendar_removed:
        raise HTTPException(status_code=404, detail="FakeFed statement not found.")

    url = f"https://fakefed.ellep.it{href}"
    with connect() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM documents WHERE url = ?", (url,))
        doc_row = cursor.fetchone()
        if doc_row:
            doc_id = doc_row[0]
            for table in ["sentiment", "sentiment2", "sentiment3", "sentiment_w", "signals"]:
                try:
                    conn.execute(f"DELETE FROM {table} WHERE document_id = ?", (doc_id,))
                except Exception:
                    pass
            conn.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
            try:
                conn.execute("DELETE FROM signals")
            except Exception:
                pass

    # Regenerate signals for all remaining documents sequentially to keep EWMA consistent
    try:
        from agents.strategist import process_unprocessed_documents
        process_unprocessed_documents(db_path=database_path())
    except Exception as e:
        print(f"Warning: could not regenerate signals after deletion: {e}")

    return {
        "status": "deleted",
        "filename": safe_filename,
        "url": f"https://fakefed.ellep.it{href}",
        "calendar_removed": calendar_removed,
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
