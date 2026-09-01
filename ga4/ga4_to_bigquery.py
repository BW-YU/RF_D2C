#!/usr/bin/env python3
"""
GA4 -> BigQuery 적재 (cloop-collab/RF_D2C · ga4 폴더)
네이버(naver/naver_to_bigquery.py)와 동일한 방식: 환경변수 기반 + daily에 백필 통합.

프로젝트 rf-ads-db-500505 · 데이터셋 rf_ga4 (asia-northeast3)
  - rf_ga4      : 일별 API 스냅샷 (cloop + sprint 합침)
  - rf_ga4_d0   : 당일 (cloop + sprint 합침)
  - rf_ga4_quality_daily : 속성×일자별 provisional/partial/confirmed 상태
  - rf_ga4_load_audit    : 매 실행 품질 상태 이력

모드:
  python ga4/ga4_to_bigquery.py --mode daily
     · 평소: 최근 LOOKBACK_DAYS(기본 8)일 재적재 (지연 반영분 보정) + KEEP_DAYS 초과분 정리
     · BACKFILL_DAYS>0 로 실행 시: 과거 N일 1회 백필 (rf_ga4 전체 교체)
  python ga4/ga4_to_bigquery.py --mode d0
     · 당일 데이터로 rf_ga4_d0 교체 (시간당)

UTM 매핑:
  utm_source=session_source / utm_medium=session_medium / utm_campaign=session_campaign_name
  utm_term=session_manual_term / utm_content=session_manual_ad_content
  + session_manual_campaign_name(수동 utm_campaign·캠페인 매출 파리티) / first_user_source_medium(첫 접점 first-touch)
  (2026-07: utm_id=session_campaign_id 드롭 → API 차원 9개 한도 내에서 위 2종 확보)
"""

import argparse
import collections
import os
import datetime as dt
import statistics
import uuid
from zoneinfo import ZoneInfo

from google.analytics.data_v1beta import BetaAnalyticsDataClient
from google.analytics.data_v1beta.types import (
    DateRange,
    Dimension,
    Metric,
    RunReportRequest,
)
from google.api_core.exceptions import NotFound
from google.cloud import bigquery

# ========== 설정 (환경변수로 덮어쓰기 가능) ==========
GCP_PROJECT = os.environ.get("BQ_PROJECT", "rf-ads-db-500505")
BQ_DATASET = os.environ.get("BQ_DATASET", "rf_ga4")
BQ_LOCATION = os.environ.get("BQ_LOCATION", "asia-northeast3")
TABLE_DAILY = os.environ.get("BQ_TABLE", "rf_ga4")        # 일별 API 스냅샷
TABLE_D0 = os.environ.get("BQ_TABLE_D0", "rf_ga4_d0")     # 시간당·당일
TABLE_QUALITY = os.environ.get("BQ_QUALITY_TABLE", "rf_ga4_quality_daily")
TABLE_AUDIT = os.environ.get("BQ_AUDIT_TABLE", "rf_ga4_load_audit")
LOOKBACK_DAYS = int(os.environ.get("LOOKBACK_DAYS") or "8")
BACKFILL_DAYS = int(os.environ.get("BACKFILL_DAYS") or "0")
KEEP_DAYS = int(os.environ.get("KEEP_DAYS") or "365")
KST = ZoneInfo("Asia/Seoul")

# GA4 속성ID -> brand(몰) 이름
PROPERTIES = {
    "316130085": "cloop",    # 클룹몰
    "499489594": "sprint",   # 스프린트몰
}

# GA4 차원. date 제외 8개 + date = 9개 (API 최대 9개). utm_id(sessionCampaignId) 드롭 → manual campaign·first-touch 확보.
DIMENSIONS = [
    "date",
    "sessionSource",              # utm_source
    "sessionMedium",              # utm_medium
    "sessionCampaignName",        # utm_campaign (자동 해석 포함)
    "sessionManualCampaignName",  # 수동 utm_campaign (캠페인 매출 파리티)
    "sessionManualTerm",          # utm_term
    "sessionManualAdContent",     # utm_content
    "sessionDefaultChannelGroup",
    "firstUserSourceMedium",      # 첫 접점(first-touch) source / medium
]
# GA4 지표. 8개 + activeUsers·userEngagementDuration = 10개 (API 최대 10개).
METRICS = [
    "sessions",
    "totalUsers",
    "newUsers",
    "addToCarts",
    "checkouts",
    "ecommercePurchases",
    "purchaseRevenue",
    "firstTimePurchasers",
    "activeUsers",
    "userEngagementDuration",
]

