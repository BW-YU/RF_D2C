#!/usr/bin/env node
/**
 * 주문 구성 기반 일별 원가·물류 추정 v2.
 *
 * 계산 grain: shipped_date × mall × order_id × order_item_code
 * 저장 grain: shipped_date × mall. 주문월→출고월 이월을 별도 보존한다.
 * Cafe24 delivered_date는 역사 구간 결손이 커 회계 인식일로 직접 쓰지 않는다.
 * 회계 실청구가 아니며, 미매칭을 버리지 않고 품질 컬럼으로 함께 저장한다.
 */
"use strict";

const fs = require("fs");
const path = require("path");
const { BigQuery } = require("@google-cloud/bigquery");
const {
  parseCost, ovGroups, ovBoxCost, sheetForDate, readCostLedger,
  kstDateStr, addDaysStr,
} = require("./cost_to_bigquery");

const PROJECT = process.env.BQ_PROJECT || "rf-ads-db-500505";
const LOCATION = process.env.BQ_LOCATION || "asia-northeast3";
const SOURCE = "cafe24.rf_cafe24_order_items_current";
const TARGET = "mart.dtc_order_cost_daily_v2";
const LOOKBACK_DAYS = Number(process.env.LOOKBACK_DAYS || 3);
const ENGINE_VERSION = "order-cost-v2.1.0";
const COST_MIN_COVERAGE = Number(process.env.COST_MIN_COVERAGE || 0.95);
const SHIP_MIN_COVERAGE = Number(process.env.SHIP_MIN_COVERAGE || 0.95);

function csvCells(line) {
  const out = []; let cur = "", quoted = false;
  for (let i = 0; i < line.length; i++) {
    const ch = line[i];
    if (ch === '"' && line[i + 1] === '"') { cur += '"'; i++; }
    else if (ch === '"') quoted = !quoted;
    else if (ch === "," && !quoted) { out.push(cur); cur = ""; }
    else cur += ch;
  }
  out.push(cur); return out;
}

function loadRatecard(file = path.join(__dirname, "ship_ratecard.csv")) {
  const lines = fs.readFileSync(file, "utf8").trim().split(/\r?\n/);
  const head = csvCells(lines.shift());
  return lines.map(line => Object.fromEntries(head.map((h, i) => [h, csvCells(line)[i]])))
    .map(r => ({ effectiveDate: r.effective_date, productType: r.product_type,
      units: Number(r.unit_count), packType: r.pack_type, cost: Number(String(r.cost).replace(/,/g, "")) }))
    .filter(r => r.effectiveDate && r.productType && r.units > 0 && r.cost > 0);
}

function brandOf(name, option) {
  const s = `${name || ""} ${option || ""}`;
  if (/오프아워|피치릴렉서|라임브리즈|자몽피즈/.test(s)) return "오프아워";
  if (/원더팝|더팝/.test(s)) return "원더팝";
  if (/소요일|한끼|두유/.test(s)) return "소요일";
  if (/푸룬|프룬|화이바/.test(s)) return "화이바";
  if (/사사비|애사비(?:소다)?\s*사이다/.test(s)) return "사사비";
  if (/애사비(?:소다)?\s*딥|애사비딥/.test(s)) return "애사비딥";
  if (/자두/.test(s)) return "자사비";
  if (/콜라/.test(s)) return "콜사비";
  if (/스프린트|에너지/.test(s)) return "스프린트";
  if (/애사비/.test(s)) return "애사비즈";
  return "기타";
}

function priceBrand(brand) {
  if (["애사비즈", "자사비", "콜사비", "사사비", "애사비딥"].includes(brand)) return "애사비";
  if (brand === "푸룬화이바") return "화이바";
  return brand;
}

function dealFor(name, option, mall, date, units, grossPerSet, priceLog) {
  const brand = brandOf(name, option), pb = priceBrand(brand);
  const candidates = priceLog.filter(r => r.mall === mall && r.brand === pb && r.packCount === units
    && r.effectiveFrom <= date && (!r.effectiveTo || date <= r.effectiveTo));
  if (!candidates.length) return { deal: "오가닉", brand, exact: false, priceGap: null };
  const best = candidates.reduce((a, r) => Math.abs(r.salePrice - grossPerSet) < Math.abs(a.salePrice - grossPerSet) ? r : a);
  const gap = Math.abs(best.salePrice - grossPerSet);
  return { deal: best.deal, brand, exact: gap <= Math.max(1000, best.salePrice * 0.05), priceGap: gap };
}

