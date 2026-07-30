-- 매체 통합 소스 뷰: 메타 + 네이버 + 구글(영구 일별 원장: DTS백필 과거 + Ads Scripts 당일/이력) + 카카오를 공통 컬럼으로 통일 (캠페인 단위)
-- 몰 구분: 메타=account_id, 네이버=account, 구글=customer_id
CREATE OR REPLACE VIEW `rf-ads-db-500505.mart.ad_unified_src` AS
WITH meta AS (
  SELECT report_date,
    CASE
      WHEN account_id IN ('1462607070849777','793134085895227','3589083851393515') THEN 'cloop'
      WHEN account_id = '3342733785912061' THEN 'sprint'
      ELSE 'unknown' END AS mall,
    'meta' AS media, campaign_id, ANY_VALUE(campaign_name) AS campaign_name,
    CAST(NULL AS STRING) AS landing,
    SUM(impressions) AS impressions, SUM(clicks) AS clicks, SUM(spend) AS cost,
    SUM(web_purchase_count) AS conversions, SUM(web_purchase_value) AS conversion_value
  FROM `rf-ads-db-500505.meta_ads.rf_meta_ads`
  WHERE report_date >= DATE_SUB(CURRENT_DATE('Asia/Seoul'), INTERVAL 2 YEAR)
  GROUP BY report_date, mall, campaign_id
),
naver AS (
  SELECT report_date, LOWER(account) AS mall, 'naver' AS media, campaign_id,
    ANY_VALUE(campaign_name) AS campaign_name,
    ANY_VALUE(lp_type) AS landing,   -- 자사몰 / 스마트스토어 구분
    SUM(impressions) AS impressions, SUM(clicks) AS clicks, SUM(cost) AS cost,
    SUM(conversions) AS conversions, SUM(conversion_value) AS conversion_value
  FROM `rf-ads-db-500505.naver_ads.rf_naver_sa_ads`
  WHERE report_date >= DATE_SUB(CURRENT_DATE('Asia/Seoul'), INTERVAL 2 YEAR) AND level='campaign'
  GROUP BY report_date, mall, campaign_id
),
-- 구글: rf_google_campaign_daily = 영구 파티션 원장(rf_google_campaign_daily_cloop/_sprint UNION). DTS백필 과거 + Ads Scripts 매시간 적재(당일 포함).
--   ※ 2026-07-31 rf_google_campaign_current(DTS<오늘 UNION 옛 intraday)에서 전환: 스크립트 컷오버 후 intraday 미적재로 당일/어제 구멍 발생 → 영구 원장으로 재연결.
g_stats AS (
  SELECT report_date, mall,
    CAST(campaign_id AS STRING) AS campaign_id,
    ANY_VALUE(campaign_name) AS campaign_name,
    SUM(impressions) AS impressions,
    SUM(clicks) AS clicks,
    SUM(cost) AS cost,
    SUM(conversions) AS conversions,
    SUM(conversion_value) AS conversion_value
  FROM `rf-ads-db-500505.google_ads_raw.rf_google_campaign_daily`
  WHERE report_date >= DATE_SUB(CURRENT_DATE('Asia/Seoul'), INTERVAL 2 YEAR)
  GROUP BY report_date, mall, campaign_id
),
google AS (
  SELECT report_date, mall, 'google' AS media, campaign_id,
    campaign_name, CAST(NULL AS STRING) AS landing,
    impressions, clicks, cost, conversions, conversion_value
  FROM g_stats
),
-- 카카오모먼트: 캠페인 단위(rf_kakao_campaign). 몰=광고계정, 전환은 미수집(NULL).
kakao AS (
  SELECT date AS report_date,
    CASE ad_account_id WHEN '501057' THEN 'cloop' WHEN '800005' THEN 'sprint' ELSE 'unknown' END AS mall,
    'kakao' AS media, campaign_id, ANY_VALUE(campaign_name) AS campaign_name,
    CAST(NULL AS STRING) AS landing,
    SUM(impressions) AS impressions, SUM(clicks) AS clicks, SUM(cost) AS cost,
    CAST(NULL AS FLOAT64) AS conversions, CAST(NULL AS FLOAT64) AS conversion_value
  FROM `rf-ads-db-500505.kakao_moment.rf_kakao_campaign`
  WHERE date >= DATE_SUB(CURRENT_DATE('Asia/Seoul'), INTERVAL 2 YEAR)
  GROUP BY report_date, mall, campaign_id
),
-- 광고 어드민 다운로드 RAW(과거 이력 보완, mart.ad_manual).
-- 중복 방지: 같은 (media,mall,report_date)가 API에 있으면 API 우선, 없을 때만 수동 RAW 사용.
api_union AS (
  SELECT * FROM meta
  UNION ALL SELECT * FROM naver
  UNION ALL SELECT * FROM google
  UNION ALL SELECT * FROM kakao
),
api_dates AS (
  SELECT DISTINCT media, mall, report_date FROM api_union
),
manual AS (
  SELECT m.report_date, m.mall, m.media, m.campaign_id, m.campaign_name,
    m.landing,
    m.impressions, m.clicks, m.cost, m.conversions, m.conversion_value
  FROM `rf-ads-db-500505.mart.ad_manual` m
  LEFT JOIN api_dates d
    ON d.media = m.media AND d.mall = m.mall AND d.report_date = m.report_date
  WHERE m.report_date >= DATE_SUB(CURRENT_DATE('Asia/Seoul'), INTERVAL 2 YEAR)
    AND d.media IS NULL   -- API가 커버하지 않는 (media,mall,일자)만 수동 RAW 사용
)
-- sales_channel(판매채널): 스마트스토어(단일 클룹) vs 자사몰(몰별). 매출 매칭용.
--   landing=스마트스토어 → 'smartstore' / 그 외(자사몰·NULL) → 몰(cloop/sprint)=cafe24 자사몰
SELECT *, CASE WHEN landing = '스마트스토어' THEN 'smartstore' ELSE mall END AS sales_channel
FROM (
  SELECT * FROM api_union
  UNION ALL SELECT * FROM manual
)
