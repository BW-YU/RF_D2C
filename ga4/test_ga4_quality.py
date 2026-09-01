import datetime as dt
import importlib.util
import pathlib
import unittest

from google.api_core.exceptions import NotFound


MODULE_PATH = pathlib.Path(__file__).with_name("ga4_to_bigquery.py")
SPEC = importlib.util.spec_from_file_location("ga4_to_bigquery", MODULE_PATH)
GA4 = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(GA4)


class FakeBigQuery:
    def __init__(self, tables):
        self.tables = set(tables)

    def get_table(self, table):
        if table not in self.tables:
            raise NotFound("not found")
        return object()


def api_row(brand, property_id, day, sessions, purchases=1):
    return {
        "brand": brand,
        "property_id": property_id,
        "date": day.isoformat(),
        "sessions": sessions,
        "ecommerce_purchases": purchases,
        "add_to_carts": purchases * 3,
        "checkouts": purchases * 2,
    }


class Ga4QualityTest(unittest.TestCase):
    def test_intraday_low_ratio_is_partial_and_final_is_confirmed(self):
        start = dt.date(2026, 8, 24)
        end = dt.date(2026, 8, 31)
        rows = []
        tables = set()
        for offset in range(8):
            day = start + dt.timedelta(days=offset)
            suffix = day.strftime("%Y%m%d")
            cloop_sessions = 40 if day == end else 100
            rows.append(api_row("cloop", "316130085", day, cloop_sessions))
            rows.append(api_row("sprint", "499489594", day, 20))
            cloop_kind = "events_intraday" if day == end else "events"
            tables.add(f"{GA4.GCP_PROJECT}.analytics_316130085.{cloop_kind}_{suffix}")
            tables.add(f"{GA4.GCP_PROJECT}.analytics_499489594.events_{suffix}")

        quality = GA4.build_quality_rows(
            FakeBigQuery(tables), rows, start.isoformat(), end.isoformat(), "run-1",
            loaded_at="2026-09-01T01:00:00+00:00",
        )
        by_key = {(row["brand"], row["date"]): row for row in quality}

        self.assertEqual(16, len(quality))
        self.assertEqual("partial", by_key[("cloop", end.isoformat())]["data_status"])
        self.assertAlmostEqual(0.4, by_key[("cloop", end.isoformat())]["session_ratio"])
        self.assertEqual("confirmed", by_key[("sprint", end.isoformat())]["data_status"])

    def test_absent_native_export_is_missing(self):
        day = dt.date(2026, 8, 31)
        quality = GA4.build_quality_rows(
            FakeBigQuery(set()),
            [api_row("cloop", "316130085", day, 100),
             api_row("sprint", "499489594", day, 20)],
            day.isoformat(), day.isoformat(), "run-2",
        )
        self.assertEqual({"missing"}, {row["data_status"] for row in quality})


if __name__ == "__main__":
    unittest.main()
