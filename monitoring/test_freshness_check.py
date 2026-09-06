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

    def test_partial_is_warning(self):
        # GA4 D-1은 하루 중 여러 차례 롤링 재적재된다. "partial"은 아직 native
        # daily(final) 테이블로 안 바뀐 정상 과도기라 STALE(실패)이 아니라 WARN.
        # 260906: 이 케이스가 STALE로 잘못 실패해 5회 연속 오탐 발생 → 수정.
        ok, warning, detail = summarize_ga4_quality(
            [row("cloop", "partial", sessions=40, ratio=0.4), row("sprint", "confirmed")]
        )
        self.assertTrue(ok)
        self.assertTrue(warning)
        self.assertIn("cloop", detail)

    def test_confirmed_with_low_ratio_is_failure(self):
        # native final 테이블은 있어도(confirmed) 세션이 비정상으로 적으면
        # 롤링 재적재 과도기가 아니라 진짜 수집 결손이므로 STALE 유지.
        ok, warning, detail = summarize_ga4_quality(
            [row("cloop", "confirmed", sessions=40, ratio=0.4), row("sprint", "confirmed")]
        )
        self.assertFalse(ok)
        self.assertFalse(warning)
        self.assertIn("40%", detail)

    def test_missing_status_is_failure(self):
        # native export 자체가 없는(intraday도 daily도 없는) 진짜 결손은 그대로 STALE.
        ok, warning, detail = summarize_ga4_quality(
            [row("cloop", "missing", sessions=0, ratio=None), row("sprint", "confirmed")]
        )
        self.assertFalse(ok)
        self.assertFalse(warning)
        self.assertIn("cloop", detail)

    def test_missing_brand_is_failure(self):
        ok, warning, detail = summarize_ga4_quality([row("cloop", "confirmed")])
        self.assertFalse(ok)
        self.assertFalse(warning)
        self.assertIn("sprint", detail)


if __name__ == "__main__":
    unittest.main()