DIM_COLS = {
    "date": "date",
    "sessionSource": "session_source",
    "sessionMedium": "session_medium",
    "sessionCampaignName": "session_campaign_name",
    "sessionManualCampaignName": "session_manual_campaign_name",
    "sessionManualTerm": "session_manual_term",
    "sessionManualAdContent": "session_manual_ad_content",
    "sessionDefaultChannelGroup": "session_default_channel_group",
    "firstUserSourceMedium": "first_user_source_medium",
}
METRIC_COLS = {
    "sessions": "sessions",
    "totalUsers": "total_users",
    "newUsers": "new_users",
    "addToCarts": "add_to_carts",
    "checkouts": "checkouts",
    "ecommercePurchases": "ecommerce_purchases",
    "purchaseRevenue": "purchase_revenue",
    "firstTimePurchasers": "first_time_purchasers",
    "activeUsers": "active_users",
    "userEngagementDuration": "user_engagement_duration",
}
FLOAT_METRICS = {"purchase_revenue", "user_engagement_duration"}
# =====================================================


def bq_schema():
    fields = [
        bigquery.SchemaField("brand", "STRING"),
        bigquery.SchemaField("property_id", "STRING"),
        bigquery.SchemaField("date", "DATE"),
        bigquery.SchemaField("session_source", "STRING"),              # utm_source
        bigquery.SchemaField("session_medium", "STRING"),              # utm_medium
        bigquery.SchemaField("session_campaign_name", "STRING"),         # utm_campaign (자동 해석)
        bigquery.SchemaField("session_manual_campaign_name", "STRING"),  # 수동 utm_campaign
        bigquery.SchemaField("session_manual_term", "STRING"),           # utm_term
        bigquery.SchemaField("session_manual_ad_content", "STRING"),     # utm_content
        bigquery.SchemaField("session_default_channel_group", "STRING"),
        bigquery.SchemaField("first_user_source_medium", "STRING"),      # 첫 접점(first-touch)
    ]
    for _, col in METRIC_COLS.items():
        bq_type = "FLOAT" if col in FLOAT_METRICS else "INTEGER"
        fields.append(bigquery.SchemaField(col, bq_type))
    fields.append(bigquery.SchemaField("loaded_at", "TIMESTAMP"))
    return fields


def quality_schema():
    return [
        bigquery.SchemaField("brand", "STRING"),
        bigquery.SchemaField("property_id", "STRING"),
        bigquery.SchemaField("date", "DATE"),
        bigquery.SchemaField("data_status", "STRING"),
        bigquery.SchemaField("native_table_type", "STRING"),
        bigquery.SchemaField("api_sessions", "INTEGER"),
        bigquery.SchemaField("api_purchases", "INTEGER"),
        bigquery.SchemaField("api_add_to_carts", "INTEGER"),
        bigquery.SchemaField("api_checkouts", "INTEGER"),
        bigquery.SchemaField("prior_7d_median_sessions", "FLOAT"),
        bigquery.SchemaField("session_ratio", "FLOAT"),
        bigquery.SchemaField("loaded_at", "TIMESTAMP"),
        bigquery.SchemaField("run_id", "STRING"),
    ]


def native_export_state(bq, property_id, date_value):
    """GA4 native export에서 해당 일자의 final/intraday 존재 상태를 판정한다."""
    suffix = str(date_value).replace("-", "")
    dataset = f"analytics_{property_id}"
    daily = f"{GCP_PROJECT}.{dataset}.events_{suffix}"
    intraday = f"{GCP_PROJECT}.{dataset}.events_intraday_{suffix}"
    try:
        bq.get_table(daily)
        return "confirmed", "daily"
    except NotFound:
        pass
    try:
        bq.get_table(intraday)
        return "provisional", "intraday"
    except NotFound:
        pass
    return "missing", "missing"


