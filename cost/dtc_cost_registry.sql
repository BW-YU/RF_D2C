-- DTC 비매체 비용 입력 SSOT. 금액은 VAT 제외 기준을 권장한다.
CREATE TABLE IF NOT EXISTS `rf-ads-db-500505.ops_input.dtc_cost_registry` (
  cost_id STRING NOT NULL OPTIONS(description='변하지 않는 비용 식별자'),
  cost_name STRING NOT NULL,
  cost_class STRING NOT NULL OPTIONS(description='pa|cps|crm|martech|brand_ad|other'),
  cm_scope STRING NOT NULL OPTIONS(description='CM1 또는 CM2'),
  mall STRING NOT NULL OPTIONS(description='all|cloop|sprint'),
  allocation_basis STRING NOT NULL OPTIONS(description='daily|monthly|period_total|net_pct'),
  amount NUMERIC OPTIONS(description='daily/monthly/period_total 원화 금액'),
  rate FLOAT64 OPTIONS(description='net_pct용 소수 비율. 0.00342 = 0.342%'),
  valid_from DATE NOT NULL,
  valid_to DATE NOT NULL,
  vendor STRING,
  contract_id STRING,
  campaign_id STRING,
  source_ref STRING,
  note STRING,
  active BOOL NOT NULL,
  updated_by STRING,
  updated_at TIMESTAMP NOT NULL
)
CLUSTER BY cm_scope, cost_class, mall
OPTIONS(description='PA·CPS·CRM·마테크·브랜드광고 비용의 유효기간형 입력 원장. CM1/CM2 경계를 명시한다.');

