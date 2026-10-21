"""Delivery completeness gate; never use hourly as a purchase substitute."""
import math
import re
from datetime import date


def aggregate(rows):
    totals, seen = {}, set()
    for r in rows:
        day, acct, ad = str(r['report_date']), str(r['account_id']), str(r.get('ad_id') or '')
        date.fromisoformat(day)
        key = (day, acct, ad)
        if not ad or key in seen:
            raise RuntimeError(f'Meta quality: missing/duplicate ad key {key}')
        seen.add(key)
        t = totals.setdefault((day, acct), dict(n_rows=0, spend=0., impressions=0., action_rows=0))
        t['n_rows'] += 1
        for metric in ('spend', 'impressions'):
            value = float(r[metric])
            if not math.isfinite(value) or value < 0:
                raise RuntimeError(f'Meta quality: invalid {metric} for {key}')
            t[metric] += value
        t['action_rows'] += r.get('actions') not in (None, '', 'null', '[]')
    return totals


def validate(totals, references, accounts, since, until, floor=.5):
    if not math.isfinite(floor) or not 0 < floor <= 1:
        raise ValueError('META_MIN_DAILY_REFERENCE_RATIO must be >0 and <=1')
    accounts = {str(a).strip() for a in accounts if str(a).strip()}
    for day, acct in totals:
        if not since <= day <= until or acct not in accounts:
            raise RuntimeError(f'Meta quality: unexpected date/account {(day, acct)}')
    checks = 0
    for ref in references:
        day, acct = str(ref['report_date']), str(ref['account_id'])
        if acct not in accounts:
            if any(float(ref.get(m) or 0) > 0 for m in ('spend', 'impressions')):
                raise RuntimeError(f'Meta quality: active account omitted {acct}')
            continue
        if ref['source'] == 'hourly' and ref['n_rows'] != ref['unique_rows']:
            raise RuntimeError(f'Meta quality: hourly duplicate keys {(day, acct)}')
        candidate = totals.get((day, acct), {})
        for metric in ('spend', 'impressions'):
            baseline = float(ref.get(metric) or 0)
            if baseline > 0:
                checks += 1
                value = candidate.get(metric, 0)
                if value < baseline * floor:
                    raise RuntimeError(f'Meta quality: {day}/{acct} {metric}={value:g} < {floor:.0%} of {ref["source"]}={baseline:g}; no load performed')
        if int(ref.get('action_rows') or 0) > 0 and not candidate.get('action_rows'):
            raise RuntimeError(f'Meta quality: actions disappeared {(day, acct)}')
    return checks


def before_load(client, project, dataset, rows, accounts, since, until, floor=.5):
    since, until = date.fromisoformat(str(since)).isoformat(), date.fromisoformat(str(until)).isoformat()
    if since > until or not re.fullmatch(r'[A-Za-z0-9_-]+', project) or not re.fullmatch(r'[A-Za-z0-9_]+', dataset):
        raise ValueError('Invalid quality reference/range')
    sql = f"""SELECT 'daily' source, report_date, account_id, COUNT(*) n_rows,
      COUNT(DISTINCT ad_id) unique_rows, SUM(spend) spend, SUM(impressions) impressions,
      COUNTIF(actions IS NOT NULL AND actions NOT IN ('','null','[]')) action_rows
      FROM `{project}.{dataset}.rf_meta_ads`
      WHERE report_date BETWEEN DATE '{since}' AND DATE '{until}' GROUP BY 2,3
      UNION ALL SELECT 'hourly', report_date, account_id, COUNT(*),
      COUNT(DISTINCT CONCAT(ad_id,'|',CAST(hour AS STRING))), SUM(spend), SUM(impressions), CAST(NULL AS INT64)
      FROM `{project}.{dataset}.rf_meta_ads_hourly`
      WHERE report_date BETWEEN DATE '{since}' AND DATE '{until}' GROUP BY 2,3"""
    refs = [dict(r) for r in client.query(sql).result()]
    totals = aggregate(rows)
    checks = validate(totals, refs, accounts, since, until, floor)
    return dict(totals=totals, references=refs, checks=checks, since=since, until=until)


def after_load(client, table_id, proof):
    if not re.fullmatch(r'[A-Za-z0-9_-]+\.[A-Za-z0-9_]+\.[A-Za-z0-9_]+', table_id):
        raise ValueError('Invalid quality target')
    sql = f"""SELECT report_date,account_id,COUNT(*) n_rows,SUM(spend) spend,
      SUM(impressions) impressions,
      COUNTIF(actions IS NOT NULL AND actions NOT IN ('','null','[]')) action_rows
      FROM `{table_id}` WHERE report_date BETWEEN DATE '{proof['since']}' AND DATE '{proof['until']}' GROUP BY 1,2"""
    actual = {(str(r['report_date']),str(r['account_id'])): dict(r) for r in client.query(sql).result()}
    if set(actual) != set(proof['totals']):
        raise RuntimeError('Meta quality: persisted date/account coverage differs')
    for key, expected in proof['totals'].items():
        for metric in ('n_rows','spend','impressions','action_rows'):
            if not math.isclose(float(actual[key].get(metric) or 0), expected[metric], rel_tol=1e-10, abs_tol=1e-5):
                raise RuntimeError(f'Meta quality: persisted {key}/{metric} differs')
