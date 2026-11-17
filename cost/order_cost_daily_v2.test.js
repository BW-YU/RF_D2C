"use strict";
const assert = require("assert");
const { loadRatecard, dateValue, dealFor, productType, rateAsOf, computeDaily } = require("./order_cost_daily_v2");

const card = loadRatecard();
// 261115 파서는 조각 구성(comp)도 돌려준다 — 기존 계약(원가·캔·조각 수)만 비교할 때 쓴다.
const core = x => x && ({ boxCost: x.boxCost, cans: x.cans, pieces: x.pieces });
assert.equal(productType("클룹 애사비즈 30개입"), "파우치");
assert.equal(productType("스프린트 에너지 24개입"), "캔");
assert.deepEqual(rateAsOf(card, "2026-09-14", "캔", 24, false), { cost: 4954, exact: true });
assert.deepEqual(rateAsOf(card, "2026-09-14", "캔", 48, true), { cost: 10878, exact: true });
assert.equal(rateAsOf(card, "2026-09-14", "캔", 30, false).cost, 7635);
const prices = [{ mall: "cloop", brand: "애사비", deal: "기본딜", packCount: 48,
  salePrice: 46900, effectiveFrom: "2026-07-30", effectiveTo: null }];
assert.equal(dealFor("애사비소다", "48개입", "cloop", "2026-09-14", 48, 46900, prices).deal, "기본딜");
assert.equal(dealFor("애사비소다", "24개입", "cloop", "2026-09-14", 24, 25900, prices).deal, "오가닉");
assert.equal(dateValue({ value: "2026-08-01" }), "2026-08-01");
const ledger = new Map([["애사비즈, 파인애플", [{ ef: "2026-01-01", cost: 100 }]]]);
const daily = computeDaily([{ date: "2026-08-01", orderDate: "2026-07-31", deliveredDate: "2026-08-03",
  mallId: "cloop", orderId: "o1", productName: "애사비즈 24개입", optionName: "파인애플 24개입",
  quantity: 1, allocatedNet: 10000 }], ledger, card, []);
assert.equal(daily[0].prior_order_month_orders, 1);
assert.equal(daily[0].prior_order_month_net_revenue, 10000);
assert.equal(daily[0].same_order_month_net_revenue, 0);
assert.equal(daily[0].avg_order_to_ship_days, 1);
assert.equal(daily[0].delivery_date_coverage, 1);
// 261002: 그룹 평균 폴백은 용량 없는 비캔 품목(보틀)을 빼고 캔만 평균한다.
const { parseCost, ovGroups, ovBoxCost } = require("./cost_to_bigquery");
const sp = parseCost([[0, 0, 0, "[상온]클룹_스프린트,진격거,500ml*1입", 0, 0, 400],
  [0, 0, 0, "[상온]클룹_스프린트,에반게리온,보틀", 0, 0, 12580]]);
ovGroups(sp);
assert.deepEqual(core(ovBoxCost("스프린트 신상 에너지 355mL", "개입 수=24개입_1", sp, false)), { boxCost: 9600, cans: 24, pieces: 1 });
// 261002: 상품명에 용량이 없으면 그룹 주력 규격(여기선 500mL) 단가를 쓴다(맛 일치 첫 품목 250mL로 새지 않게).
const as = parseCost([[0, 0, 0, "클룹_애사비소다,오리지널,250ml", 0, 0, 237],
  [0, 0, 0, "클룹_애사비소다,오리지널,500ml", 0, 0, 292],
  [0, 0, 0, "클룹_애사비소다,청사과,500ml", 0, 0, 300]]);
ovGroups(as);
assert.equal(ovBoxCost("[시크릿 특가] 애사비소다 최저가", "맛 선택=오리지널*24", as, false).boxCost, 292 * 24);
assert.equal(ovBoxCost("클룹 애사비소다 250mL", "맛 선택=오리지널*24", as, false).boxCost, 237 * 24);
// 261002: 맛 없는 옵션의 그룹 평균에서 페트를 뺀다.
const ap = parseCost([[0, 0, 0, "클룹_애사비소다,오리지널,500ml", 0, 0, 292],
  [0, 0, 0, "[상온]클룹_페트,애사비소다,오리지널,500ml", 0, 0, 126]]);
ovGroups(ap);
assert.equal(ovBoxCost("클룹 애사비소다 500mL", "개입 수=48개입#1", ap, false).boxCost, 292 * 48);
// 261002: 주력 규격이 250mL인 그룹은 250mL가 기본이다.
const se = parseCost([[0, 0, 0, "클룹_스프린트에너지,사우어베리,250ml", 0, 0, 248],
  [0, 0, 0, "클룹_스프린트에너지,애플블라스트,250ml", 0, 0, 245],
  [0, 0, 0, "클룹_스프린트에너지,레몬,500ml", 0, 0, 420]]);
