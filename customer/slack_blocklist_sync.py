#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
슬랙 #1_crm_cx 의 CRM 차단 요청 → 수신거부(BLOCKLIST) 시트 적재.

요청 메시지에 연락처·고객명·회원 ID가 이미 들어 있어 그대로 시트에 넣는다.
(BQ 매칭은 주문 이력이 있는 회원만 잡히지만, CX가 어드민에서 확인해 적어주므로
 비회원 주문·탈퇴 직전 회원까지 커버된다.)

적재 범위: **클룹·스프린트(자사몰)만**. 한끼통살·랩노쉬는 다른 몰이라 제외하고 로그만 남긴다.
시트 열: A=reg(요청시각) B=phone C=member_id_1 D=member_id_2 F=고객명 G=출처(슬랙 링크)
        E=데이터라이즈 등록(체크박스)은 건드리지 않는다.

이미 있는 번호는 행을 추가하지 않고 빈 칸만 채운다(멱등).

적재한 요청에는 ✅ 리액션과 스레드 답글을 남긴다(SLACK_ACK=0 으로 끌 수 있음).
⚠ '차단 완료' 버튼은 누를 수 없다 — Slack 블록 버튼은 그 버튼을 소유한 앱에만 이벤트가 간다.

필수 환경변수: SLACK_BOT_TOKEN(channels:history + chat:write + reactions:write),
              GOOGLE_APPLICATION_CREDENTIALS(SA키)