function productType(productName) {
  const s = String(productName || "");
  if (/1\.5\s*l|1\.5\s*리터/i.test(s)) return "1.5L";
  if (/파우치|애사비즈|사사비|리사비|패사비|화이바/.test(s)) return /화이바/.test(s) ? "화이바" : "파우치";
  if (/페트|PET/i.test(s)) return "페트";
  return "캔";
}

function rateAsOf(card, date, type, units, mixed) {
  const pack = mixed ? "혼합" : "단품";
  const eligible = card.filter(r => r.effectiveDate <= date && r.productType === type && r.packType === pack);
  const latestByUnits = new Map();
  for (const r of eligible) {
    const prev = latestByUnits.get(r.units);
    if (!prev || r.effectiveDate > prev.effectiveDate) latestByUnits.set(r.units, r);
  }
  const rows = [...latestByUnits.values()].sort((a, b) => a.units - b.units);
  const exact = rows.find(r => r.units === units);
  if (exact) return { cost: exact.cost, exact: true };
  // 정가표에 정확한 박스가 없으면 큰 규격부터 결정론적으로 분할한다.
  let remain = units, cost = 0;
  for (const r of [...rows].sort((a, b) => b.units - a.units)) {
    const n = Math.floor(remain / r.units); cost += n * r.cost; remain -= n * r.units;
  }
  if (remain > 0 && rows.length) { const fit = rows.find(r => r.units >= remain) || rows[rows.length - 1]; cost += fit.cost; remain = 0; }
  return cost > 0 && remain === 0 ? { cost, exact: false } : null;
}

