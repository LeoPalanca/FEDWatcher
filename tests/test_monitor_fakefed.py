import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agents.monitor_fed import (
    classify_doc_type_from_url,
    deduplicate_documents,
    discover_calendar_candidates,
    extract_release_date_from_url,
)
from agents.monitor_fred import MonitorFredAgent


class MonitorFakeFedTests(unittest.TestCase):
    def test_discovers_fakefed_calendar_statement_links(self):
        calendar_html = Path("fakefed/monetarypolicy/fomccalendars.htm").read_text(
            encoding="utf-8"
        )

        class Response:
            text = calendar_html

            def raise_for_status(self):
                return None

        with patch("agents.monitor_fed.requests.get", return_value=Response()):
            candidates = discover_calendar_candidates(
                base_url="https://fakefed.ellep.it",
                calendar_path="/monetarypolicy/fomccalendars.htm",
                timeout=10,
            )

        urls = {doc["url"] for doc in candidates}

        self.assertEqual(len(urls), 0)
        self.assertTrue(all(doc["doc_type"] == "statement" for doc in candidates))

    def test_classifies_fed_press_release_statement_from_link_text(self):
        url = "https://fakefed.ellep.it/newsevents/pressreleases/monetary20260507a.htm"
        self.assertEqual(classify_doc_type_from_url(url, "FOMC statement"), "statement")

    def test_extracts_release_date_from_fed_style_url(self):
        url = "https://fakefed.ellep.it/newsevents/pressreleases/monetary20260507a.htm"
        self.assertEqual(extract_release_date_from_url(url), "2026-05-07")

    def test_deduplicate_prefers_html_over_pdf_for_same_document(self):
        docs = [
            {
                "central_bank": "FED",
                "doc_type": "statement",
                "release_date": "2026-05-07",
                "url": "https://fakefed.ellep.it/newsevents/pressreleases/monetary20260507a.pdf",
            },
            {
                "central_bank": "FED",
                "doc_type": "statement",
                "release_date": "2026-05-07",
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
                        policy_rate REAL,
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
                    "policy_rate": 3.0,
                    "interpolated_fields": "",
                }
            ]

            with patch("agents.monitor_fred.FredClient"), patch(
                "agents.monitor_fred.fetch_monthly_macro_rows",
                return_value=rows,
            ) as fetch_rows:
                agent = MonitorFredAgent(
                    db_path=db_path,
                    start="1994-01-01",
                    end="1994-01-31",
                )
                written_rows = agent.run()

            expected_rows = [
                rows[0],
                {
                    **rows[0],
                    "observation_month": "1994-02",
                    "interpolated_fields": (
                        "Data for this month were unavailable, "
                        "computed values are taken from the previous one"
                    ),
                }
            ]
            self.assertEqual(written_rows, expected_rows)
            fetch_rows.assert_called_once()

            with sqlite3.connect(db_path) as conn:
                saved = conn.execute(
                    """
                    SELECT observation_month, core_cpi_index, core_cpi_yoy,
                           unemployment_rate, us2y_yield, policy_rate,
                           interpolated_fields
                    FROM macro_data
                    """
                ).fetchone()

            self.assertEqual(
                saved,
                ("1994-01", 154.5, 2.93, 6.6, 4.14, 3.0, ""),
            )


if __name__ == "__main__":
    unittest.main()
