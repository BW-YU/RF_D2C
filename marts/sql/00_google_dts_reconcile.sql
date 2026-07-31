-- 00_google_dts_reconcile.sql
-- 구글 광고 영구 원장(google_ads_raw.rf_google_campaign_daily_cloop / _sprint)의 "확정일" 재동기화.
--
-- 배경: Ads Scripts는 '오늘' 파티션만 매시간 WRITE_TRUNCATE로 적재한다. 그 값은 구글 인트라데이
--   *추정치*라, 밤 마지막 실행 시점에 프리징되면 확정 일일값보다 과소다(마지막 실행~자정 미포함 +
--   인트라데이는 확정보다 낮게 잡히고 다음날 상향 확정). 소액 계정(스프린트)일수록 % 과소가 크다.
--   예) 2026-07-30 검증: cloop 스크립트 656,596 vs 구글UI 707,328(-7.2%), sprint 44,478 vs 53,256(-16.5%).
--
-- 처리: DTS(p_ads_CampaignBasicStats)가 D-1을 확정하면 그 확정값으로 과거 파티션을 덮어쓴다.
--   → 결과: '오늘' = 스크립트 실시간 / '과거(DTS 보유일)' = DTS 확정. 정확도 회복 + 확정값이 영구
--     원장에 누적되어 향후 DTS 삭제(단일화) 경로도 열림.
--
-- 규칙:
--   * '오늘'(report_date = CURRENT_DATE) 파티션은 절대 건드리지 않음(스크립트 실시간 유지, 구멍 방지).
--   * DTS가 아직 못 실은 어제(예: 오전 실행 시 D-1)는 dts_recent에 없으므로 미변경 → 스크립트값 유지
--     (구멍 없이 소폭 과소로 남았다가, DTS가 실은 다음 실행에서 자동 확정 보정. 최대 ~1일 지연).
--   * 멱등: 최근 14일을 매 실행 DELETE+INSERT(재실행 안전). DTS 세그먼트 다행은 그대로 적재(마트가 SUM).
--   * 파일명 00_ → refresh_marts.py 정렬 실행에서 마트 뷰(10_)/테이블(20_)보다 먼저 돌아 원장을 먼저 확정.
--
-- 계정: cloop customer_id=2580015098, sprint=2082026590. DTS 원천 접미사 _3030273599(전송 계정).

DECLARE win_start DATE DEFAULT DATE_SUB(CURRENT_DATE('Asia/Seoul'), INTERVAL 14 DAY);

-- DTS 확정 소스(오늘 제외, 최근 14일, cloop/sprint만). 캠페인명은 p_ads_Campaign에서 조인.
CREATE TEMP TABLE dts_recent AS
WITH nm AS (
  SELECT campaign_id, ANY_VALUE(campaign_name) AS campaign_name
  FROM `rf-ads-db-500505.google_ads_raw.p_ads_Campaign_3030273599`
  GROUP BY campaign_id
)
SELECT
  s.segments_date AS report_date,
  s.customer_id,
  CASE s.customer_id WHEN 2580015098 THEN 'cloop' WHEN 2082026590 THEN 'sprint' END AS mall,
  s.campaign_id,
  nm.campaign_name,
  CAST(s.metrics_impressions AS INT64) AS impressions,
  CAST(s.metrics_clicks AS INT64)      AS clicks,
  s.metrics_cost_micros / 1e6          AS cost,
  s.metrics_conversions                AS conversions,
  s.metrics_conversions_value          AS conversion_value,
  CURRENT_TIMESTAMP()                  AS batch_time
FROM `rf-ads-db-500505.google_ads_raw.p_ads_CampaignBasicStats_3030273599` s
LEFT JOIN nm USING (campaign_id)
WHERE s.segments_date >= win_start
  AND s.segments_date < CURRENT_DATE('Asia/Seoul')   -- 오늘 제외(스크립트 실시간 보존)
  AND s.customer_id IN (2580015098, 2082026590);

-- cloop: DTS가 확정 보유한 날만 교체(그 외 날짜=스크립트값 유지)
DELETE FROM `rf-ads-db-500505.google_ads_raw.rf_google_campaign_daily_cloop`
WHERE report_date >= win_start
  AND report_date < CURRENT_DATE('Asia/Seoul')
  AND report_date IN (SELECT DISTINCT report_date FROM dts_recent WHERE mall = 'cloop');
INSERT INTO `rf-ads-db-500505.google_ads_raw.rf_google_campaign_daily_cloop`
  (report_date, customer_id, mall, campaign_id, campaign_name, impressions, clicks, cost, conversions, conversion_value, batch_time)
SELECT report_date, customer_id, mall, campaign_id, campaign_name, impressions, clicks, cost, conversions, conversion_value, batch_time
FROM dts_recent WHERE mall = 'cloop';

-- sprint
DELETE FROM `rf-ads-db-500505.google_ads_raw.rf_google_campaign_daily_sprint`
WHERE report_date >= win_start
  AND report_date < CURRENT_DATE('Asia/Seoul')
  AND report_date IN (SELECT DISTINCT report_date FROM dts_recent WHERE mall = 'sprint');
INSERT INTO `rf-ads-db-500505.google_ads_raw.rf_google_campaign_daily_sprint`
  (report_date, customer_id, mall, campaign_id, campaign_name, impressions, clicks, cost, conversions, conversion_value, batch_time)
SELECT report_date, customer_id, mall, campaign_id, campaign_name, impressions, clicks, cost, conversions, conversion_value, batch_time
FROM dts_recent WHERE mall = 'sprint';
