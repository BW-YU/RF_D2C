"use strict";
const assert = require("assert");
const { loadRatecard, dateValue, dealFor, productType, rateAsOf, computeDaily } = require("./order_cost_daily_v2");

const card = loadRatecard();
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
assert.deepEqual(ovBoxCost("스프린트 신상 에너지 355mL", "개입 수=24개입_1", sp, false), { boxCost: 9600, cans: 24, pieces: 1 });
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