function computeDaily(rawRows, ledger, ratecard, priceLog = []) {
  const byDate = new Map();
  for (const r of rawRows) {
    const d = r.date && r.date.value ? String(r.date.value) : String(r.date || "").slice(0, 10);
    if (!byDate.has(d)) byDate.set(d, []); byDate.get(d).push(r);
  }
  const output = [];
  for (const [date, rows] of byDate) {
    const costs = parseCost(sheetForDate(ledger, date));
    const orders = new Map();
    for (const r of rows) {
      const key = `${r.mallId}|${r.orderId}`;
      if (!orders.has(key)) orders.set(key, { mall: String(r.mallId), id: String(r.orderId), lines: [] });
      orders.get(key).lines.push(r);
    }
    const acc = new Map();
    for (const order of orders.values()) {
      const a = acc.get(order.mall) || { report_date: date, mall: order.mall, net_revenue: 0, cogs: 0, logistics: 0,
        order_count: 0, item_line_count: 0, item_quantity: 0, cogs_matched_lines: 0,
        logistics_matched_orders: 0, exact_rate_orders: 0, unmatched_cogs_lines: 0,
        unmatched_logistics_orders: 0, deal_mapped_lines: 0, prior_order_month_orders: 0,
        prior_order_month_net_revenue: 0, same_order_month_net_revenue: 0,
        order_to_ship_lag_days_sum: 0, order_to_ship_lag_orders: 0,
        delivered_date_orders: 0, deals: new Map() };
      a.order_count++;
      const orderDate = String(order.lines[0].orderDate || "").slice(0, 10);
      const orderNet = order.lines.reduce((s, x) => s + Number(x.allocatedNet || 0), 0);
      if (orderDate) {
        const lag = Math.round((Date.parse(`${date}T00:00:00Z`) - Date.parse(`${orderDate}T00:00:00Z`)) / 86400000);
        if (Number.isFinite(lag)) { a.order_to_ship_lag_days_sum += lag; a.order_to_ship_lag_orders++; }
        if (orderDate.slice(0, 7) < date.slice(0, 7)) {
          a.prior_order_month_orders++; a.prior_order_month_net_revenue += orderNet;
        } else {
          a.same_order_month_net_revenue += orderNet;
        }
      }
      if (order.lines.some(x => x.deliveredDate)) a.delivered_date_orders++;
      let units = 0, orderCogs = 0, orderCostOk = true;
      const parsedLines = [];
      const types = new Set(), components = new Set();
      for (const r of order.lines) {
        const qty = Number(r.quantity || 0); a.item_line_count++; a.item_quantity += Math.max(qty, 0);
        const pn = String(r.productName || ""), on = String(r.optionName || "");
        a.net_revenue += Number(r.allocatedNet || 0);
        const parsed = ovBoxCost(pn, on, costs, order.mall === "sprint");
        const dm = parsed ? dealFor(pn, on, order.mall, date, parsed.cans,
          Number(r.allocatedNet || 0) * 1.1 / Math.max(qty, 1), priceLog)
          : { deal: "오가닉", brand: brandOf(pn, on), exact: false };
        if (dm.exact) a.deal_mapped_lines++;
        if (!parsed || !(parsed.boxCost > 0) || !(parsed.cans > 0) || !(qty > 0)) {
          a.unmatched_cogs_lines++; orderCostOk = false;
          parsedLines.push({ ...dm, cogs: 0, units: 0 }); continue;
        }
        a.cogs_matched_lines++; orderCogs += qty * parsed.boxCost; units += qty * parsed.cans;
        parsedLines.push({ ...dm, cogs: qty * parsed.boxCost, units: qty * parsed.cans });
        types.add(productType(pn)); components.add(`${pn}|${on}`);
      }
      a.cogs += orderCogs;
      const type = types.size === 1 ? [...types][0] : null;
      const mixed = components.size > 1 || order.lines.some(r => /\+|\/|혼합|골라/i.test(String(r.optionName || "")));
      const ship = type && units > 0 ? rateAsOf(ratecard, date, type, units, mixed) : null;
      if (ship) { a.logistics += ship.cost; a.logistics_matched_orders++; if (ship.exact) a.exact_rate_orders++; }
      else a.unmatched_logistics_orders++;
      for (const line of parsedLines) {
        const key = `${line.brand}|${line.deal}`;
        const d = a.deals.get(key) || { brand: line.brand, deal: line.deal, cogs: 0, logistics: 0, item_lines: 0, order_ids: new Set() };
        d.cogs += line.cogs; d.logistics += ship && units ? ship.cost * line.units / units : 0;
        d.item_lines++; d.order_ids.add(order.id); a.deals.set(key, d);
      }
      if (!orderCostOk) { /* 품질 컬럼이 이 주문의 부분 원가를 명시적으로 드러낸다. */ }
      acc.set(order.mall, a);
    }
    for (const a of acc.values()) {
      a.cost_coverage = a.item_line_count ? a.cogs_matched_lines / a.item_line_count : 0;
      a.shipping_coverage = a.order_count ? a.logistics_matched_orders / a.order_count : 0;
      a.deal_map_coverage = a.item_line_count ? a.deal_mapped_lines / a.item_line_count : 0;
      a.delivery_date_coverage = a.order_count ? a.delivered_date_orders / a.order_count : 0;
      a.avg_order_to_ship_days = a.order_to_ship_lag_orders ? a.order_to_ship_lag_days_sum / a.order_to_ship_lag_orders : null;
      a.is_trusted = a.cost_coverage >= COST_MIN_COVERAGE && a.shipping_coverage >= SHIP_MIN_COVERAGE;
      a.deal_breakdown_json = JSON.stringify([...a.deals.values()].map(d => ({ brand: d.brand, deal: d.deal,
        cogs: Math.round(d.cogs), logistics: Math.round(d.logistics), item_lines: d.item_lines,
        orders: d.order_ids.size })).sort((x, y) => y.cogs + y.logistics - x.cogs - x.logistics));
      delete a.deals;
      output.push(a);
    }
  }
  return output;
}