선택: BLOCKLIST_SHEET_ID, SLACK_CHANNEL_ID, SLACK_LOOKBACK_DAYS, SLACK_ACK
전제: 시트를 rf-mkt@rf-ads-db-500505.iam.gserviceaccount.com 에 '편집자' 공유
"""
import os
import re
import json
import time
import datetime
import urllib.parse
import urllib.request

from google.oauth2 import service_account
from googleapiclient.discovery import build

SHEET_ID = os.environ.get("BLOCKLIST_SHEET_ID", "1_a4UjVqRek0A615qUPlJDxlCpQJ8Awzl6ImGz0B15ms")
TAB = os.environ.get("BLOCKLIST_TAB", "BLOCKLIST")
CHANNEL = os.environ.get("SLACK_CHANNEL_ID", "C04FPV7D8BZ")
WORKSPACE = os.environ.get("SLACK_WORKSPACE", "egnisworkspace")
LOOKBACK_DAYS = int(os.environ.get("SLACK_LOOKBACK_DAYS", "14"))
ACK = os.environ.get("SLACK_ACK", "1") == "1"      # 적재한 요청에 ✅ + 스레드 답글
BRANDS = ("클룹", "스프린트")          # 자사몰만
H_PHONE, H_ID1, H_ID2 = "phone", "member_id_1", "member_id_2"
H_NAME, H_SRC = "고객명", "출처"
KEY = os.environ["GOOGLE_APPLICATION_CREDENTIALS"]
SLACK_TOKEN = os.environ["SLACK_BOT_TOKEN"]

RE_NAME = re.compile(r"(?:고객명|성함)\s*[:：]\s*([^\n`*]+)")
RE_PHONE = re.compile(r"연락처\s*[:：]\s*([0-9\-\s]+)")
RE_IDS = re.compile(r"회원\s*ID\s*[:：]\s*([^\n`*]+)")


def digits(v):
    """숫자만 남기고, 앞자리 0이 빠진 10자리는 0을 붙인다."""
    x = re.sub(r"\D", "", str(v or ""))
    if len(x) == 10 and x.startswith("1"):
        x = "0" + x
    return x


def hyphenate(d):
    return f"{d[:3]}-{d[3:7]}-{d[7:]}" if len(d) == 11 else d


def slack_history():
    oldest = time.time() - LOOKBACK_DAYS * 86400
    cursor, out = "", []
    while True:
        q = {"channel": CHANNEL, "limit": "200", "oldest": f"{oldest:.6f}"}
        if cursor:
            q["cursor"] = cursor
        req = urllib.request.Request(
            "https://slack.com/api/conversations.history?" + urllib.parse.urlencode(q),
            headers={"Authorization": "Bearer " + SLACK_TOKEN})
        res = json.load(urllib.request.urlopen(req))
        if not res.get("ok"):
            raise RuntimeError("Slack API 오류: " + str(res.get("error")))
        out += res.get("messages", [])
        cursor = (res.get("response_metadata") or {}).get("next_cursor", "")
        if not cursor:
            return out


def brand_of(msg):
    src = " ".join(filter(None, [
        msg.get("username", ""),
        (msg.get("bot_profile") or {}).get("name", ""),
        msg.get("text", "")[:60],
    ]))
    for b in ("클룹", "스프린트", "한끼통살", "랩노쉬"):
        if b in src:
            return b
    return ""


def parse(msg):
    """차단 요청 메시지 → dict 또는 None"""
    text = msg.get("text") or ""
    if "차단" not in text and "차단" not in msg.get("username", ""):
        # 요청 본문은 attachments 안에 있을 수도 있다
        pass
    for att in msg.get("attachments") or []:
        text += "\n" + (att.get("text") or "") + "\n" + (att.get("fallback") or "")
    ph = RE_PHONE.search(text)
    if not ph:
        return None
    d = digits(ph.group(1))
    if len(d) != 11:
        return None

    ids_raw = RE_IDS.search(text)
    ids = []
    if ids_raw:
        for tok in re.split(r"[,/]", ids_raw.group(1)):
            tok = tok.strip().strip("`*")
            # '탈퇴하여 확인불가' 처럼 한글이 섞인 값은 아이디가 아니다
            if tok and not re.search(r"[가-힣]", tok):
                ids.append(tok)
    nm = RE_NAME.search(text)
    ts = msg.get("ts", "")
    when = datetime.datetime.fromtimestamp(
        float(ts), datetime.timezone(datetime.timedelta(hours=9))
    ).strftime("%Y-%m-%d %H:%M:%S") if ts else ""
    return {
        "brand": brand_of(msg),
        "phone": d,
        "ids": ids[:2],
        "ids_dropped": ids[2:],          # 시트 열이 2개라 3번째부터는 못 넣는다
        "name": (nm.group(1).strip().strip("`*") if nm else ""),
        "reg": when,
        "ts": ts,
        "url": f"https://{WORKSPACE}.slack.com/archives/{CHANNEL}/p{ts.replace('.', '')}" if ts else "",
    }


def slack_post(method, payload):
    """Slack Web API POST. 실패해도 적재 결과를 깨지 않도록 오류만 반환한다."""
    req = urllib.request.Request(
        "https://slack.com/api/" + method,
        data=json.dumps(payload).encode(),
        headers={"Authorization": "Bearer " + SLACK_TOKEN,
                 "Content-Type": "application/json; charset=utf-8"})
    try:
        res = json.load(urllib.request.urlopen(req))
    except Exception as e:                      # noqa: BLE001
        return str(e)
    return None if res.get("ok") else res.get("error")


def ack(p, note):
    """적재 완료 표시: ✅ 리액션 + 스레드 답글.
    ⚠ '차단 완료' 버튼 자체는 누를 수 없다(그 버튼을 소유한 앱만 처리 가능).
       버튼 클릭이 프로세스상 필요하면 사람이 누르거나, 워크플로 소유자가 스텝을 손봐야 한다."""
    if not p["ts"]:
        return
    err = slack_post("reactions.add", {"channel": CHANNEL, "timestamp": p["ts"],
                                       "name": "white_check_mark"})
    if err and err != "already_reacted":
        print(f"  리액션 실패({p['phone']}): {err}")
    err = slack_post("chat.postMessage", {"channel": CHANNEL, "thread_ts": p["ts"],
                                          "text": f"수신거부 시트 {note} :white_check_mark:"})
    if err:
        print(f"  스레드 답글 실패({p['phone']}): {err}")


def col(values, idx):
    """1-based 열 번호로 값 읽기"""
    return str(values[idx - 1]).strip() if len(values) >= idx else ""


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
    sheets = svc.spreadsheets()

    rows = sheets.values().get(spreadsheetId=SHEET_ID, range=f"{TAB}!A:Z",
                               valueRenderOption="FORMATTED_VALUE").execute().get("values", [])
    head = rows[0] if rows else []

    def find(name, fallback):
        return head.index(name) + 1 if name in head else fallback
    c_phone, c_id1 = find(H_PHONE, 2), find(H_ID1, 3)
    c_id2 = find(H_ID2, c_id1 + 1)
    c_name, c_src = find(H_NAME, 5), find(H_SRC, 6)

    # 체크박스 열이 빈 행까지 깔려 있어 A열(reg) 기준으로 실제 마지막 행을 잡는다
    last = max((i for i, r in enumerate(rows, start=1) if r and str(r[0]).strip()), default=1)
    index = {}
    for i, r in enumerate(rows[1:last], start=2):
        index.setdefault(digits(col(r, c_phone)), i)

    parsed, skipped = [], []
    for msg in slack_history():
        p = parse(msg)
        if not p:
            continue
        if p["brand"] not in BRANDS:
            skipped.append((p["brand"], p["phone"]))
            continue
        parsed.append(p)
    parsed.sort(key=lambda x: x["reg"])          # 오래된 요청부터
    seen, uniq = set(), []
    for p in parsed:                              # 같은 번호 중복 요청은 1건만
        if p["phone"] in seen:
            continue
        seen.add(p["phone"])
        uniq.append(p)

    data, added, filled = [], 0, 0
    acks = []
    for p in uniq:
        row = index.get(p["phone"])
        if row:                                   # 기존 행: 빈 칸만 채움
            cur = rows[row - 1] if row - 1 < len(rows) else []
            patch = []
            if p["ids"] and not col(cur, c_id1):
                patch.append({"range": f"{TAB}!{a1(c_id1)}{row}", "values": [[p["ids"][0]]]})
            if len(p["ids"]) > 1 and not col(cur, c_id2):
                patch.append({"range": f"{TAB}!{a1(c_id2)}{row}", "values": [[p["ids"][1]]]})
            if p["name"] and not col(cur, c_name):
                patch.append({"range": f"{TAB}!{a1(c_name)}{row}", "values": [[p["name"]]]})
            if p["url"] and not col(cur, c_src):
                patch.append({"range": f"{TAB}!{a1(c_src)}{row}", "values": [[p["url"]]]})
            if patch:
                data += patch
                filled += 1
                acks.append((p, f"{row}행 보강(아이디·고객명)"))
        else:                                     # 신규 행
            last += 1
            data.append({"range": f"{TAB}!A{last}", "values": [[p["reg"]]]})
            data.append({"range": f"{TAB}!{a1(c_phone)}{last}",
                         "values": [[hyphenate(p["phone"])]]})
            if p["ids"]:
                data.append({"range": f"{TAB}!{a1(c_id1)}{last}", "values": [[p["ids"][0]]]})
            if len(p["ids"]) > 1:
                data.append({"range": f"{TAB}!{a1(c_id2)}{last}", "values": [[p["ids"][1]]]})
            data.append({"range": f"{TAB}!{a1(c_name)}{last}", "values": [[p["name"]]]})
            data.append({"range": f"{TAB}!{a1(c_src)}{last}", "values": [[p["url"]]]})
            index[p["phone"]] = last
            added += 1
            acks.append((p, f"{last}행 추가"
                            + (f" / 아이디 {', '.join(p['ids'])}" if p["ids"] else " / 아이디 없음")))

    if data:
        sheets.values().batchUpdate(spreadsheetId=SHEET_ID, body={
            "valueInputOption": "RAW", "data": data}).execute()

    if ACK:
        for p, note in acks:
            ack(p, note)

    print(f"슬랙 차단요청 {len(parsed)}건(자사몰) 처리 → 신규 {added}행, 기존 보강 {filled}행")
    over = [(p["phone"], p["ids_dropped"]) for p in uniq if p["ids_dropped"]]
    if over:
        print("⚠ 아이디가 3개 이상이라 일부 미기입(member_id_3 열 필요):",
              ", ".join(f"{ph}→{ids}" for ph, ids in over))
    if skipped:
        other = {}
        for b, ph in skipped:
            other[b] = other.get(b, 0) + 1
        print("제외(타 몰):", ", ".join(f"{k} {v}건" for k, v in sorted(other.items())))


if __name__ == "__main__":
    main()
