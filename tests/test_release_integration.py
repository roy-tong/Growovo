"""Cross-module regressions found during the 0.14 release review; offline."""
import json
import plistlib
import tempfile
import unittest
from pathlib import Path

from core import grow_fit
from core.targeting import TargetList, TargetingError, score_candidate
from tests import test_grow_agent
from tests.test_grow_v2 import _queue


class TargetEvidenceTests(unittest.TestCase):
    def test_unknown_sponsorship_is_not_evidence_of_zero(self):
        result = score_candidate({'followers': 8000})
        self.assertIn('sponsored_recent', result['evidence_missing'])
        self.assertFalse(any('无商单' in r for r in result['reasons']))

    def test_target_history_is_not_overwritten_and_handles_are_case_insensitive(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'dream.json'
            TargetList.start(path, 'dream')
            targets = TargetList(path)
            targets.add('X', '@Alice', 8000)
            with self.assertRaises(TargetingError):
                targets.add('x', 'alice', 9000)
            with self.assertRaises(TargetingError):
                TargetList.start(path, 'dream')
            with self.assertRaises(TargetingError):
                targets.advance('x:ALICE', 'contacted')
            targets.advance('x:@ALICE', 'contacted', note='sent receipt')
            self.assertEqual('contacted', targets.load()['targets'][0]['stage'])
            self.assertEqual([], targets.next_batch(0))

    def test_weekly_metrics_use_the_right_event_window_and_koc_population(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'dream.json'
            TargetList.start(path, 'dream')
            targets = TargetList(path)
            for handle, followers, engaged in [('old', 8000, '2026-11-01'),
                    ('future', 8000, '2026-11-20'), ('mega', 900000, '2026-11-14'),
                    ('current', 8000, '2026-11-14')]:
                targets.add('x', handle, followers, on='2026-11-01')
                targets.advance(handle, 'contacted', on='2026-11-01', note='sent receipt')
                targets.advance(handle, 'engaged', on=engaged, note='reply receipt')
            targets.advance('old', 'amplified', on='2026-11-14', note='repost receipt')
            result = targets.summary('2026-11-14')
            self.assertEqual(2, result['engaged_last_7d'])
            self.assertEqual(1, result['koc_engaged_last_7d'])
            self.assertEqual(1, result['koc_amplified_total'])

    def test_trust_scoring_reaches_automatic_prospect_ranking(self):
        profile = {'keywords': ['invoice', 'reconcile'], 'audience': ['finops']}
        accounts = {h: {'followers': 8000, 'bio': 'FinOps invoice reconciliation',
                       'sponsored_recent': sponsors} for h, sponsors in [('promoter', 5), ('peer', 0)]}
        posts = [{'handle': h, 'url': f'https://x.com/{h}/status/1',
                  'text': 'How do you reconcile invoice cost?', 'age_hours': 2,
                  'engagement': {'likes': 10, 'replies': 2}} for h in accounts]
        ranked = grow_fit.rank_prospects(posts, profile, accounts=accounts)
        self.assertEqual('peer', ranked[0]['handle'])
        self.assertGreater(ranked[0]['trust']['score'], ranked[1]['trust']['score'])


class ProductionIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_grow_agent.ProductionTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def test_stale_inventory_does_not_block_replenishment(self):
        f = self.fixture
        _queue(f.root, 'g', '2026-10-20')
        result = f.run.produce(f.root, on='2026-11-01', writer=f.writer, discover=f.discover)
        self.assertEqual(1, result['added'], result)

    def test_scheduler_keeps_explicit_workspace(self):
        f = self.fixture
        result = f.run.install(f.root, load=False)
        payload = plistlib.loads(result['plist'].encode())
        self.assertEqual(str(f.root.resolve()), payload['EnvironmentVariables']['MKT_HOME'])
        self.assertIn('MKT_HOME=', result['command'])
        self.assertIn(str(f.root.resolve()), result['command'])
        script = Path(result['fallback']).read_text()
        self.assertIn('export MKT_HOME=', script)
        self.assertIn(str(f.root.resolve()), script)
        self.assertTrue(Path(f.run.load()['facts_ref']).is_absolute())


class NightIntegrationTests(unittest.TestCase):
    def test_login_needs_positive_evidence_not_just_absence_of_redirect(self):
        from core import session_gate
        from unittest.mock import Mock
        page = Mock(url='https://x.com/home')
        page.locator.return_value.first.wait_for.side_effect = TimeoutError('no account UI')
        session_gate.clear_cache()
        self.addCleanup(session_gate.clear_cache)
        state = session_gate.check_login('x', connect=lambda: (None, page))
        self.assertEqual('unknown', state['status'])

    def test_native_cdp_connection_preserves_browser_defaults(self):
        from channels.x.vendor.session_page import connect_browser
        calls = []
        class BrowserType:
            def connect_over_cdp(self, endpoint, *, no_defaults=False):
                calls.append((endpoint, no_defaults))
                return 'browser'
        self.assertEqual('browser', connect_browser(BrowserType(), 'http://localhost:9222'))
        self.assertEqual([('http://localhost:9222', True)], calls)

    def test_explicit_tactic_channels_override_other_owned_platforms(self):
        from core.tactics import recommend, GENERIC_CHANNELS
        facts = {'owned_assets': {'platforms': ['x', 'email', 'reddit']}}
        picks = recommend(facts, channels=['email'], today='2026-11-14')['picks']
        self.assertTrue(picks)
        self.assertTrue(all(p['channel'] in GENERIC_CHANNELS or p['channel'] == 'email' for p in picks))

    def test_watch_ignores_scripts_and_failed_snapshots_and_isolates_lists(self):
        from core.watch import WatchList, WatchError
        with tempfile.TemporaryDirectory() as tmp:
            first, second = [WatchList(Path(tmp) / f'{name}.json') for name in ['a', 'b']]
            for watch in (first, second):
                WatchList.start(watch.path, watch.path.stem)
                watch.add('https://example.invalid/price', 'price', 'Example',
                          fetcher=lambda _: '<script>timestamp=1</script><p>Pro plan costs 49 dollars each month</p>')
            with self.assertRaises(WatchError):
                WatchList.start(first.path, 'a')
            result = first.check(fetcher=lambda _: '<script>timestamp=2</script><p>Pro plan costs 49 dollars each month</p>')
            self.assertEqual([], result['events'])
            result = first.check(fetcher=lambda _: '', log=lambda *args: None)
            self.assertEqual(1, result['failed'])
            self.assertEqual([], result['events'])
            changed = lambda _: 'Pro plan costs 99 dollars each month'
            self.assertEqual(1, len(first.check(fetcher=changed)['events']))
            self.assertEqual([], first.check(fetcher=changed)['events'])
            self.assertEqual([], second.events())
            self.assertEqual('needs_review', first.events()[0]['verification'])

    def test_future_supply_crosses_week_boundary_and_launch_embeds_portfolio(self):
        from core.ledger import Ledger
        fixture = test_grow_agent.ProductionTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        data = fixture.run.load()
        self.assertTrue(data['strategy']['tactic_portfolio']['picks'])
        data['supply']['min_cover_days'] = 1
        fixture.run.save(data)
        Ledger(fixture.root / 'ledger.jsonl').append(channel='x', action='reply',
            campaign='g-20261107', target='https://x.com/another/status/1',
            result='committed', ts='2026-11-07T04:00:00')
        result = fixture.run.produce(fixture.root, on='2026-11-07',
            writer=fixture.writer, discover=fixture.discover, max_items=1)
        self.assertEqual(1, result['added'], result)
        items = [item for p in (fixture.root / 'queues').glob('*.json')
                 for item in json.loads(p.read_text())['items']]
        self.assertEqual(['2026-11-08'], [item['scheduled_for'] for item in items])