def build_quality_rows(bq, api_rows, start_date, end_date, run_id, loaded_at=None):
    """API 집계와 native export final 존재 여부를 결합해 일×속성 품질 상태를 만든다."""
    loaded_at = loaded_at or dt.datetime.now(dt.timezone.utc).isoformat()
    agg = collections.defaultdict(lambda: collections.Counter())
    for row in api_rows:
        key = (row["brand"], row["property_id"], row["date"])
        for field in ("sessions", "ecommerce_purchases", "add_to_carts", "checkouts"):
            agg[key][field] += row.get(field, 0) or 0

    start = dt.date.fromisoformat(start_date)
    end = dt.date.fromisoformat(end_date)
    result = []
    for property_id, brand in PROPERTIES.items():
        history = []
        day = start
        while day <= end:
            date_value = day.isoformat()
            values = agg.get((brand, property_id, date_value), collections.Counter())
            sessions = int(values.get("sessions", 0))
            median = float(statistics.median(history[-7:])) if history else 0.0
            ratio = sessions / median if median else None
            export_status, native_type = native_export_state(bq, property_id, date_value)
            status = export_status
            if export_status == "provisional" and ratio is not None and ratio < 0.5:
                status = "partial"
            result.append({
                "brand": brand,
                "property_id": property_id,
                "date": date_value,
                "data_status": status,
                "native_table_type": native_type,
                "api_sessions": sessions,
                "api_purchases": int(values.get("ecommerce_purchases", 0)),
                "api_add_to_carts": int(values.get("add_to_carts", 0)),
                "api_checkouts": int(values.get("checkouts", 0)),
                "prior_7d_median_sessions": median or None,
                "session_ratio": ratio,
                "loaded_at": loaded_at,
                "run_id": run_id,
            })
            if sessions:
                history.append(sessions)
            day += dt.timedelta(days=1)
    return result


def fetch_property(client, property_id, brand, start_date, end_date):
    rows = []
    offset = 0
    page = 100000
    loaded_at = dt.datetime.now(dt.timezone.utc).isoformat()
    while True:
        req = RunReportRequest(
            property=f"properties/{property_id}",
            dimensions=[Dimension(name=d) for d in DIMENSIONS],
            metrics=[Metric(name=m) for m in METRICS],
            date_ranges=[DateRange(start_date=start_date, end_date=end_date)],
            limit=page,
            offset=offset,
        )
        resp = client.run_report(request=req)
        for r in resp.rows:
            rec = {"brand": brand, "property_id": property_id}
            for i, d in enumerate(DIMENSIONS):
                val = r.dimension_values[i].value
                col = DIM_COLS[d]
                if col == "date":
                    rec[col] = dt.datetime.strptime(val, "%Y%m%d").date().isoformat()
                else:
                    rec[col] = val
            for i, m in enumerate(METRICS):
                col = METRIC_COLS[m]
                raw = r.metric_values[i].value or "0"
                rec[col] = float(raw) if col in FLOAT_METRICS else int(float(raw))
            rec["loaded_at"] = loaded_at
            rows.append(rec)
        total = resp.row_count or 0
        offset += page
        if offset >= total:
            break
    print(f"  - {brand}({property_id}) {start_date}~{end_date}: {len(rows)} rows")
    return rows


def fetch_all(start_date, end_date):
    client = BetaAnalyticsDataClient()
    all_rows = []
    for pid, brand in PROPERTIES.items():
        all_rows.extend(fetch_property(client, pid, brand, start_date, end_date))
    return all_rows


def ensure_dataset(bq):
    ds = bigquery.Dataset(f"{GCP_PROJECT}.{BQ_DATASET}")
    ds.location = BQ_LOCATION
    bq.create_dataset(ds, exists_ok=True)


def ensure_table(bq, table_id, schema=None):
    full = f"{GCP_PROJECT}.{BQ_DATASET}.{table_id}"
    try:
        bq.get_table(full)
    except NotFound:
        table = bigquery.Table(full, schema=schema or bq_schema())
        table.time_partitioning = bigquery.TimePartitioning(field="date")
        bq.create_table(table)
        print(f"  * 테이블 생성: {full}")
    return full


