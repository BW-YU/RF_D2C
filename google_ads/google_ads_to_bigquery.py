#!/usr/bin/env python3
"""Google Ads 읽기 전용 성과를 BigQuery 영구 원장과 통합 mart에 적재한다.

고장 난 Ads Scripts의 대체 경로다. 공식 Google Ads API에서 캠페인×일 grain을
조회하고, 조회가 성공한 날짜 파티션만 덮어쓴다. 이후 같은 날짜의 Google 행만
``mart.ad_unified_src``에서 ``mart.ad_unified_daily``로 원자적으로 재반영한다.
광고 설정을 생성·수정하는 API는 호출하지 않는다.
"""

from __future__ import annotations

import os
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from google.api_core.exceptions import NotFound
from google.cloud import bigquery


KST = ZoneInfo("Asia/Seoul")
PROJECT = os.environ.get("BQ_PROJECT", "rf-ads-db-500505")
DATASET = os.environ.get("BQ_DATASET", "google_ads_raw")
LOCATION = os.environ.get("BQ_LOCATION", "asia-northeast3")
ACCOUNTS_SPEC = os.environ.get("GOOGLE_ADS_CUSTOMER_IDS", "2580015098:cloop")
LOOKBACK_DAYS = int(os.environ.get("LOOKBACK_DAYS", "3"))


SCHEMA = [
    bigquery.SchemaField("report_date", "DATE"),
    bigquery.SchemaField("customer_id", "INT64"),
    bigquery.SchemaField("mall", "STRING"),
    bigquery.SchemaField("campaign_id", "INT64"),
    bigquery.SchemaField("campaign_name", "STRING"),
    bigquery.SchemaField("impressions", "INT64"),
    bigquery.SchemaField("clicks", "INT64"),
    bigquery.SchemaField("cost", "FLOAT64"),
    bigquery.SchemaField("conversions", "FLOAT64"),
    bigquery.SchemaField("conversion_value", "FLOAT64"),
    bigquery.SchemaField("batch_time", "TIMESTAMP"),
]


def required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"필수 환경변수 누락: {name}")
    return value


def parse_accounts(spec: str) -> list[tuple[str, str]]:
    accounts = []
    for item in spec.split(","):
        customer_id, sep, mall = item.strip().partition(":")
        customer_id = customer_id.replace("-", "")
        if not sep or not customer_id.isdigit() or not mall:
            raise ValueError(f"잘못된 GOOGLE_ADS_CUSTOMER_IDS 항목: {item!r}")
        accounts.append((customer_id, mall))
    if not accounts:
        raise ValueError("Google Ads 계정이 없습니다")
    return accounts


def date_window(today: date | None = None) -> tuple[date, date]:
    end = date.fromisoformat(os.environ["END_DATE"]) if os.environ.get("END_DATE") else (
        today or datetime.now(KST).date()
    )
    if os.environ.get("START_DATE"):
        start = date.fromisoformat(os.environ["START_DATE"])
    else:
        if LOOKBACK_DAYS < 1:
            raise ValueError("LOOKBACK_DAYS는 1 이상이어야 합니다")
        start = end - timedelta(days=LOOKBACK_DAYS - 1)
    if start > end:
        raise ValueError("START_DATE가 END_DATE보다 늦습니다")
    return start, end


def google_client():
    # 라이브러리는 실행 시에만 로딩해 순수 함수 단위테스트가 별도 인증 없이 돌게 한다.
    from google.ads.googleads.client import GoogleAdsClient

    config = {
        "developer_token": required_env("GOOGLE_ADS_DEVELOPER_TOKEN"),
        "client_id": required_env("GOOGLE_ADS_CLIENT_ID"),
        "client_secret": required_env("GOOGLE_ADS_CLIENT_SECRET"),
        "refresh_token": required_env("GOOGLE_ADS_REFRESH_TOKEN"),
        "login_customer_id": required_env("GOOGLE_ADS_LOGIN_CUSTOMER_ID").replace("-", ""),
        "use_proto_plus": True,
    }
    return GoogleAdsClient.load_from_dict(config)


