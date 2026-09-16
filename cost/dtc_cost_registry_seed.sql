-- 기존 2026-07/08 관리회계 대사로 확인한 경상 비용 최초 이관.
MERGE `rf-ads-db-500505.ops_input.dtc_cost_registry` T
USING (
  SELECT 'toss_cps' cost_id,'토스페이 CPS' cost_name,'cps' cost_class,'CM1' cm_scope,'all' mall,'net_pct' allocation_basis,CAST(NULL AS NUMERIC) amount,0.00342 rate,DATE '2026-07-01' valid_from,DATE '2099-12-31' valid_to,'비바리퍼블리카' vendor,CAST(NULL AS STRING) contract_id,CAST(NULL AS STRING) campaign_id,'2026-07/08 관리회계 세일즈광고 대사' source_ref,'순매출 연동' note
  UNION ALL SELECT 'payco_cps','페이코 CPS','cps','CM1','all','net_pct',NULL,0.00195,DATE '2026-07-01',DATE '2099-12-31','NHN페이코',NULL,NULL,'2026-07/08 관리회계 세일즈광고 대사','순매출 연동'
  UNION ALL SELECT 'flowlink_2026','의적단 딜커머스','pa','CM1','all','monthly',6790000,NULL,DATE '2026-01-01',DATE '2026-12-31','플로우링크',NULL,NULL,'2026-07/08 관리회계 세일즈광고 대사','월 결산분개'
  UNION ALL SELECT 'naver_brand_search','네이버 브랜드검색 당월분','pa','CM1','all','monthly',4690000,NULL,DATE '2026-07-01',DATE '2099-12-31','네이버',NULL,NULL,'2026-07/08 관리회계 세일즈광고 대사','전월 이월분 제외'
  UNION ALL SELECT 'ifdo_base','이프두 구독 기본','martech','CM1','all','monthly',860000,NULL,DATE '2026-07-01',DATE '2099-12-31','니블스카이',NULL,NULL,'2026-07/08 관리회계 세일즈광고 대사','사용량 초과분 제외'
  UNION ALL SELECT 'kakao_moment_prepaid','카카오모먼트 선급대체','crm','CM1','all','monthly',950000,NULL,DATE '2026-07-01',DATE '2099-12-31','카카오',NULL,NULL,'2026-07/08 관리회계 세일즈광고 대사','원천 배선 완료 시 종료'
  UNION ALL SELECT 'bloomai_send','블룸에이아이 발송','crm','CM1','all','monthly',575000,NULL,DATE '2026-07-01',DATE '2099-12-31','블룸에이아이',NULL,NULL,'2026-07/08 관리회계 세일즈광고 대사','CRM 뷰 미포함분'
) S
ON T.cost_id=S.cost_id AND T.valid_from=S.valid_from
WHEN NOT MATCHED THEN INSERT (
  cost_id,cost_name,cost_class,cm_scope,mall,allocation_basis,amount,rate,usage_months,
  valid_from,valid_to,vendor,contract_id,campaign_id,source_ref,note,active,updated_by,updated_at
) VALUES (
  S.cost_id,S.cost_name,S.cost_class,S.cm_scope,S.mall,S.allocation_basis,S.amount,S.rate,NULL,
  S.valid_from,S.valid_to,S.vendor,S.contract_id,S.campaign_id,S.source_ref,S.note,TRUE,'codex-seed',CURRENT_TIMESTAMP()
);