def load_replace(bq, table_id, rows):
    """테이블 전체 교체 (WRITE_TRUNCATE). 백필/당일 갱신용. 스키마도 최신으로 반영."""
    full = ensure_table(bq, table_id)
    job_config = bigquery.LoadJobConfig(
        schema=bq_schema(),
        write_disposition="WRITE_TRUNCATE",
    )
    bq.load_table_from_json(rows, full, job_config=job_config).result()
    print(f"[{table_id}] 전체 교체: {len(rows)} rows")


def load_merge_range(bq, table_id, rows, start, end):
    """스테이징 적재 후 트랜잭션으로 [start, end]를 원자 교체한다."""
    full = ensure_table(bq, table_id)
    staging = f"{full}__staging_{uuid.uuid4().hex[:10]}"
    job_config = bigquery.LoadJobConfig(schema=bq_schema(), write_disposition="WRITE_TRUNCATE")
    bq.load_table_from_json(rows, staging, job_config=job_config).result()
    cutoff = (dt.datetime.now(KST).date() - dt.timedelta(days=KEEP_DAYS)).isoformat()
    try:
        bq.query(f"""
        BEGIN TRANSACTION;
        DELETE FROM `{full}` WHERE date BETWEEN '{start}' AND '{end}';
        INSERT INTO `{full}` SELECT * FROM `{staging}`;
        DELETE FROM `{full}` WHERE date < '{cutoff}';
        COMMIT TRANSACTION;
        """).result()
    finally:
        bq.delete_table(staging, not_found_ok=True)
    print(f"[{table_id}] {start}~{end} 갱신 ({len(rows)} rows), {cutoff} 이전 정리")


def load_quality(bq, rows, start, end):
    current = ensure_table(bq, TABLE_QUALITY, quality_schema())
    audit = ensure_table(bq, TABLE_AUDIT, quality_schema())
    stage = f"{current}__staging_{uuid.uuid4().hex[:10]}"
    config = bigquery.LoadJobConfig(schema=quality_schema(), write_disposition="WRITE_TRUNCATE")
    bq.load_table_from_json(rows, stage, job_config=config).result()
    try:
        bq.query(f"""
        BEGIN TRANSACTION;
        DELETE FROM `{current}` WHERE date BETWEEN '{start}' AND '{end}';
        INSERT INTO `{current}` SELECT * FROM `{stage}`;
        COMMIT TRANSACTION;
        """).result()
        append = bigquery.LoadJobConfig(schema=quality_schema(), write_disposition="WRITE_APPEND")
        bq.load_table_from_json(rows, audit, job_config=append).result()
    finally:
        bq.delete_table(stage, not_found_ok=True)
    counts = collections.Counter(row["data_status"] for row in rows)
    print(f"[{TABLE_QUALITY}] 품질 상태: {dict(sorted(counts.items()))}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", required=True, choices=["daily", "d0"])
    args = ap.parse_args()

    bq = bigquery.Client(project=GCP_PROJECT)
    ensure_dataset(bq)
    today = dt.datetime.now(KST).date()
    yesterday = today - dt.timedelta(days=1)

    if args.mode == "daily":
        if BACKFILL_DAYS > 0:
            start = (today - dt.timedelta(days=BACKFILL_DAYS)).isoformat()
            end = yesterday.isoformat()
            print(f"[daily/backfill {BACKFILL_DAYS}d] {start} ~ {end}")
            load_replace(bq, TABLE_DAILY, fetch_all(start, end))
        else:
            start = (today - dt.timedelta(days=LOOKBACK_DAYS)).isoformat()
            end = yesterday.isoformat()
            print(f"[daily/lookback {LOOKBACK_DAYS}d] {start} ~ {end}")
            run_id = uuid.uuid4().hex
            rows = fetch_all(start, end)
            quality = build_quality_rows(bq, rows, start, end, run_id)
            load_merge_range(bq, TABLE_DAILY, rows, start, end)
            load_quality(bq, quality, start, end)
    elif args.mode == "d0":
        d = today.isoformat()
        print(f"[d0] {d}")
        load_replace(bq, TABLE_D0, fetch_all(d, d))


if __name__ == "__main__":
    main()