def fetch_rows(client, customer_id: str, mall: str, start: date, end: date) -> list[dict]:
    query = f"""
      SELECT segments.date, customer.id, campaign.id, campaign.name,
             metrics.impressions, metrics.clicks, metrics.cost_micros,
             metrics.conversions, metrics.conversions_value
      FROM campaign
      WHERE segments.date BETWEEN '{start.isoformat()}' AND '{end.isoformat()}'
    """
    service = client.get_service("GoogleAdsService")
    now = datetime.now(timezone.utc).isoformat()
    rows = []
    for batch in service.search_stream(customer_id=customer_id, query=query):
        for row in batch.results:
            rows.append(
                {
                    "report_date": str(row.segments.date),
                    "customer_id": int(row.customer.id),
                    "mall": mall,
                    "campaign_id": int(row.campaign.id),
                    "campaign_name": str(row.campaign.name),
                    "impressions": int(row.metrics.impressions),
                    "clicks": int(row.metrics.clicks),
                    "cost": float(row.metrics.cost_micros) / 1_000_000,
                    "conversions": float(row.metrics.conversions),
                    "conversion_value": float(row.metrics.conversions_value),
                    "batch_time": now,
                }
            )
    return rows


def ensure_table(client: bigquery.Client, mall: str) -> str:
    dataset = bigquery.Dataset(f"{PROJECT}.{DATASET}")
    dataset.location = LOCATION
    client.create_dataset(dataset, exists_ok=True)
    table_id = f"{PROJECT}.{DATASET}.rf_google_campaign_daily_{mall}"
    try:
        client.get_table(table_id)
    except NotFound:
        table = bigquery.Table(table_id, schema=SCHEMA)
        table.time_partitioning = bigquery.TimePartitioning(
            type_=bigquery.TimePartitioningType.DAY, field="report_date"
        )
        table.clustering_fields = ["campaign_id"]
        client.create_table(table)
    return table_id


def load_partitions(
    client: bigquery.Client, table_id: str, rows: list[dict], start: date, end: date
) -> None:
    by_date: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        by_date[row["report_date"]].append(row)

    day = start
    while day <= end:
        day_text = day.isoformat()
        day_rows = by_date.get(day_text, [])
        if day_rows:
            config = bigquery.LoadJobConfig(
                schema=SCHEMA,
                source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
                write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
            )
            client.load_table_from_json(
                day_rows, f"{table_id}${day.strftime('%Y%m%d')}", job_config=config
            ).result()
        else:
            # API 조회 자체가 성공한 뒤 0행인 날짜만 기존 잔존 파티션을 비운다.
            config = bigquery.QueryJobConfig(
                query_parameters=[bigquery.ScalarQueryParameter("day", "DATE", day_text)]
            )
            client.query(
                f"DELETE FROM `{table_id}` WHERE report_date = @day", job_config=config
            ).result()
        day += timedelta(days=1)


def refresh_google_mart(client: bigquery.Client, start: date, end: date) -> None:
    sql = """
    BEGIN TRANSACTION;
      DELETE FROM `rf-ads-db-500505.mart.ad_unified_daily`
       WHERE media = 'google' AND report_date BETWEEN @start AND @end;
      INSERT INTO `rf-ads-db-500505.mart.ad_unified_daily`
      SELECT * FROM `rf-ads-db-500505.mart.ad_unified_src`
       WHERE media = 'google' AND report_date BETWEEN @start AND @end;
    COMMIT TRANSACTION;
    """
    config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("start", "DATE", start.isoformat()),
            bigquery.ScalarQueryParameter("end", "DATE", end.isoformat()),
        ]
    )
    client.query(sql, job_config=config).result()


def main() -> None:
    start, end = date_window()
    ads = google_client()
    bq = bigquery.Client(project=PROJECT, location=LOCATION)
    total_rows = 0
    for customer_id, mall in parse_accounts(ACCOUNTS_SPEC):
        rows = fetch_rows(ads, customer_id, mall, start, end)
        table_id = ensure_table(bq, mall)
        load_partitions(bq, table_id, rows, start, end)
        total_rows += len(rows)
        print(f"Google Ads 적재: {mall} {start}~{end} {len(rows)}행")
    refresh_google_mart(bq, start, end)
    print(f"Google mart 반영 완료: {start}~{end} 총 {total_rows}행")


if __name__ == "__main__":
    main()