ovGroups(se);
assert.equal(ovBoxCost("[시크릿 특가] 스프린트 에너지드링크 4종 골라담기", "개입 수=48개입#1", se, false).boxCost, Math.round((248 + 245) / 2) * 48);
// 261002: 맛별 고정 단가(라임브리즈 328 등)를 없앴다 — 원장 단가를 쓴다.
const oh = parseCost([[0, 0, 0, "[상온]클룹_오프아워,라임브리즈,350ml*1입", 0, 0, 351],
  [0, 0, 0, "[상온]클룹_오프아워,피치릴렉서,350ml*1입", 0, 0, 352]]);
ovGroups(oh);
assert.equal(ovBoxCost("클룹 오프아워 350mL", "맛 선택=라임브리즈*24", oh, false).boxCost, 351 * 24);
// 261006: 주문명에 용량이 없으면 카탈로그명 용량으로 원가 단가를 고른다(딜·브랜드는 주문명 그대로).
const { sizedName } = require("./order_cost_daily_v2");
assert.equal(sizedName("[시크릿 특가] 스프린트 에너지드링크 4종 최저가로 골라담기", "[썸머블프 특가] 스프린트 에너지 500mL 4종 | 진격의거인"),
  "[시크릿 특가] 스프린트 에너지드링크 4종 최저가로 골라담기 500ml");
assert.equal(sizedName("클룹 애사비소다 500mL", "클룹 애사비소다 250mL"), "클룹 애사비소다 500mL");
assert.equal(sizedName("[골라담기] 3종 맛보기 럭키박스", "[골라담기] 3종 맛보기 럭키박스"), "[골라담기] 3종 맛보기 럭키박스");
const se5 = parseCost([[0, 0, 0, "클룹_스프린트에너지,사우어베리,250ml", 0, 0, 248], [0, 0, 0, "클룹_스프린트에너지,애플블라스트,250ml", 0, 0, 245],
  [0, 0, 0, "클룹_스프린트에너지,레몬,500ml", 0, 0, 420], [0, 0, 0, "클룹_스프린트에너지,자몽,500ml", 0, 0, 430]]);
ovGroups(se5);
assert.equal(ovBoxCost(sizedName("[시크릿 특가] 스프린트 에너지드링크 4종 최저가로 골라담기", "스프린트 에너지 500mL 4종"), "개입 수=48개입#1", se5, false).boxCost, 425 * 48);
console.log("order_cost_daily_v2 tests: ok");
// 261016: 다브랜드 골라담기(클룹 593) — 상품명에 그룹이 없고 구성은 추가옵션에만 있다. 조각별 그룹·맛 단가로 원가를 낸다.
const { ovMixedBoxCost } = require("./cost_to_bigquery");
const mix = parseCost([[0, 0, 0, "[상온]클룹_애사비소다,사이다,250ml*1입", 0, 0, 290],
  [0, 0, 0, "[상온]클룹_애사비소다,콜라,250ml*1입", 0, 0, 295],
  [0, 0, 0, "[상온]클룹_오프아워,자몽피즈,355ml*1입", 0, 0, 360]]);
ovGroups(mix);
assert.deepEqual(core(ovMixedBoxCost("맛 선택=애사비소다 사이다*12 / 오프아워 자몽피즈*36", mix)), { boxCost: 290 * 12 + 360 * 36, cans: 48, pieces: 2 });
assert.equal(ovMixedBoxCost("맛 선택=애사비소다 사이다*12 / 알수없는음료*12", mix), null);   // 한 조각이라도 못 읽으면 미매칭으로 남긴다
assert.equal(ovMixedBoxCost("맛 선택=애사비소다 사이다", mix), null);                           // 수량 없는 조각
assert.equal(ovBoxCost("[한정판매] 베스트 제품 내맘대로 골라담기", "구성=48개입#1", mix, false), null);
const d593 = computeDaily([{ date: "2026-10-15", orderDate: "2026-10-15", mallId: "cloop", orderId: "o593",
  productName: "[한정판매] 베스트 제품 내맘대로 골라담기", optionName: "구성=48개입#1",
  addOption: "맛 선택=애사비소다 콜라*24 / 오프아워 자몽피즈*24", quantity: 1, allocatedNet: 30000 }],
  new Map([["[상온]클룹_애사비소다,콜라,250ml*1입", [{ ef: "2026-01-01", cost: 295 }]],
           ["[상온]클룹_오프아워,자몽피즈,355ml*1입", [{ ef: "2026-01-01", cost: 360 }]]]), card, []);
