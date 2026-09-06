#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
freshness_check.py
------------------
핵심 적재 테이블이 "제때·완전하게" 갱신되고 있는지 확인하는 모니터.
날짜 신선도와 GA4 D-1의 native export 확정 상태를 함께 판정한다.

- 하나라도 STALE 이면 종료코드 1 → GitHub Actions 실패 → 기본 실패 알림(이메일) 발송.
- SLACK_WEBHOOK_URL 환경변수(선택)가 있으면 요약을 슬랙으로도 전송.
- 읽기 전용(어느 데이터셋도 수정하지 않음).
"""
import os
import sys
import logging

from google.cloud import bigquery

BQ_PROJECT = os.environ.get("BQ_PROJECT", "rf-ads-db-500505")
BQ_LOCATION = os.environ.get("BQ_LOCATION", "asia-northeast3")
SLACK_WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL", "").strip()
SLACK_BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN", "").strip()
SLACK_CHANNEL_ID = os.environ.get("SLACK_CHANNEL_ID", "").strip()
SLACK_MENTION_ID = os.environ.get("SLACK_MENTION_ID", "").strip()

# GA4는 D-1이 하루 중 여러 차례 롤링 재적재된다. "partial"은 native intraday만 있고
# 아직 daily(final) 테이블로 교체되기 전 상태 — 정오~오후 체크 시각에 흔히 걸리는
# 정상 과도기라 STALE(실패)이 아니라 WARN으로만 남긴다. "confirmed"인데도
# session_ratio가 이 임계치 밑이면 그건 과도기가 아니라 진짜 수집 결손이라 STALE로 본다.
# 임계치는 ga4/ga4_to_bigquery.py의 provisional→partial 판정 기준(0.5)과 맞춘다.
GA4_CONFIRMED_RATIO_STALE_THRESHOLD = 0.5

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("freshness_check")

# (표시이름, 테이블, 날짜컬럼, 허용지연일)
#   허용지연일 = 오늘(KST)로부터 최신 날짜가 이 값 이내여야 정상.
CHECKS = [
    ("카페24 매출(시간대)",  "cafe24.rf_cafe24_sales_daily",       "report_date",  2),
    ("카페24 주문",          "cafe24.rf_cafe24_orders",            "report_date",  2),
    ("카페24 유입귀속",      "cafe24.rf_cafe24_order_attribution", "report_date",  2),
    ("메타 광고",            "meta_ads.rf_meta_ads",               "report_date",  2),
    ("네이버 SA",            "naver_ads.rf_naver_sa_ads",          "report_date",  3),
    ("구글 광고(DTS)",       "google_ads_raw.p_ads_CampaignBasicStats_3030273599", "segments_date", 3),
    ("카카오모먼트",         "kakao_moment.rf_kakao_campaign",     "date",         3),
]


def check_one(client, label, table, date_col, max_days):
    q = (f"SELECT MAX(`{date_col}`) AS mx, "
         f"DATE_DIFF(CURRENT_DATE('Asia/Seoul'), MAX(`{date_col}`), DAY) AS lag "
         f"FROM `{BQ_PROJECT}.{table}`")
    try:
        row = list(client.query(q).result())[0]
    except Exception as e:  # noqa: BLE001
        return dict(label=label, table=table, ok=False,
                    detail=f"조회 실패: {str(e)[:120]}")
    if row["mx"] is None:
        return dict(label=label, table=table, ok=False, detail="데이터 없음")
    lag = row["lag"]
    ok = lag is not None and lag <= max_days
    return dict(label=label, table=table, ok=ok,
                detail=f"최신 {row['mx']} (지연 {lag}일 / 허용 {max_days}일)")


def summarize_ga4_quality(rows):
    """D-1 cloop+sprint 품질행을 fail-closed로 요약한다."""
    expected = {"cloop", "sprint"}
    by_brand = {row["brand"]: row for row in rows}
    missing_brands = sorted(expected - set(by_brand))
    if missing_brands:
        return False, False, "품질행 없음: " + ", ".join(missing_brands)

    def fmt(brand):
        row = by_brand[brand]
        ratio = row.get("session_ratio")
        ratio_text = f", 직전중위 대비 {ratio:.0%}" if ratio is not None else ""
        return f"{brand}={row['data_status']}({row.get('api_sessions', 0):,} 세션{ratio_text})"

    # "missing" = native export 자체가 없음(intraday도 daily도 없음) — 실제 결손, STALE.
    # confirmed인데 session_ratio가 임계치 밑이면 final 테이블은 있어도 세션 자체가
    # 비정상으로 적으므로 STALE. 그 외(예: confirmed·ratio 정상)는 STALE 아님.
    missing = [b for b in sorted(expected) if by_brand[b]["data_status"] == "missing"]
    stale_confirmed = [
        b for b in sorted(expected)
        if by_brand[b]["data_status"] == "confirmed"
        and by_brand[b].get("session_ratio") is not None
        and by_brand[b]["session_ratio"] < GA4_CONFIRMED_RATIO_STALE_THRESHOLD
    ]
    bad = missing + stale_confirmed
    if bad:
        return False, False, "; ".join(fmt(b) for b in sorted(bad))

    # "partial" = native intraday만 있고 daily(final)로 아직 안 바뀐 롤링 재적재 과도기.
    # 오탐 방지를 위해 STALE이 아니라 WARN으로만 남긴다(260906).
    pending = [b for b in sorted(expected)
               if by_brand[b]["data_status"] in {"partial", "provisional"}]
    if pending:
        return True, True, "native final 대기: " + ", ".join(pending)
    if all(by_brand[b]["data_status"] == "confirmed" for b in expected):
        return True, False, "cloop+sprint native final 확인"
    unknown = ", ".join(f"{b}={by_brand[b]['data_status']}" for b in sorted(expected))
    return False, False, "알 수 없는 품질 상태: " + unknown


def check_ga4_quality(client):
    table = "rf_ga4.rf_ga4_quality_daily"
    q = f"""
    SELECT brand, data_status, api_sessions, session_ratio, loaded_at
    FROM `{BQ_PROJECT}.{table}`
    WHERE date = DATE_SUB(CURRENT_DATE('Asia/Seoul'), INTERVAL 1 DAY)
    QUALIFY ROW_NUMBER() OVER (PARTITION BY brand ORDER BY loaded_at DESC) = 1
    """
    try:
        rows = [dict(row.items()) for row in client.query(q).result()]
    except Exception as e:  # noqa: BLE001
        return dict(label="GA4 D-1 완전성", table=table, ok=False, warning=False,
                    detail=f"조회 실패: {str(e)[:120]}")
    ok, warning, detail = summarize_ga4_quality(rows)
    return dict(label="GA4 D-1 완전성", table=table, ok=ok,
                warning=warning, detail=detail)


def notify_slack(text):
    mention = f"<@{SLACK_MENTION_ID}> " if SLACK_MENTION_ID else ""
    text = mention + text
    if not SLACK_WEBHOOK_URL and not (SLACK_BOT_TOKEN and SLACK_CHANNEL_ID):
        log.warning("Slack 자격증명 없음: 알림 미전송")
        return
    try:
        import requests
        if SLACK_WEBHOOK_URL:
            response = requests.post(SLACK_WEBHOOK_URL, json={"text": text}, timeout=15)
            response.raise_for_status()
            return
        response = requests.post(
            "https://slack.com/api/chat.postMessage",
            headers={"Authorization": f"Bearer {SLACK_BOT_TOKEN}"},
            json={"channel": SLACK_CHANNEL_ID, "text": text},
            timeout=15,
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("ok") is not True:
            raise RuntimeError(payload.get("error", "unknown Slack API error"))
    except Exception as e:  # noqa: BLE001
        log.warning("슬랙 전송 실패: %s", str(e)[:120])


def main():
    client = bigquery.Client(project=BQ_PROJECT, location=BQ_LOCATION)
    results = [check_one(client, *c) for c in CHECKS]
    results.append(check_ga4_quality(client))
    stale = [r for r in results if not r["ok"]]
    warnings = [r for r in results if r.get("warning")]

    for r in results:
        mark = "STALE" if not r["ok"] else ("WARN" if r.get("warning") else "OK ")
        log.info("[%s] %s — %s (%s)", mark, r["label"], r["detail"], r["table"])

    if stale:
        lines = [f"🔴 데이터 신선도 경고 ({len(stale)}건):"]
        lines += [f"• {r['label']}: {r['detail']}" for r in stale]
        msg = "\n".join(lines)
        log.error(msg)
        notify_slack(msg)
        sys.exit(1)

    if warnings:
        lines = [f"🟡 데이터 신선도 잠정 ({len(warnings)}건):"]
        lines += [f"• {r['label']}: {r['detail']}" for r in warnings]
        notify_slack("\n".join(lines))
        log.warning("품질 잠정 상태: %s", ", ".join(r["label"] for r in warnings))
        return

    notify_slack("✅ 데이터 신선도 정상 (" + ", ".join(r["label"] for r in results) + ")")
    log.info("전체 신선도 정상")


if __name__ == "__main__":
    main()
