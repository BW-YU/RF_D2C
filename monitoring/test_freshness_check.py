import unittest

from freshness_check import summarize_ga4_quality


def row(brand, status, sessions=100, ratio=1.0):
    return {
        "brand": brand,
        "data_status": status,
        "api_sessions": sessions,
        "session_ratio": ratio,
    }


class Ga4FreshnessSummaryTest(unittest.TestCase):
    def test_both_confirmed_is_green(self):
        self.assertEqual(
            (True, False, "cloop+sprint native final 확인"),
            summarize_ga4_quality([row("cloop", "confirmed"), row("sprint", "confirmed")]),
        )

    def test_provisional_is_warning(self):
        ok, warning, detail = summarize_ga4_quality(
            [row("cloop", "provisional"), row("sprint", "confirmed")]
        )
        self.assertTrue(ok)
        self.assertTrue(warning)
        self.assertIn("cloop", detail)

    def test_partial_is_failure(self):
        ok, warning, detail = summarize_ga4_quality(
            [row("cloop", "partial", sessions=40, ratio=0.4), row("sprint", "confirmed")]
        )
        self.assertFalse(ok)
        self.assertFalse(warning)
        self.assertIn("40%", detail)

    def test_missing_brand_is_failure(self):
        ok, warning, detail = summarize_ga4_quality([row("cloop", "confirmed")])
        self.assertFalse(ok)
        self.assertFalse(warning)
        self.assertIn("sprint", detail)


if __name__ == "__main__":
    unittest.main()
