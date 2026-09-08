import unittest

import datetime

from freshness_check import summarize_ga4_quality, apply_ack, load_acks


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



class AckTests(unittest.TestCase):
    def _stale(self):
        return dict(label="카카오모먼트", table="kakao_moment.rf_kakao_campaign", ok=False,
                    detail="최신 2026-08-31 (지연 8일 / 허용 3일)")

    def test_ack_within_deadline_becomes_warning(self):
        acks = {"카카오모먼트": ("2026-09-21", "Kakao API 403")}
        r = apply_ack(self._stale(), acks, datetime.date(2026, 9, 8))
        self.assertTrue(r["ok"]); self.assertTrue(r["warning"])
        self.assertIn("인지된 장애", r["detail"])

    def test_ack_after_deadline_stays_stale(self):
        acks = {"카카오모먼트": ("2026-09-21", "Kakao API 403")}
        r = apply_ack(self._stale(), acks, datetime.date(2026, 9, 22))
        self.assertFalse(r["ok"]); self.assertNotIn("warning", r)

    def test_unacked_label_untouched(self):
        r = apply_ack(dict(self._stale(), label="메타 광고"), {"카카오모먼트": ("2026-09-21", "x")}, datetime.date(2026, 9, 8))
        self.assertFalse(r["ok"])

    def test_ok_result_untouched(self):
        r = apply_ack(dict(self._stale(), ok=True), {"카카오모먼트": ("2026-09-21", "x")}, datetime.date(2026, 9, 8))
        self.assertTrue(r["ok"]); self.assertNotIn("warning", r)

    def test_env_override_parses_and_ignores_bad(self):
        acks = load_acks("네이버 SA=2026-10-01:점검;깨진항목;메타 광고=notadate")
        self.assertEqual(acks["네이버 SA"], ("2026-10-01", "점검"))
        self.assertNotIn("메타 광고", acks)
        self.assertIn("카카오모먼트", acks)


if __name__ == "__main__":
    unittest.main()
