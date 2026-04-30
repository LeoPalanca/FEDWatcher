import unittest
from datetime import datetime

from agents.monitor import (
    classify_doc_type,
    deduplicate_documents,
    extract_release_date,
)


class MonitorFakeFedTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