assert.equal(d593[0].cost_coverage, 1);
assert.equal(d593[0].cogs, 295 * 24 + 360 * 24);
console.log("order_cost_daily_v2 tests OK");
// 261017: 유주얌 6차 공구 — 상품명·조각에 그룹이 없는 짧은 맛 이름(사과12 + 감귤12 …). 정확히 아는 맛만 그룹을 붙인다.
const { ovShortBoxCost } = require("./cost_to_bigquery");
const yjm = parseCost([[0, 0, 0, "[상온]클룹_애사비소다,오리지널,500ml*1입", 0, 0, 290], [0, 0, 0, "[상온]클룹_애사비소다,오리지널,500ml*1입 페트", 0, 0, 126],
  [0, 0, 0, "[상온]클룹_애사비소다,콜라,500ml*1입", 0, 0, 296], [0, 0, 0, "[상온]클룹_오프아워,피치릴렉서,350ml*1입", 0, 0, 340],
  [0, 0, 0, "[상온]클룹_제로소다,레몬,250ml*1입", 0, 0, 227]]);
ovGroups(yjm);
const YP = "[유주얌 6차 마켓] 클룹 베스트셀러 특가";
assert.deepEqual(core(ovShortBoxCost(YP, "유주얌 전용 특가=[BEST/48개입] 사과24 + 콜라24", yjm)), { boxCost: 290 * 24 + 296 * 24, cans: 48, pieces: 2 });  // 캔 단가(페트 제외)
assert.deepEqual(core(ovShortBoxCost(YP, "유주얌 전용 특가=[혼합/24개입] 피치릴렉서24", yjm)), { boxCost: 340 * 24, cans: 24, pieces: 1 });
assert.equal(ovShortBoxCost(YP, "유주얌 전용 특가=[48개입] 사과24 + 바나나24", yjm), null);   // 모르는 맛 하나라도 있으면 미매칭
assert.equal(ovShortBoxCost("애사비소다 사과 500ml", "사과12", yjm), null);                     // 상품명에 그룹이 있으면 기존 경로
console.log("order_cost_daily_v2 short-flavor tests OK");
// 261021: 슈퍼포커스 굿즈 세트 단계형 옵션 — 병수는 「수량 선택=N개입」만. 맛 숫자·굿즈 「3종」을 더하지 않는다.
const spf = parseCost([[0, 0, 0, "[상온]클룹_스프린트,슈퍼포커스 오리지널,355ml*1입", 0, 0, 330], [0, 0, 0, "[상온]클룹_스프린트,슈퍼포커스 울트라바이올렛,355ml*1입", 0, 0, 340]]);
ovGroups(spf);
const SPN = "스프린트 슈퍼포커스 에너지 355mL 3종 | 사이버펑크";
const sf1 = ovBoxCost(SPN, "① 세트 선택=음료 + 장패드 세트, ② 음료 수량 선택=24개입 (1박스), ③ 맛 선택=[베스트] 오리지널 12개입 + 울트라바이올렛 12개입", spf, false);
assert.deepEqual(core(sf1), { boxCost: 330 * 12 + 340 * 12, cans: 24, pieces: 2 });
const sf2 = ovBoxCost(SPN, "① 세트 선택=[한정판매] 음료 + 굿즈 3종 세트 (아크릴스탠드 + 장패드 + 메탈키링 1종), ② 음료 수량 선택=72개입", spf, false);
assert.equal(sf2.cans, 72);
const sf3 = ovBoxCost(SPN, "① 세트 선택=[베스트] 음료 단독 세트, ② 음료 수량 선택=48개입 (2박스), ③ 맛 선택=오리지널 12개입", spf, false);
assert.equal(sf3.cans, 48);   // 맛 합계가 수량과 안 맞으면 수량을 믿는다
assert.equal(ovBoxCost(SPN, "개입 수=24개입_1", spf, false).cans, 24);   // 기존 형식 그대로
console.log("order_cost_daily_v2 set-option tests OK");
// 261115: 줄 브랜드 분할 — 혼합 상자는 조각 브랜드로 원가·캔·순매출을 나누고, 합계 원가는 그대로다. 딜 축에 순매출(net)을 싣는다.
const { lineBrandSplit } = require("./order_cost_daily_v2");
ovGroups(mix);
const dm2 = computeDaily([{ date: "2026-11-02", orderDate: "2026-11-02", mallId: "cloop", orderId: "m1",
  productName: "[한정판매] 베스트 제품 내맘대로 골라담기", optionName: "구성=48개입#1",
  addOption: "맛 선택=애사비소다 콜라*24 / 오프아워 자몽피즈*24", quantity: 1, allocatedNet: 30000 },
  { date: "2026-11-02", orderDate: "2026-11-02", mallId: "cloop", orderId: "m2",
  productName: "클룹 애사비소다 250mL", optionName: "맛 선택=사이다*24", quantity: 1, allocatedNet: 12000 }],
  new Map([["[상온]클룹_애사비소다,콜라,250ml*1입", [{ ef: "2026-01-01", cost: 295 }]],
           ["[상온]클룹_애사비소다,사이다,250ml*1입", [{ ef: "2026-01-01", cost: 290 }]],
           ["[상온]클룹_오프아워,자몽피즈,355ml*1입", [{ ef: "2026-01-01", cost: 360 }]]]), card, []);
