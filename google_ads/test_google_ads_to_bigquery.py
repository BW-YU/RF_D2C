import os
import unittest
from datetime import date
from unittest.mock import patch

from google_ads.google_ads_to_bigquery import date_window, parse_accounts, required_env


class GoogleAdsCollectorTest(unittest.TestCase):
    def test_parse_accounts_normalizes_ids(self):
        self.assertEqual(
            parse_accounts("258-001-5098:cloop,2082026590:sprint"),
            [("2580015098", "cloop"), ("2082026590", "sprint")],
        )

    def test_parse_accounts_rejects_invalid_contract(self):
        with self.assertRaises(ValueError):
            parse_accounts("2580015098")

    def test_default_window_includes_today_and_previous_two_days(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("START_DATE", None)
            os.environ.pop("END_DATE", None)
            self.assertEqual(
                date_window(date(2026, 8, 31)),
                (date(2026, 8, 29), date(2026, 8, 31)),
            )

    def test_explicit_window(self):
        with patch.dict(
            os.environ, {"START_DATE": "2026-08-30", "END_DATE": "2026-08-30"}
        ):
            self.assertEqual(
                date_window(date(2026, 8, 31)),
                (date(2026, 8, 30), date(2026, 8, 30)),
            )

    def test_required_env_fails_closed(self):
        with patch.dict(os.environ, {"MISSING_TEST_SECRET": ""}):
            with self.assertRaises(RuntimeError):
                required_env("MISSING_TEST_SECRET")


if __name__ == "__main__":
    unittest.main()
