import copy
import json
import subprocess
import sys
import unittest
from core import editorial_review as er
from core import content_ops

TEXT = '# 标题\n\n买过不等于持续使用。\n\n## 判断\n\n去看同一用户的后续记录。'

def completed():
    r = er.packet('blog', TEXT)
    for c in r['checks']:
        c.update(verdict='pass', quote='买过不等于持续使用。', reason='区分了购买表达与持续行为。')
    for s in r['outline']: s['role'] = '推进读者判断，提出验证动作。'
    r['claim_coverage_note'] = '本文为方法判断，没有市场数值；关键概念按上下文整读。'
    return r

class EditorialReviewTests(unittest.TestCase):
    def test_empty_source_rejected(self):
        with self.assertRaises(ValueError): er.packet('blog', '')

    def test_blank_packet_is_not_review(self):
        self.assertFalse(er.validate('blog', TEXT, er.packet('blog', TEXT))['ok'])
    def test_complete_record_is_not_truth_or_publish_permission(self):
        res = er.validate('blog', TEXT, completed())
        self.assertTrue(res['ok'], res)
        self.assertFalse(res['semantic_truth_verified'])
        self.assertFalse(res['publish_authorized'])
    def test_new_draft_invalidates_previous_review(self):
        codes = {e['code'] for e in er.validate('blog', TEXT+'新增结论', completed())['errors']}
        self.assertIn('stale_review', codes)
    def test_fabricated_quote_and_missing_section(self):
        r=completed();r['checks'][0]['quote']='不在正文';r['outline'].pop()
        codes={e['code'] for e in er.validate('blog',TEXT,r)['errors']}
        self.assertTrue({'untraceable_quote','outline_coverage'} <= codes)
    def test_cross_population_ratio_rejected(self):
        r=completed();r['claims']=[{'quote':'买过不等于持续使用。','kind':'conversion',
            'source':'研究表','inference':'不同评论相除','linked_cohort':False,
            'numerator_population':'购买评论','denominator_population':'价格评论'}]
        self.assertIn('cross_cohort_conversion', [e['code'] for e in er.validate('blog',TEXT,r)['errors']])
    def test_uncomparable_trend_and_causal_leap(self):
        r=completed();r['claims']=[{'quote':'买过不等于持续使用。','kind':k,'source':'表','inference':'解释'} for k in ['trend','causal']]
        codes={e['code'] for e in er.validate('blog',TEXT,r)['errors']}
        self.assertTrue({'incomparable_trend','unsupported_causality'} <= codes)
    def test_revision_cannot_be_marked_done(self):
        r=completed();r['checks'][0]['verdict']='revise';r['checks'][0]['revision']='重写开头'
        self.assertFalse(er.validate('blog',TEXT,r)['ok'])
    def test_cli_and_mcp_share_packet(self):
        cli=subprocess.run([sys.executable,'mkt.py','content','review','blog','--text',TEXT],capture_output=True,text=True)
        self.assertEqual(cli.returncode,0,cli.stderr)
        import mcp_server
        result=mcp_server.handle({'jsonrpc':'2.0','id':1,'method':'tools/call','params':{
            'name':'mkt_content','arguments':{'action':'review','channel':'blog','text':TEXT}}})
        mcp=json.loads(result['result']['content'][0]['text'])
        self.assertEqual(json.loads(cli.stdout),mcp)
    def test_structural_audit_does_not_claim_editorial_pass(self):
        r=content_ops.audit_text('blog',TEXT)
        self.assertEqual(r['editorial_review'],'pending')
        self.assertFalse(r['publish_ready'])
    def test_plan_and_rules_share_length(self):
        for r in content_ops.plan()['rows']:
            lohi=content_ops.CHANNELS[r['channel']]['length']
            self.assertEqual(r['length'],f"{lohi['min']}—{lohi['max']} 字")
