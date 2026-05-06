from math import isclose
import unittest

from sources.fred import FredObservation, build_monthly_macro_rows


class FredSourceTest(unittest.TestCase):
    def test_build_monthly_macro_rows_aligns_series_and_calculates_cpi_changes(self):
        rows = build_monthly_macro_rows(
            core_cpi=[
                FredObservation("2025-01-01", 100.0),
                FredObservation("2025-02-01", 101.0),
                FredObservation("2026-01-01", 104.0),
            ],
            unemployment=[
                FredObservation("2025-01-01", 4.1),
                FredObservation("2026-01-01", 4.0),
            ],
            us2y=[
                FredObservation("2025-01-31", 4.25),
                FredObservation("2025-02-28", 4.15),
                FredObservation("2026-01-31", 3.95),
            ],
        )

        self.assertEqual(
            rows[0],
            {
                "observation_month": "2025-01",
                "core_cpi_index": 100.0,
                "core_cpi_mom": None,
                "core_cpi_yoy": None,
                "unemployment_rate": 4.1,
                "us2y_yield": 4.25,
            },
        )
        self.assertEqual(
            {
                key: rows[1][key]
                for key in [
                    "observation_month",
                    "core_cpi_index",
                    "core_cpi_yoy",
                    "unemployment_rate",
                    "us2y_yield",
                ]
            },
            {
                "observation_month": "2025-02",
                "core_cpi_index": 101.0,
                "core_cpi_yoy": None,
                "unemployment_rate": None,
                "us2y_yield": 4.15,
            },
        )
        self.assertEqual(
            {
                key: rows[2][key]
                for key in [
                    "observation_month",
                    "core_cpi_index",
                    "unemployment_rate",
                    "us2y_yield",
                ]
            },
            {
                "observation_month": "2026-01",
                "core_cpi_index": 104.0,
                "unemployment_rate": 4.0,
                "us2y_yield": 3.95,
            },
        )
        self.assertTrue(isclose(rows[1]["core_cpi_mom"], 1.0))
        self.assertTrue(isclose(rows[2]["core_cpi_mom"], 2.9702970297))
        self.assertTrue(isclose(rows[2]["core_cpi_yoy"], 4.0))


if __name__ == "__main__":
    unittest.main()
