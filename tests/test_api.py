import os
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient


class TestApi(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp_dir.name) / "fedwatcher.db"
        with sqlite3.connect(self.db_path) as conn:
            conn.executescript(
                """
                CREATE TABLE documents (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    central_bank TEXT,
                    doc_type TEXT,
                    release_date TEXT,
                    url TEXT UNIQUE,
                    raw_text TEXT,
                    processed INTEGER DEFAULT 0
                );
                CREATE TABLE macro_data (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    observation_month TEXT UNIQUE NOT NULL,
                    core_cpi_yoy REAL
                );
                INSERT INTO documents
                    (central_bank, doc_type, release_date, url, raw_text, processed)
                VALUES
                    ('FED', 'statement', '2026-05-01', 'fed://one', 'policy text', 0);
                INSERT INTO macro_data (observation_month, core_cpi_yoy)
                VALUES ('2026-04', 2.7);
                """
            )
        os.environ["FEDWATCHER_DB_PATH"] = str(self.db_path)
        os.environ["FAKEFED_PUBLISH_PASSWORD"] = "test-password"

        from app.main import app

        self.client = TestClient(app)

    def tearDown(self):
        os.environ.pop("FEDWATCHER_DB_PATH", None)
        os.environ.pop("FAKEFED_PUBLISH_PASSWORD", None)
        os.environ.pop("FAKEFED_ROOT", None)
        self.tmp_dir.cleanup()

    def test_tables_lists_database_tables(self):
        response = self.client.get("/api/tables")
        self.assertEqual(response.status_code, 200)
        tables = {table["name"]: table for table in response.json()["tables"]}
        self.assertEqual(tables["documents"]["row_count"], 1)
        self.assertIn("macro_data", tables)

    def test_table_rows_returns_columns_and_rows(self):
        response = self.client.get("/api/tables/documents")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["row_count"], 1)
        self.assertEqual(payload["rows"][0]["release_date"], "2026-05-01")

    def test_snapshot_matches_static_explorer_shape(self):
        response = self.client.get("/api/snapshot")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["documents"]["rows"][0]["doc_type"], "statement")
        self.assertEqual(payload["macro_data"]["columns"], ["id", "observation_month", "core_cpi_yoy"])

    def test_publish_fakefed_statement_writes_page_and_updates_links(self):
        fakefed_root = Path(self.tmp_dir.name) / "fakefed"
        shutil.copytree("fakefed", fakefed_root)
        os.environ["FAKEFED_ROOT"] = str(fakefed_root)

        response = self.client.post(
            "/api/fakefed/statements",
            headers={"X-FakeFed-Password": "test-password"},
            json={
                "release_date": "2026-06-10",
                "statement_text": (
                    "Inflation is extremely elevated.\n\n"
                    "The Committee decided to raise the target range by 125 basis points."
                ),
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["filename"], "monetary20260610a.htm")

        statement_path = (
            fakefed_root / "newsevents" / "pressreleases" / "monetary20260610a.htm"
        )
        statement_html = statement_path.read_text(encoding="utf-8")
        self.assertIn("Press Release - June 10, 2026", statement_html)
        self.assertIn("125 basis points", statement_html)

        index_html = (fakefed_root / "index.html").read_text(encoding="utf-8")
        calendar_html = (
            fakefed_root / "monetarypolicy" / "fomccalendars.htm"
        ).read_text(encoding="utf-8")
        self.assertIn("monetary20260610a.htm", index_html)
        self.assertIn("monetary20260610a.htm", calendar_html)
        self.assertIn('<td class="date">10</td>', calendar_html)

    def test_publish_fakefed_statement_requires_password(self):
        response = self.client.post(
            "/api/fakefed/statements",
            json={"release_date": "2026-06-10", "statement_text": "x" * 40},
        )

        self.assertEqual(response.status_code, 401)

    def test_delete_fakefed_statement_removes_file_and_calendar_row(self):
        fakefed_root = Path(self.tmp_dir.name) / "fakefed"
        shutil.copytree("fakefed", fakefed_root)
        os.environ["FAKEFED_ROOT"] = str(fakefed_root)

        response = self.client.delete(
            "/api/fakefed/statements/monetary20260604a.htm",
            headers={"X-FakeFed-Password": "test-password"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "deleted")
        self.assertFalse(
            (
                fakefed_root
                / "newsevents"
                / "pressreleases"
                / "monetary20260604a.htm"
            ).exists()
        )

        calendar_html = (
            fakefed_root / "monetarypolicy" / "fomccalendars.htm"
        ).read_text(encoding="utf-8")
        index_html = (fakefed_root / "index.html").read_text(encoding="utf-8")
        self.assertNotIn("monetary20260604a.htm", calendar_html)
        self.assertNotIn(
            'href="/newsevents/pressreleases/monetary20260604a.htm"',
            index_html,
        )
        self.assertIn("monetary20260519a.htm", index_html)

    def test_delete_fakefed_statement_requires_password(self):
        response = self.client.delete(
            "/api/fakefed/statements/monetary20260604a.htm",
        )

        self.assertEqual(response.status_code, 401)

    def test_delete_fakefed_statement_rejects_invalid_filename(self):
        response = self.client.delete(
            "/api/fakefed/statements/not-a-statement.htm",
            headers={"X-FakeFed-Password": "test-password"},
        )

        self.assertEqual(response.status_code, 400)


if __name__ == "__main__":
    unittest.main()
