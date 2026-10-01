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
console.log("order_cost_daily_v2 tests: ok");
