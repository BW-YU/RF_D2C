# 광고·자사몰 → BigQuery 적재

내부용 데이터 수집·mart 파이프라인. 비밀값은 GitHub Secrets로 관리한다.

## Google Ads

- 수집기: `google_ads/google_ads_to_bigquery.py`
- 워크플로: `.github/workflows/google_ads_hourly.yml`
- 현재 상태: **수동 전용**. 첫 운영 검증에서 Developer Token이 테스트 계정 전용으로 확인돼
  `DEVELOPER_TOKEN_NOT_APPROVED`가 발생했다. Google Ads API Center에서 Basic 또는 Standard
  access 승인 후 매시 20분 스케줄을 복원한다.
- 범위: 공식 Google Ads API 읽기 전용, 기본 최근 3일(오늘 포함)
- 적재: `google_ads_raw.rf_google_campaign_daily_cloop` 날짜 파티션을 덮어쓴 뒤
  같은 구간의 Google 행만 `mart.ad_unified_daily`에 원자적으로 반영한다.
- DTS는 확정 백필 경로로 계속 유지한다. API 수집은 승인 후 D-1·당일 공백을 메우며 광고 설정은 변경하지 않는다.