async function sourceRows(bq, start, end) {
  const sql = `WITH items AS (
    SELECT *, DATE(TIMESTAMP(JSON_VALUE(raw_json,'$.shipped_date')),'Asia/Seoul') ship_date,
      GREATEST((IFNULL(SAFE_CAST(JSON_VALUE(raw_json,'$.product_price') AS FLOAT64),product_price)
        + IFNULL(SAFE_CAST(JSON_VALUE(raw_json,'$.option_price') AS FLOAT64),0)
        - IFNULL(SAFE_CAST(JSON_VALUE(raw_json,'$.additional_discount_price') AS FLOAT64),0))*quantity,1) line_weight
    FROM \`${PROJECT}.${SOURCE}\`
    WHERE mall IN ('cloop','sprint')
      AND DATE(TIMESTAMP(JSON_VALUE(raw_json,'$.shipped_date')),'Asia/Seoul') BETWEEN '${start}' AND '${end}'
      AND IFNULL(JSON_VALUE(raw_json,'$.status_code'),'') NOT LIKE 'C%'
  ), orders AS (
    SELECT mall,order_id, DATE(ordered_at,'Asia/Seoul') order_date,
      SAFE_CAST(JSON_VALUE(raw_json,'$.actual_order_amount.order_price_amount') AS FLOAT64)/1.1 order_net
    FROM \`${PROJECT}.cafe24.rf_cafe24_orders_current\`
    WHERE mall IN ('cloop','sprint') AND IFNULL(JSON_VALUE(raw_json,'$.canceled'),'F') NOT IN ('T','M')
  )
  SELECT ship_date AS date, o.order_date AS orderDate,
    DATE(TIMESTAMP(JSON_VALUE(i.raw_json,'$.delivered_date')),'Asia/Seoul') AS deliveredDate,
    i.mall AS mallId, i.order_id AS orderId,
    JSON_VALUE(raw_json,'$.order_item_code') AS orderItemCode,
    product_name AS productName,
    COALESCE(JSON_VALUE(raw_json,'$.option_value'), JSON_VALUE(raw_json,'$.option_value_default')) AS optionName,
    quantity, o.order_net*SAFE_DIVIDE(line_weight,SUM(line_weight) OVER(PARTITION BY i.mall,i.order_id)) AS allocatedNet
    FROM items i JOIN orders o USING(mall,order_id)`;
  const [rows] = await bq.query({ query: sql, location: LOCATION }); return rows;
}

async function readPriceLog(bq) {
  const sql = `SELECT CAST(effective_from AS STRING) effectiveFrom,
    CAST(effective_to AS STRING) effectiveTo, mall, brand, deal, pack_count packCount,
    sale_price salePrice
    FROM \`${PROJECT}.ops_input.price_log\`
    WHERE sale_price > 0 AND pack_count > 0`;
  const [rows] = await bq.query({ query: sql, location: LOCATION });
  return rows.map(r => ({ ...r, packCount: Number(r.packCount), salePrice: Number(r.salePrice) }));
}

