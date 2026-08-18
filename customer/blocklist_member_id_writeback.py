#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
수신거부(BLOCKLIST) 시트에 카페24 `member_id` 되쓰기.

시트의 전화번호(B열)를 주문 연락처와 대조해 회원 아이디를 C열에 채웁니다.
다른 CRM 솔루션이 전화번호 대신 회원 아이디를 요구할 때 그대로 넘길 수 있게 하는 용도.

조회 원천: `cafe24_pii.rf_cafe24_customer_contact`(주문 buyer/receiver 연락처, PII 정책태그)
- 전화번호는 숫자만 남겨 대조. 숫자 셀로 저장돼 앞자리 0이 빠진 10자리는 0을 붙여 보정.
- 한 번호에 아이디가 여러 개면 `id1; id2`(몰→아이디 순)로 한 셀에 기입.
- 매칭 실패는 빈칸. 비회원 주문이거나 주문 이력이 없는 번호.
  ⚠ 회원 마스터가 없어 '가입만 하고 주문 없는 회원'은 원리상 매칭 불가.

전제: 시트를 서비스계정과 '편집자' 공유해야 함(적재는 뷰어로 충분하지만 되쓰기는 편집 권한 필요)
      rf-mkt@rf-ads-db-500505.iam.gserviceaccount.com
필수 환경변수: GOOGLE_APPLICATION_CREDENTIALS(SA키), (선택) BLOCKLIST_SHEET_ID/TAB/ID_COL
"""
import os
import re

from google.oauth2 import service_account
from googleapiclient.discovery import build
from google.cloud import bigquery

PROJECT = os.environ.get("BQ_PROJECT", "rf-ads-db-500505")
LOCATION = os.environ.get("BQ_LOCATION", "asia-northeast3")
SHEET_ID = os.environ.get("BLOCKLIST_SHEET_ID", "1_a4UjVqRek0A615qUPlJDxlCpQJ8Awzl6ImGz0B15ms")
TAB = os.environ.get("BLOCKLIST_TAB", "BLOCKLIST")
ID_COL = os.environ.get("BLOCKLIST_ID_COL", "C")   # 기입할 열
ID_HEADER = "member_id"
KEY = os.environ["GOOGLE_APPLICATION_CREDENTIALS"]

SQL = """
WITH c AS (
  SELECT REGEXP_REPLACE(buyer_cellphone, r'[^0-9]', '') AS ph, mall, member_id AS mid
  FROM `{p}.cafe24_pii.rf_cafe24_customer_contact`
  WHERE buyer_cellphone IS NOT NULL AND member_id IS NOT NULL AND member_id <> ''
  UNION ALL
  SELECT REGEXP_REPLACE(receiver_cellphone, r'[^0-9]', '') AS ph, mall, member_id AS mid
  FROM `{p}.cafe24_pii.rf_cafe24_customer_contact`
  WHERE receiver_cellphone IS NOT NULL AND member_id IS NOT NULL AND member_id <> ''
)
SELECT ph, STRING_AGG(mid, '; ' ORDER BY mall, mid) AS ids
FROM (SELECT DISTINCT ph, mall, mid FROM c WHERE ph IN UNNEST(@phones))
GROUP BY ph
"""


def normalize(v):
    """숫자만 남기고, 앞자리 0이 빠진 10자리는 0을 붙인다."""
    x = re.sub(r"\D", "", str(v or ""))
    if len(x) == 10 and x.startswith("1"):
        x = "0" + x
    return x


def main():
    creds = service_account.Credentials.from_service_account_file(
        KEY, scopes=["https://www.googleapis.com/auth/spreadsheets"])
    svc = build("sheets", "v4", credentials=creds, cache_discovery=False)

    vals = svc.spreadsheets().values().get(
        spreadsheetId=SHEET_ID, range=f"{TAB}!A:B",
        valueRenderOption="FORMATTED_VALUE").execute().get("values", [])
    if len(vals) < 2:
        print("시트에 데이터 행이 없음 — 건너뜀")
        return

    phones = [normalize(r[1] if len(r) > 1 else "") for r in vals[1:]]
    uniq = sorted({p for p in phones if len(p) == 11})
    if not uniq:
        print("유효한 전화번호 없음 — 건너뜀")
        return

    bq = bigquery.Client(project=PROJECT, location=LOCATION)
    job = bq.query(SQL.format(p=PROJECT), job_config=bigquery.QueryJobConfig(
        query_parameters=[bigquery.ArrayQueryParameter("phones", "STRING", uniq)]))
    mapping = {row["ph"]: row["ids"] for row in job.result()}

    column = [[ID_HEADER]] + [[mapping.get(p, "")] for p in phones]
    rng = f"{TAB}!{ID_COL}1:{ID_COL}{len(column)}"
    svc.spreadsheets().values().update(
        spreadsheetId=SHEET_ID, range=rng, valueInputOption="RAW",
        body={"range": rng, "majorDimension": "ROWS", "values": column}).execute()

    filled = sum(1 for c in column[1:] if c[0])
    print(f"member_id 되쓰기 완료: {len(phones)}행 중 {filled}행 기입 "
          f"(고유번호 {len(uniq)}건 중 {len(mapping)}건 매칭) → {rng}")


if __name__ == "__main__":
    main()
