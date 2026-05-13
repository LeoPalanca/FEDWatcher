import unittest
import sqlite3
import tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from agents.monitor import (
    MonitorAgent,
    classify_doc_type,
    deduplicate_documents,
    extract_release_date,
)


class MonitorFakeFedTests(unittest.TestCase):
    def test_discovers_fakefed_calendar_statement_links(self):
        calendar_html = Path("fakefed/monetarypolicy/fomccalendars.htm").read_text(
            encoding="utf-8"
        )

        class Response:
            text = calendar_html

            def raise_for_status(self):
                return None

        with patch("agents.monitor.requests.get", return_value=Response()):
            agent = MonitorAgent(base_url="https://fakefed.ellep.it")
            candidates = agent.fetch_candidate_documents()

        urls = {doc["url"] for doc in candidates}

        self.assertIn(
            "https://fakefed.ellep.it/newsevents/pressreleases/monetary20260507a.htm",
            urls,
        )
        self.assertIn(
            "https://fakefed.ellep.it/newsevents/pressreleases/monetary20260318a.htm",
            urls,
        )
        self.assertTrue(all(doc["doc_type"] == "statement" for doc in candidates))

    def test_classifies_fed_press_release_statement_from_link_text(self):
        url = "https://fakefed.ellep.it/newsevents/pressreleases/monetary20260507a.htm"

        doc_type = classify_doc_type(url, "FOMC statement")

        self.assertEqual(doc_type, "statement")

    def test_extracts_release_date_from_fed_style_url(self):
        url = "https://fakefed.ellep.it/newsevents/pressreleases/monetary20260507a.htm"

        release_date = extract_release_date(url)

        self.assertEqual(release_date, datetime(2026, 5, 7))

    def test_deduplicate_prefers_html_over_pdf_for_same_document(self):
        docs = [
            {
                "central_bank": "FED",
                "doc_type": "statement",
                "release_date": datetime(2026, 5, 7),
                "url": "https://fakefed.ellep.it/newsevents/pressreleases/monetary20260507a.pdf",
            },
            {
                "central_bank": "FED",
                "doc_type": "statement",
                "release_date": datetime(2026, 5, 7),
                "url": "https://fakefed.ellep.it/newsevents/pressreleases/monetary20260507a.htm",
            },
        ]

        deduped = deduplicate_documents(docs)

        self.assertEqual(len(deduped), 1)
        self.assertTrue(deduped[0]["url"].endswith(".htm"))

    def test_refresh_macro_data_upserts_fred_rows(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "fedwatcher.db"
            with sqlite3.connect(db_path) as conn:
                conn.execute(
                    """
                    CREATE TABLE macro_data (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        observation_month TEXT UNIQUE NOT NULL,
                        core_cpi_index REAL,
                        core_cpi_mom REAL,
                        core_cpi_yoy REAL,
                        unemployment_rate REAL,
                        us2y_yield REAL,
                        interpolated_fields TEXT,
                        source TEXT DEFAULT 'FRED',
                        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                        updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                    )
                    """
                )

            rows = [
                {
                    "observation_month": "1994-01",
                    "core_cpi_index": 154.5,
                    "core_cpi_mom": 0.12,
                    "core_cpi_yoy": 2.93,
                    "unemployment_rate": 6.6,
                    "us2y_yield": 4.14,
                    "interpolated_fields": "",
                }
            ]

            with patch("agents.monitor.FredClient"), patch(
                "agents.monitor.fetch_monthly_macro_rows",
                return_value=rows,
            ) as fetch_rows:
                agent = MonitorAgent(
                    db_path=db_path,
                    macro_start="1994-01-01",
                    macro_end="1994-01-31",
                )
                written_rows = agent.refresh_macro_data()

            self.assertEqual(written_rows, rows)
            fetch_rows.assert_called_once()

            with sqlite3.connect(db_path) as conn:
                saved = conn.execute(
                    """
                    SELECT observation_month, core_cpi_index, core_cpi_yoy,
                           unemployment_rate, us2y_yield, interpolated_fields
                    FROM macro_data
                    """
                ).fetchone()

            self.assertEqual(
                saved,
                ("1994-01", 154.5, 2.93, 6.6, 4.14, ""),
            )


if __name__ == "__main__":
    unittest.main()
