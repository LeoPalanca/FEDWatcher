from math import isclose
import unittest

from sources.fred import (
    SERIES_CORE_CPI,
    SERIES_UNEMPLOYMENT,
    SERIES_US2Y,
    FredObservation,
    build_monthly_macro_rows,
    fetch_monthly_macro_rows,
)


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
        by_month = {row["observation_month"]: row for row in rows}

        self.assertEqual(
            by_month["2025-01"],
            {
                "observation_month": "2025-01",
                "core_cpi_index": 100.0,
                "core_cpi_mom": None,
                "core_cpi_yoy": None,
                "unemployment_rate": 4.1,
                "us2y_yield": 4.25,
                "interpolated_fields": "",
            },
        )
        self.assertEqual(
            {
                key: by_month["2025-02"][key]
                for key in [
                    "observation_month",
                    "core_cpi_index",
                    "core_cpi_yoy",
                    "unemployment_rate",
                    "us2y_yield",
                    "interpolated_fields",
                ]
            },
            {
                "observation_month": "2025-02",
                "core_cpi_index": 101.0,
                "core_cpi_yoy": None,
                "unemployment_rate": None,
                "us2y_yield": 4.15,
                "interpolated_fields": "",
            },
        )
        self.assertEqual(
            {
                key: by_month["2026-01"][key]
                for key in [
                    "observation_month",
                    "core_cpi_index",
                    "core_cpi_mom",
                    "unemployment_rate",
                    "us2y_yield",
                    "interpolated_fields",
                ]
            },
            {
                "observation_month": "2026-01",
                "core_cpi_index": 104.0,
                "core_cpi_mom": None,
                "unemployment_rate": 4.0,
                "us2y_yield": 3.95,
                "interpolated_fields": "",
            },
        )
        self.assertEqual(len(rows), 13)
        self.assertTrue(isclose(by_month["2025-02"]["core_cpi_mom"], 1.0))
        self.assertIsNone(by_month["2025-03"]["core_cpi_index"])
        self.assertIsNone(by_month["2026-01"]["core_cpi_mom"])
        self.assertTrue(isclose(by_month["2026-01"]["core_cpi_yoy"], 4.0))

    def test_build_monthly_macro_rows_interpolates_single_missing_months(self):
        rows = build_monthly_macro_rows(
            core_cpi=[
                FredObservation("2025-01-01", 100.0),
                FredObservation("2025-03-01", 104.0),
            ],
            unemployment=[
                FredObservation("2025-01-01", 4.0),
                FredObservation("2025-03-01", 4.2),
            ],
            us2y=[
                FredObservation("2025-01-31", 4.1),
                FredObservation("2025-03-31", 4.3),
            ],
        )

        self.assertEqual([row["observation_month"] for row in rows], ["2025-01", "2025-02", "2025-03"])
        self.assertTrue(isclose(rows[1]["core_cpi_index"], 102.0))
        self.assertTrue(isclose(rows[1]["core_cpi_mom"], 2.0))
        self.assertTrue(isclose(rows[1]["unemployment_rate"], 4.1))
        self.assertTrue(isclose(rows[1]["us2y_yield"], 4.2))
        self.assertEqual(
            rows[1]["interpolated_fields"],
            "core_cpi_index,unemployment_rate,us2y_yield",
        )

    def test_build_monthly_macro_rows_does_not_interpolate_multi_month_gaps(self):
        rows = build_monthly_macro_rows(
            core_cpi=[
                FredObservation("2025-01-01", 100.0),
                FredObservation("2025-04-01", 106.0),
            ],
            unemployment=[],
            us2y=[],
        )

        self.assertEqual(
            [row["observation_month"] for row in rows],
            ["2025-01", "2025-02", "2025-03", "2025-04"],
        )
        self.assertIsNone(rows[1]["core_cpi_index"])
        self.assertIsNone(rows[2]["core_cpi_index"])
        self.assertEqual(rows[1]["interpolated_fields"], "")
        self.assertEqual(rows[2]["interpolated_fields"], "")

    def test_fetch_monthly_macro_rows_uses_cpi_lookback_but_filters_output(self):
        class FakeClient:
            def __init__(self):
                self.calls = []

            def observations(
                self,
                series_id,
                observation_start,
                observation_end=None,
                frequency=None,
                aggregation_method=None,
            ):
                self.calls.append(
                    {
                        "series_id": series_id,
                        "observation_start": observation_start,
                        "observation_end": observation_end,
                        "frequency": frequency,
                        "aggregation_method": aggregation_method,
                    }
                )
                if series_id == SERIES_CORE_CPI:
                    return [
                        FredObservation("1993-01-01", 100.0),
                        FredObservation("1993-12-01", 110.0),
                        FredObservation("1994-01-01", 112.0),
                        FredObservation("1994-02-01", 113.0),
                    ]
                if series_id == SERIES_UNEMPLOYMENT:
                    return [
                        FredObservation("1994-01-01", 6.6),
                        FredObservation("1994-02-01", 6.6),
                    ]
                if series_id == SERIES_US2Y:
                    return [
                        FredObservation("1994-01-31", 4.2),
                        FredObservation("1994-02-28", 4.4),
                    ]
                return []

        client = FakeClient()
        rows = fetch_monthly_macro_rows(
            client,
            observation_start="1994-01-01",
            observation_end="1994-02-28",
        )

        self.assertEqual([row["observation_month"] for row in rows], ["1994-01", "1994-02"])
        self.assertTrue(isclose(rows[0]["core_cpi_mom"], 100 * (112.0 / 110.0 - 1)))
        self.assertTrue(isclose(rows[0]["core_cpi_yoy"], 12.0))
        self.assertEqual(rows[0]["unemployment_rate"], 6.6)
        self.assertEqual(rows[0]["us2y_yield"], 4.2)
        self.assertEqual(
            client.calls[0],
            {
                "series_id": SERIES_CORE_CPI,
                "observation_start": "1993-01-01",
                "observation_end": "1994-02-28",
                "frequency": None,
                "aggregation_method": None,
            },
        )
        self.assertEqual(client.calls[1]["observation_start"], "1994-01-01")
        self.assertEqual(client.calls[2]["observation_start"], "1994-01-01")
        self.assertEqual(client.calls[2]["frequency"], "m")
        self.assertEqual(client.calls[2]["aggregation_method"], "avg")


if __name__ == "__main__":
    unittest.main()
