import ast
import copy
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock
from meta_daily_quality import aggregate, validate, before_load, after_load

DAY='2026-10-20'
ACCT='1462607070849777'

def row(spend=19689044, impressions=1407007, **kw):
    return dict(report_date=DAY,account_id=ACCT,ad_id='ad-1',spend=spend,impressions=impressions,actions='[{"action_type":"purchase","value":"10"}]',**kw)

def ref(**kw):
    d=dict(source='hourly',report_date=DAY,account_id=ACCT,n_rows=24,unique_rows=24,spend=19689044,impressions=1407007,action_rows=None);d.update(kw);return d

class QualityTests(unittest.TestCase):
    def check(self, rows, refs=None, **kw):
        return validate(aggregate(rows), refs if refs is not None else [ref()], [ACCT],DAY,DAY,**kw)
    def test_real_incident_spend_rejected(self):
        with self.assertRaisesRegex(RuntimeError,'spend=1283'): self.check([row(1283,97)])
    def test_impressions_independently_rejected(self):
        with self.assertRaisesRegex(RuntimeError,'impressions=97'): self.check([row(impressions=97)])
    def test_normal_delivery_accepted(self): self.assertEqual(self.check([row()]),2)
    def test_exact_fifty_percent_allowed(self): self.check([row(100,100)],[ref(spend=200,impressions=200)])
    def test_below_fifty_percent_rejected(self):
        with self.assertRaises(RuntimeError): self.check([row(99.99,100)],[ref(spend=200,impressions=200)])
    def test_missing_account_day_rejected(self):
        with self.assertRaises(RuntimeError): self.check([])
    def test_account_removed_from_config_rejected(self):
        with self.assertRaisesRegex(RuntimeError,'omitted'): self.check([row()],[ref(account_id='other')])
    def test_positive_previous_daily_used_without_hourly(self):
        with self.assertRaises(RuntimeError): self.check([row(1,1)],[ref(source='daily')])
    def test_purchase_actions_disappearing_rejected(self):
        r=row();r['actions']=None
        with self.assertRaisesRegex(RuntimeError,'actions disappeared'): self.check([r],[ref(source='daily',action_rows=5)])
    def test_hourly_duplicates_block_reference(self):
        with self.assertRaisesRegex(RuntimeError,'hourly duplicate'): self.check([row()],[ref(unique_rows=23)])
    def test_ad_duplicates_block_candidate(self):
        with self.assertRaisesRegex(RuntimeError,'duplicate ad'): aggregate([row(),row()])
    def test_nonfinite_negative_null_rejected(self):
        for v in [float('nan'),float('inf'),-1,None]:
            with self.subTest(v=v), self.assertRaises((RuntimeError,TypeError)): aggregate([row(v)])
    def test_bad_floor_rejected(self):
        for v in [0,-1,1.01,float('nan')]:
            with self.subTest(v=v), self.assertRaises(ValueError): self.check([row()],floor=v)
    def test_out_of_range_rejected(self):
        r=row();r['report_date']='2026-10-19'
        with self.assertRaises(RuntimeError):self.check([r])
    def test_reference_query_error_fails_closed(self):
        client=Mock();client.query.side_effect=RuntimeError('permission denied')
        with self.assertRaises(RuntimeError):before_load(client,'proj','meta_ads',[row()],[ACCT],DAY,DAY)
    def test_after_load_success(self):
        client=Mock();v=aggregate([row()]);client.query.return_value.result.return_value=[dict(report_date=DAY,account_id=ACCT,**v[(DAY,ACCT)])]
        after_load(client,'proj.meta_ads.rf_meta_ads',dict(totals=v,since=DAY,until=DAY))
    def test_after_load_mismatch_fails(self):
        client=Mock();v=aggregate([row()]);client.query.return_value.result.return_value=[dict(report_date=DAY,account_id=ACCT,**{**v[(DAY,ACCT)],'spend':1283})]
        with self.assertRaises(RuntimeError):after_load(client,'proj.meta_ads.rf_meta_ads',dict(totals=v,since=DAY,until=DAY))
    def test_actual_loader_calls_gate_before_any_writes(self):
        tree=ast.parse((Path(__file__).resolve().parents[1]/'meta_to_bigquery.py').read_text())
        for name in ['load_by_partition','load_replace_table']:
            fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name==name)
            gate=Mock(side_effect=RuntimeError('quality failure'));client=Mock()
            ns={'_quality_before':gate}
            exec(compile(ast.Module(body=[fn],type_ignores=[]),'actual-loader','exec'),ns)
            with self.subTest(loader=name), self.assertRaisesRegex(RuntimeError,'quality failure'):ns[name](client,'proj.meta_ads.rf_meta_ads',[row()],DAY,DAY)
            client.load_table_from_json.assert_not_called()
            gate.assert_called_once()

if __name__=='__main__':unittest.main()
