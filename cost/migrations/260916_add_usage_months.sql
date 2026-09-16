ALTER TABLE `rf-ads-db-500505.ops_input.dtc_cost_registry`
ADD COLUMN IF NOT EXISTS usage_months INT64
OPTIONS(description='period_total 활용 개월 수. valid_from + N개월 - 1일로 일할 종료일 계산');

ASSERT (
  SELECT COUNT(*) = 0
  FROM `rf-ads-db-500505.ops_input.dtc_cost_registry`
  WHERE usage_months IS NOT NULL AND usage_months <= 0
) AS 'usage_months must be a positive integer';
