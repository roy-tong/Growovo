"""Offline tests for discovery → generation → independent review → stage → tick."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from core.grow_agent import produce, AgentError, CodexWriter, select_candidate
from core.grow_v2 import GrowRunV2
from core.execution_policy import authorize
from core.grow_lock import run_lock
from tests.test_grow_v2 import _facts, _now, _reader, _runner

BODY = 'We reconcile invoice total, usage export and contract price, then park the gap. Which source do you trust most?'

class ProductionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        authorize(self.root, by='test', actions=['x:reply','x:publish','email:message'], days=30)
        self.run = GrowRunV2(self.root/'grow'/'g.json')
        GrowRunV2.launch(self.run.path, run_id='g', facts_path=str(_facts(self.root)),
            goal_id='awareness.followers', channels=['x'], actions_per_week=7,
            started='2026-11-01', data_dir=self.root, skip_probe=True)
        self.calls = []

    def writer(self, request):
        self.calls.append(request)
        return {'body': BODY, 'subject': '', 'approved': True, 'reason': ''}

    def discover(self):
        return {'prospects':[{'tier':'A', 'handle':'peer', 'is_koc':True,
          'account':{'followers':8000}, 'best_post':{'url':'https://x.com/peer/status/1',
          'text':'Our AI invoice does not match usage. How do you reconcile token cost per team?'}}]}

    def test_select_candidate_respects_koc_and_touch_state(self):
        rows = [
            {"tier": "A", "handle": "done", "is_koc": True,
             "best_post": {"url": "https://x.com/done/status/1"}},
            {"tier": "A", "handle": "nonkoc", "is_koc": False,
             "best_post": {"url": "https://x.com/nonkoc/status/1"}},
            {"tier": "B", "handle": "koc", "is_koc": True,
             "best_post": {"url": "https://x.com/koc/status/1"}},
        ]
        picked = select_candidate(rows, excluded_handles=["done"])
        self.assertEqual("nonkoc", picked["handle"])
        picked = select_candidate(rows, excluded_handles=["done"], require_koc=True)
        self.assertEqual("koc", picked["handle"])
        self.assertIsNone(select_candidate(rows, excluded_targets=[
            "https://x.com/nonkoc/status/1", "https://x.com/koc/status/1"],
            excluded_handles=["done"]))

    def test_production_and_daily_quota_no_duplicate(self):
        result = self.run.produce(self.root,on='2026-11-01',writer=self.writer, discover=self.discover)
        self.assertEqual(1, result['added'], result)
        self.assertEqual(1, result['koc_added'], result)
        self.assertEqual(['qualify','draft','review'],[r['phase'] for r in self.calls])
        again = self.run.produce(self.root,on='2026-11-01',writer=self.writer, discover=self.discover)
        self.assertEqual(0, again['added'])
        self.assertEqual(3,len(self.calls))

    def test_production_covers_min_cover_days_ahead(self):
        data = self.run.load()
        data.setdefault("supply", {})["min_cover_days"] = 1
        self.run.save(data)
        pool = [{'tier': 'A', 'handle': f'peer{n}', 'is_koc': True,
                 'account': {'followers': 8000},
                 'best_post': {'url': f'https://x.com/peer{n}/status/{n}',
                               'text': 'Our AI invoice does not match usage. How do you reconcile token cost per team?'}}
                for n in (1, 2)]
        seq = {"n": 0}
        def writer(request):  # 每条正文必须互不相同，否则被去重门拦下（引擎正确行为）
            seq["n"] += 1
            return {'body': BODY + f' (case {seq["n"]})', 'subject': '',
                    'approved': True, 'reason': ''}
        result = self.run.produce(self.root, on='2026-11-01', writer=writer,
                                  discover=lambda: {'prospects': pool})
        self.assertEqual(2, result['added'], result)  # 当天 1 条 + 提前 1 天 1 条
        days = set()
        for path in (self.root / 'queues').glob('g-*.json'):
            for item in json.loads(path.read_text(encoding='utf-8'))['items']:
                days.add(item['scheduled_for'])
        self.assertEqual({'2026-11-01', '2026-11-02'}, days)

    def test_tick_produces_then_executes_same_cycle(self):
        data=self.run.load(); data['automation']={'enabled':True}; self.run.save(data)
        self.run.arm()
        result=self.run.tick(self.root,on='2026-11-01',runner=_runner(self.root,'2026-11-01'),
             reader=_reader('2026-11-01'),now=_now('2026-11-01'),
             content_writer=self.writer,discover=self.discover)
        self.assertEqual(1,result['production']['added'],result)
        self.assertEqual(1,result['daily']['published']['total'])

    def test_independent_review_rejects_and_backoff(self):
        def reject(request):
            return {'body': BODY,'approved':False,'reason':'unsourced claim'}
        result=self.run.produce(self.root,on='2026-11-01',writer=reject,discover=self.discover)
        self.assertEqual(0,result['added']); self.assertTrue(result['rejected'])
        self.assertEqual('retry_backoff',self.run.produce(self.root,on='2026-11-01',writer=self.writer)['skipped'])
        self.assertFalse(list((self.root/'queues').glob('*.json')))

    def test_model_cannot_override_target(self):
        def writer(request):
            return dict(self.writer(request),target='https://x.com/attacker/status/9')
        self.run.produce(self.root,on='2026-11-01',writer=writer,discover=self.discover)
        item=json.loads(next((self.root/'queues').glob('*.json')).read_text())['items'][0]
        self.assertEqual('https://x.com/peer/status/1',item['target'])

    def test_dry_tick_never_calls_model(self):
        data=self.run.load(); data['automation']={'enabled':True}; self.run.save(data)
        with patch('core.grow_agent.CodexWriter.__call__',side_effect=AssertionError('network')):
            self.run.tick(self.root,on='2026-11-01',reader=_reader('2026-11-01'),
                          runner=lambda *a,**k:[],now=_now('2026-11-01'))

    def test_multichannel_same_day_and_global_target_dedupe(self):
        data=self.run.load();data['channels'].append('email');data['actions'].append('email:message');self.run.save(data)
        self.run.stage(self.root,[{'target':'tweet:https://x.com/a/status/1','body':BODY}],scheduled_for='2026-11-01')
        result=self.run.stage(self.root,[{'target':'https://x.com/a/status/1','body':BODY}],scheduled_for='2026-11-02')
        self.assertEqual(0,result['added'])
        result=self.run.stage(self.root,[{'target':'person@example.com','body':BODY,'subject':'Invoice question'}],
                             channel='email',action='message',scheduled_for='2026-11-01')
        queue=json.loads(Path(result['path']).read_text())
        self.assertEqual('Invoice question',queue['items'][0]['subject'])
        self.assertEqual('email',queue['channel'])

    def test_original_post_metrics_use_resolved_url(self):
        from core.ledger import Ledger
        data=self.run.load();data["post_urls"]={"grow-post:g:one":"https://x.com/me/status/2"};self.run.save(data)
        Ledger(self.root/"ledger.jsonl").append(channel="x",action="publish",target="grow-post:g:one",
            result="committed",campaign="g-20261101",ts="2026-11-01T10:00:00")
        calls=[]
        def read(url): calls.append(url);return {"views":100,"replies":2}
        result=self.run.metrics(self.root,on="2026-11-01",reader=read)
        self.assertEqual(["https://x.com/me/status/2"],calls)
        self.assertEqual(1,result["written"])

    def test_goal_expiry_never_sends(self):
        data=self.run.load();data['goal']['ends']='2026-11-02';self.run.save(data)
        result=self.run.tick(self.root,on='2026-11-03',runner=lambda *a,**k: self.fail('sent'))
        self.assertEqual('goal_window_ended',result['skipped'])
        self.assertEqual('completed',self.run.load()['status'])

    def test_low_fit_candidate_is_not_used(self):
        def discover():
            r=self.discover();r['prospects'][0]['tier']='C';return r
        result=self.run.produce(self.root,on='2026-11-01',writer=self.writer,discover=discover)
        self.assertEqual(0,result['added']);self.assertEqual([],self.calls)

    def test_budget_reserved_on_model_failure(self):
        def fail(_): raise AgentError('offline')
        self.run.produce(self.root,on='2026-11-01',writer=fail,discover=self.discover)
        self.assertEqual(1,self.run.load()['automation']['days']['2026-11-01']['attempts'])

    def test_missing_contacts_does_not_invent_recipients(self):
        data=self.run.load();data['supply']['briefs']=[{'channel':'email','action':'message'}]
        data['quota']['action_weights']={'message':1};self.run.save(data)
        result=self.run.produce(self.root,on='2026-11-01',writer=self.writer)
        self.assertEqual(0,result['added']);self.assertIn('邮箱',result['rejected'][0])

class RuntimeTests(unittest.TestCase):
    def test_process_lock_refuses_competing_process(self):
        import subprocess, sys
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'run.json'
            with run_lock(path):
                proc=subprocess.run([sys.executable,'-c',
                    'from core.grow_lock import run_lock; import sys\nwith run_lock(sys.argv[1]): pass',str(path)],capture_output=True)
                self.assertNotEqual(0,proc.returncode)
            with run_lock(path): pass

    def test_cli_timeout_and_invalid_output_fail_closed(self):
        import subprocess
        with patch('core.grow_agent.executable',return_value='/fake/codex'), patch(
                'core.grow_agent.subprocess.run',side_effect=subprocess.TimeoutExpired('codex',1)):
            with self.assertRaises(AgentError): CodexWriter()({'phase':'draft'})

    def test_vendor_uses_existing_tab(self):
        from channels.x.vendor.session_page import automation_page, release_page
        from unittest.mock import Mock
        context=Mock(); page=Mock();page.url='https://x.com/home';context.pages=[page]
        self.assertIs(page,automation_page(context)); release_page(page)
        context.new_page.assert_not_called();page.close.assert_not_called()

    def test_publish_ledger_uses_logical_target(self):
        from channels.x import adapter
        from core.ledger import Ledger
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); result=root/'publish-test.json'
            result.write_text(json.dumps({'records':[{'index':1,'posted':True}]}))
            ledger=Ledger(root/'ledger.jsonl')
            with patch.object(adapter,'_latest_result',return_value=result):
                adapter._log_to_ledger('publish','g',ledger,{},logical_target='grow-post:g:one')
            self.assertTrue(ledger.already(channel='x',target='grow-post:g:one'))
            self.assertEqual(1,len(ledger.all()))

    def test_publication_blocks_known_unknown_before_vendor(self):
        from channels.x import adapter
        from core.ledger import Ledger
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); payload=root/'post.json';payload.write_text('{"tweets":["Example"]}')
            ledger=Ledger(root/'ledger.jsonl');ledger.append(channel='x',action='publish',target='post:one',result='indeterminate')
            with patch('core.planlint.enforce_approval',return_value='plan'), patch.object(adapter.subprocess,'run') as external:
                with self.assertRaises(SystemExit):
                    adapter.run('publish',['--file',str(payload)],go=True,campaign='g',ledger=ledger,
                                data_dir=root,logical_target='post:one')
                external.assert_not_called()

class QualificationTests(unittest.TestCase):
    setUp = ProductionTests.setUp
    discover = ProductionTests.discover

    def test_semantic_rejection_never_reaches_drafting(self):
        calls=[]
        def writer(request):
            calls.append(request['phase'])
            return {'approved':False,'body':'','reason':'Political budget, not AI invoices'}
        result=self.run.produce(self.root,on='2026-11-01',writer=writer,discover=self.discover)
        self.assertEqual(['qualify'],calls)
        self.assertEqual(0,result['added'])
