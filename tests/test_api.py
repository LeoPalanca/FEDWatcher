import os
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

        from app.main import app

        self.client = TestClient(app)

    def tearDown(self):
        os.environ.pop("FEDWATCHER_DB_PATH", None)
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


if __name__ == "__main__":
    unittest.main()
