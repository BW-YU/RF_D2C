# DTC 비용 원장 사용법

정본은 BigQuery `rf-ads-db-500505.ops_input.dtc_cost_registry`다. 대시보드는 이 표를 매일 읽어
PA·CPS·CRM 보강·마테크를 CM1에, 브랜드광고를 CM2에 반영한다.

## 입력 규칙

- `cost_id`: 계약/비용별 불변 ID. 같은 ID의 조건이 바뀌면 기존 `valid_to`를 닫고 새 행을 추가한다.
- `cost_class`: `pa|cps|crm|martech|brand_ad|other`
- `cm_scope`: `CM1|CM2`. 브랜드광고는 `CM2`.
- `mall`: `all|cloop|sprint`
- `allocation_basis`:
  - `daily`: amount를 매일 인식
  - `monthly`: amount를 해당 월의 일수로 나눠 인식
  - `period_total`: amount를 valid_from~valid_to에 균등 인식
  - `net_pct`: 당일 순매출 × rate. rate는 소수(`0.00342 = 0.342%`)
- `amount`와 `rate` 중 해당 basis에 필요한 값만 채운다.
- 원천 API나 실청구 배선이 생기면 기존 추정 행의 `valid_to`를 닫아 이중계상을 막는다.

## 신규 비용 예시

```sql
INSERT INTO `rf-ads-db-500505.ops_input.dtc_cost_registry` (
  cost_id,cost_name,cost_class,cm_scope,mall,allocation_basis,amount,rate,
  valid_from,valid_to,vendor,contract_id,campaign_id,source_ref,note,
  active,updated_by,updated_at
) VALUES (
  'brand_campaign_2609','9월 브랜드 캠페인','brand_ad','CM2','all','period_total',
  10000000,NULL,DATE '2026-09-01',DATE '2026-09-30','거래처','계약번호',NULL,
  '증빙 링크','9월 계약 총액',TRUE,'name@egnis.kr',CURRENT_TIMESTAMP()
);
```

CSV 양식은 `dtc_cost_registry_TEMPLATE.csv`, 최초 생성과 기존 7개 비용 이관은
`dtc_cost_registry.sql`·`dtc_cost_registry_seed.sql`에 있다.

## 검증

```sql
SELECT cost_class,cm_scope,COUNT(*) rows,SUM(amount) amount
FROM `rf-ads-db-500505.ops_input.dtc_cost_registry`
WHERE active
GROUP BY 1,2 ORDER BY 2,1;
```

미분류 비용을 0원으로 간주하지 않는다. 분류가 결정되지 않았으면 `other/CM1`으로 보수 차감하고
note에 임시 분류임을 남긴다.