const deals2 = JSON.parse(dm2[0].deal_breakdown_json), by2 = b => deals2.filter(x => x.brand === b);
assert.equal(dm2[0].cogs, 295 * 24 + 360 * 24 + 290 * 24);                                   // 합계 불변
assert.equal(deals2.reduce((s, x) => s + x.cogs, 0), dm2[0].cogs);
assert.equal(deals2.reduce((s, x) => s + x.net, 0), 42000);                                   // 순매출 = 줄 배분액 합
assert.equal(by2("오프아워")[0].cogs, 360 * 24);                                                // 예전엔 콜라 몫까지 오프아워
assert.equal(by2("오프아워")[0].net, 15000);                                                    // 캔 수 비율
assert.equal(by2("콜사비")[0].cogs, 295 * 24);
assert.equal(by2("애사비즈")[0].net, 12000);                                                    // 맛 글자에 브랜드가 없으면 상품명 브랜드(기존과 같음)
assert.equal(deals2.reduce((s, x) => s + x.item_lines, 0), dm2[0].item_line_count);           // 줄 수는 한 번만
assert.deepEqual(lineBrandSplit(null, "아무거나", "기타"), [{ brand: "기타", cogsShare: 1, unitShare: 1 }]);
assert.deepEqual(lineBrandSplit({ comp: [{ text: "오리지널", cogs: 100, cans: 2 }] }, "클룹 애사비소다", "콜사비"),
  [{ brand: "애사비즈", cogsShare: 1, unitShare: 1 }]);
console.log("order_cost_daily_v2 brand-split tests OK");

// 261118 「개입 수=N개입」 + 반각 「+」 맛 구성: 총 개입 수와 맛별 수량을 더하지 않는다(11-17 오프아워 24입이 42캔으로 집계).
const ohp = parseCost([[0, 0, 0, "[상온]클룹_오프아워,라임브리즈,350ml*1입", 0, 0, 351],
  [0, 0, 0, "[상온]클룹_오프아워,피치릴렉서,350ml*1입", 0, 0, 352],
  [0, 0, 0, "[상온]클룹_오프아워,자몽피즈,350ml*1입", 0, 0, 353]]);
ovGroups(ohp);
const ohPn = "[시크릿 특가] 300만 캔 돌파 기념! 클룹 오프아워 최저가";
assert.equal(ovBoxCost(ohPn, "① 개입 수=[체험팩] 24개입, ② 맛 구성=[베스트] 라임 12 + 자몽 6 + 피치릴렉서 6", ohp, false).cans, 24);
assert.equal(ovBoxCost(ohPn, "① 개입 수=[체험팩] 24개입, ② 맛 구성=[베스트] 라임 12 + 자몽 6 + 피치릴렉서 6", ohp, false).boxCost, 351 * 12 + 353 * 6 + 352 * 6);
assert.equal(ovBoxCost(ohPn, "① 개입 수=[베스트] 48개입, ② 맛 구성=[베스트] 라임브리즈 24 ＋ 자몽피즈 12 ＋ 피치릴렉서 12", ohp, false).cans, 48);
// 1+1 「(본품+증정)」: 증정 6입은 같은 주문의 100원 줄로 따로 온다 — 본품 구성만 센다.
const ohOne = "[출시특가] 오프아워 시크릿 1+1 & 100원 특가!";
assert.equal(ovBoxCost(ohOne, "개입 수=[베스트] 30개입 (24+6), 본품 구성 *6개입 별도 선택=[NEW] 라임 12 + 자몽 12", ohp, false).cans, 24);
assert.equal(ovBoxCost(ohOne, "① 총 개입 수=[체험팩] 18개입 (12+6), ② 본품 구성 *6개입 별도 선택=[베스트] 라임브리즈 6 ＋ 피치릴렉서 6", ohp, false).cans, 12);
console.log("order_cost_daily_v2 pack-total tests OK");