async function ensureAndLoad(bq, start, end, rows) {
  const target = `\`${PROJECT}.${TARGET}\``;
  const ddl = `CREATE TABLE IF NOT EXISTS ${target} (
    report_date DATE NOT NULL, mall STRING NOT NULL, recognized_net_revenue INT64,
    estimated_cogs INT64, estimated_logistics INT64,
    order_count INT64, item_line_count INT64, item_quantity INT64, cogs_matched_lines INT64,
    logistics_matched_orders INT64, exact_rate_orders INT64, unmatched_cogs_lines INT64,
    unmatched_logistics_orders INT64, cost_coverage FLOAT64, shipping_coverage FLOAT64,
    deal_mapped_lines INT64, deal_map_coverage FLOAT64, deal_breakdown_json STRING,
    prior_order_month_orders INT64, prior_order_month_net_revenue INT64,
    same_order_month_net_revenue INT64, avg_order_to_ship_days FLOAT64,
    delivered_date_orders INT64, delivery_date_coverage FLOAT64,
    is_trusted BOOL, value_type STRING, recognition_basis STRING, is_accounting_actual BOOL,
    engine_version STRING, calculated_at TIMESTAMP
  ) PARTITION BY report_date CLUSTER BY mall
  OPTIONS(description='주문상품 구성과 유효일 원가·물류 요율로 계산한 일별 추정. 회계 실청구 아님; 품질 컬럼 필수 사용.');`;
  await bq.query({ query: ddl, location: LOCATION });
  await bq.query({ query: `ALTER TABLE ${target}
    ADD COLUMN IF NOT EXISTS prior_order_month_orders INT64,
    ADD COLUMN IF NOT EXISTS prior_order_month_net_revenue INT64,
    ADD COLUMN IF NOT EXISTS same_order_month_net_revenue INT64,
    ADD COLUMN IF NOT EXISTS avg_order_to_ship_days FLOAT64,
    ADD COLUMN IF NOT EXISTS delivered_date_orders INT64,
    ADD COLUMN IF NOT EXISTS delivery_date_coverage FLOAT64`, location: LOCATION });
  const q = s => `'${String(s).replace(/'/g, "''")}'`;
  const values = rows.map(r => `('${r.report_date}','${r.mall}',${Math.round(r.net_revenue)},${Math.round(r.cogs)},${Math.round(r.logistics)},${r.order_count},${r.item_line_count},${r.item_quantity},${r.cogs_matched_lines},${r.logistics_matched_orders},${r.exact_rate_orders},${r.unmatched_cogs_lines},${r.unmatched_logistics_orders},${r.cost_coverage},${r.shipping_coverage},${r.deal_mapped_lines},${r.deal_map_coverage},${q(r.deal_breakdown_json)},${r.prior_order_month_orders},${Math.round(r.prior_order_month_net_revenue)},${Math.round(r.same_order_month_net_revenue)},${r.avg_order_to_ship_days == null ? 'NULL' : r.avg_order_to_ship_days},${r.delivered_date_orders},${r.delivery_date_coverage},${r.is_trusted},'order_composition_estimate','shipped_date_proxy',FALSE,'${ENGINE_VERSION}',CURRENT_TIMESTAMP())`).join(",\n");
  const dml = `BEGIN TRANSACTION;
    DELETE FROM ${target} WHERE report_date BETWEEN '${start}' AND '${end}';
    ${values ? `INSERT INTO ${target} (report_date,mall,recognized_net_revenue,estimated_cogs,estimated_logistics,order_count,item_line_count,item_quantity,cogs_matched_lines,logistics_matched_orders,exact_rate_orders,unmatched_cogs_lines,unmatched_logistics_orders,cost_coverage,shipping_coverage,deal_mapped_lines,deal_map_coverage,deal_breakdown_json,prior_order_month_orders,prior_order_month_net_revenue,same_order_month_net_revenue,avg_order_to_ship_days,delivered_date_orders,delivery_date_coverage,is_trusted,value_type,recognition_basis,is_accounting_actual,engine_version,calculated_at) VALUES ${values};` : ""}
    COMMIT TRANSACTION;`;
  await bq.query({ query: dml, location: LOCATION });
}

async function main() {
  const args = process.argv.slice(2), bi = args.indexOf("--backfill");
  const span = bi >= 0 ? Number(args[bi + 1]) : LOOKBACK_DAYS;
  if (!Number.isInteger(span) || span < 1 || span > 400) throw new Error("backfill_days must be 1..400");
  const end = addDaysStr(kstDateStr(new Date()), -1), start = addDaysStr(end, -(span - 1));
  const bq = new BigQuery({ projectId: PROJECT, location: LOCATION });
  const ledger = await readCostLedger(bq); ovGroups(parseCost(sheetForDate(ledger, null)));
  const rows = computeDaily(await sourceRows(bq, start, end), ledger, loadRatecard(), await readPriceLog(bq));
  if (!rows.length) throw new Error(`no rows for ${start}..${end}`);
  for (const r of rows) console.log(`[cost-v2] ${r.report_date} ${r.mall} net=${Math.round(r.net_revenue)} cogs=${Math.round(r.cogs)} logistics=${Math.round(r.logistics)} cost_coverage=${r.cost_coverage.toFixed(4)} shipping_coverage=${r.shipping_coverage.toFixed(4)} deal_coverage=${r.deal_map_coverage.toFixed(4)} trusted=${r.is_trusted}`);
  if (args.includes("--dry-run")) return;
  await ensureAndLoad(bq, start, end, rows);
  console.log(`[cost-v2] ${TARGET} ${start}~${end} ${rows.length}행 적재 · trusted ${rows.filter(r => r.is_trusted).length}/${rows.length}`);
}

if (require.main === module) main().catch(e => { console.error("[cost-v2] 실패:", e && e.stack || e); process.exit(1); });
module.exports = { csvCells, loadRatecard, brandOf, priceBrand, dealFor, productType, rateAsOf, computeDaily };
