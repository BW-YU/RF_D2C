#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
수신거부(BLOCKLIST) 시트에 카페24 `member_id` 되쓰기.

시트의 전화번호(B열)를 주문 연락처와 대조해 회원 아이디를 C열에 채웁니다.
다른 CRM 솔루션이 전화번호 대신 회원 아이디를 요구할 때 그대로 넘길 수 있게 하는 용도.

조회 원천: `cafe24_pii.rf_cafe24_customer_contact`(주문 buyer/receiver 연락처, PII 정책태그)
- 전화번호는 숫자만 남겨 대조. 숫자 셀로 저장돼 앞자리 0이 빠진 10자리는 0을 붙여 보정.
- 한 번호에 아이디가 여러 개면 `member_id_1`·`member_id_2` 두 열에 나눠 기입(몰→아이디 순).
- **이미 값이 있는 칸은 덮어쓰지 않는다.** 슬랙에서 CX가 확인해 적어준 아이디는 BQ(주문 이력)로
  못 잡는 비회원 건이라, 덮어쓰면 지워진다.
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
H_PHONE, H_ID1, H_ID2 = "phone", "member_id_1", "member_id_2"
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
SELECT ph, STRING_AGG(mid, '|' ORDER BY mall, mid) AS ids
FROM (SELECT DISTINCT ph, mall, mid FROM c WHERE ph IN UNNEST(@phones))
GROUP BY ph
"""


def normalize(v):
    """숫자만 남기고, 앞자리 0이 빠진 10자리는 0을 붙인다."""
    x = re.sub(r"\D", "", str(v or ""))
    if len(x) == 10 and x.startswith("1"):
        x = "0" + x
    return x


def a1(col_idx):
    """1-based 열 번호 → A1 표기"""
    s, n = "", col_idx
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def main():
    creds = service_account.Credentials.from_service_account_file(
        KEY, scopes=["https://www.googleapis.com/auth/spreadsheets"])
    svc = build("sheets", "v4", credentials=creds, cache_discovery=False)

    rows = svc.spreadsheets().values().get(
        spreadsheetId=SHEET_ID, range=f"{TAB}!A:Z",
        valueRenderOption="FORMATTED_VALUE").execute().get("values", [])
    if len(rows) < 2:
        print("시트에 데이터 행이 없음 — 건너뜀")
        return

    head = rows[0]
    # 열은 헤더 이름으로 찾는다(순서가 바뀌어도 안 깨진다)
    def find(name, fallback):
        return head.index(name) + 1 if name in head else fallback
    c_phone, c_id1 = find(H_PHONE, 2), find(H_ID1, 3)
    c_id2 = find(H_ID2, c_id1 + 1)

    # 체크박스 열이 빈 행까지 깔려 있어 A열(reg) 기준으로 마지막 행을 잡는다
    last = max((i for i, r in enumerate(rows, start=1) if r and str(r[0]).strip()), default=1)
    data_rows = rows[1:last]

    def cell(r, idx):
        return str(r[idx - 1]).strip() if len(r) >= idx else ""

    phones = [normalize(cell(r, c_phone)) for r in data_rows]
    # 이미 채워진 행은 조회도 기입도 하지 않는다
    todo = {p for p, r in zip(phones, data_rows)
            if len(p) == 11 and not cell(r, c_id1) and not cell(r, c_id2)}
    if not todo:
        print("빈 칸 없음 — 건너뜀")
        return

    bq = bigquery.Client(project=PROJECT, location=LOCATION)
    job = bq.query(SQL.format(p=PROJECT), job_config=bigquery.QueryJobConfig(
        query_parameters=[bigquery.ArrayQueryParameter("phones", "STRING", sorted(todo))]))
    mapping = {row["ph"]: row["ids"].split("|") for row in job.result()}

    data, filled = [], 0
    for i, (p, r) in enumerate(zip(phones, data_rows), start=2):
        if p not in todo or p not in mapping:
            continue
        ids = mapping[p]
        data.append({"range": f"{TAB}!{a1(c_id1)}{i}", "values": [[ids[0]]]})
        if len(ids) > 1:
            data.append({"range": f"{TAB}!{a1(c_id2)}{i}", "values": [[ids[1]]]})
        if len(ids) > 2:
            print(f"  ⚠ {p}: 아이디 {len(ids)}개 중 2개만 기입 ({', '.join(ids[2:])} 누락)")
        filled += 1

    if data:
        svc.spreadsheets().values().batchUpdate(spreadsheetId=SHEET_ID, body={
            "valueInputOption": "RAW", "data": data}).execute()

    print(f"member_id 되쓰기 완료: 빈 칸 {len(todo)}건 중 {filled}행 기입 "
          f"(전체 {len(data_rows)}행, 이미 채워진 행은 건드리지 않음)")


if __name__ == "__main__":
    main()
