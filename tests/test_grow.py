"""结果闭环：预检授权、日循环、周结果卡与自动调参；排期 routine 透传；X 读数解析。

零真实发送：tick 的 runner 全部注入假实现；CLI 走临时工作区。
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MKT = ROOT / "mkt.py"

from channels.x import read_stats
from core.grow import CHANNEL_ACTIONS, GrowError, GrowRun, preflight_policy
from core.execution_policy import authorize


def _authorize(data_dir):
    actions = [a for acts in CHANNEL_ACTIONS.values() for a in acts]
    authorize(Path(data_dir), by="测试授权人", actions=actions, days=30)


def _write_facts(data_dir, name="facts.json"):
    facts = {
        "product": {"name": "Verovo", "what": "AI 账期分摊", "url": "https://example.invalid",
                    "stage": "launched", "product_type": "devtool"},
        "icp": [{"hypothesis": "开发者", "tags": ["developers"],
                 "evidence_label": "hypothesis", "evidence": "待验证"}],
        "baseline": {"followers": 120},
        "value_events": [{"name": "激活", "source": "产品事件", "is_approximation": False}],
        "constraints": {"red_lines": ["不做群发"]},
        "owned_assets": {"platforms": ["x", "email"]},
    }
    path = Path(data_dir) / name
    path.write_text(json.dumps(facts, ensure_ascii=False), encoding="utf-8")
    return path


class TmpDir(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)


class PreflightTests(TmpDir):
    def test_missing_policy_gives_exact_command(self):
        missing, command = preflight_policy(self.dir, ["x", "email"])
        self.assertEqual(sorted(CHANNEL_ACTIONS["x"] + CHANNEL_ACTIONS["email"]),
                         sorted(missing))
        self.assertIn("mkt policy enable --by", command)
        self.assertNotIn("<", command)          # 粘贴即可执行，不是占位符
        self.assertIn("$USER", command)
        self.assertIn("x:reply", command)
        self.assertIn("email:message", command)

    def test_unknown_channel_rejected(self):
        with self.assertRaises(GrowError):
            preflight_policy(self.dir, ["zhihu"])

    def test_start_refuses_without_policy(self):
        facts = _write_facts(self.dir)
        with self.assertRaises(GrowError) as ctx:
            GrowRun.start(self.dir / "grow" / "g1.json", "g1", str(facts),
                          "awareness.followers", ["x", "email"], data_dir=self.dir)
        self.assertIn("mkt policy enable", str(ctx.exception))
        self.assertFalse((self.dir / "grow" / "g1.json").exists())

    def test_start_builds_goal_contract_after_authorize(self):
        _authorize(self.dir)
        facts = _write_facts(self.dir)
        data = GrowRun.start(self.dir / "grow" / "g1.json", "g1", str(facts),
                             "awareness.followers", ["x", "email"],
                             target=200, started="2026-11-01", data_dir=self.dir)
        self.assertEqual("followers", data["goal"]["metric"])
        self.assertEqual(120, data["goal"]["baseline"])
        self.assertEqual(200, data["goal"]["target"])
        self.assertFalse(data["go"])
        self.assertEqual(20, data["plan"]["actions_per_week"])


class TickTests(TmpDir):
    def setUp(self):
        super().setUp()
        _authorize(self.dir)
        facts = _write_facts(self.dir)
        self.path = self.dir / "grow" / "g1.json"
        GrowRun.start(self.path, "g1", str(facts), "awareness.followers",
                      ["x", "email"], target=200, started="2026-11-01",
                      data_dir=self.dir)
        self.queue = self.dir / "queues" / "g1-w1.json"
        self.queue.parent.mkdir(parents=True, exist_ok=True)
        self.queue.write_text(json.dumps({
            "campaign": "g1-w1", "channel": "x", "action": "reply",
            "items": [{"id": "g1-001", "target": "tweet:https://x.com/a/1",
                       "body": "一条可发的回复，内容独立成立且没有链接。"}],
        }, ensure_ascii=False), encoding="utf-8")

    def _run(self):
        return GrowRun(self.path)

    def test_queue_runs_once_per_day(self):
        calls = []

        def runner(queue_path, **kwargs):
            calls.append((str(queue_path), kwargs))
            return [{"result": "dry-run"}]

        first = self._run().tick(self.dir, on="2026-11-02", runner=runner)
        self.assertEqual(1, len(first["ran"]))
        self.assertFalse(first["ran"][0].get("error"))
        second = self._run().tick(self.dir, on="2026-11-02", runner=runner)
        self.assertEqual([], second["ran"])           # 当天第二次不重复
        third = self._run().tick(self.dir, on="2026-11-03", runner=runner)
        self.assertEqual(1, len(third["ran"]))
        self.assertEqual("active", third["status"])

    def test_all_duplicate_rejections_are_benign(self):
        def runner(queue_path, **kwargs):
            return [{"result": "rejected", "detail": "同 campaign 已成功发过（防重发）"},
                    {"result": "suppressed"}]

        summary = self._run().tick(self.dir, on="2026-11-02", runner=runner)
        self.assertEqual("active", summary["status"])
        self.assertIn("未重复发送", summary["ran"][0].get("note", ""))

    def test_rejection_without_duplicate_reason_pauses_loop(self):
        def runner(queue_path, **kwargs):
            return [{"result": "rejected", "detail": "内容检查：回复不得带链接"}]

        summary = self._run().tick(self.dir, on="2026-11-02", runner=runner)
        self.assertEqual("needs_attention", summary["status"])
        self.assertIn("内容检查", summary["ran"][0]["error"])

    def test_resume_clears_needs_attention_and_requires_note(self):
        def runner(queue_path, **kwargs):
            return [{"result": "failed"}]

        self._run().tick(self.dir, on="2026-11-02", runner=runner)
        self.assertEqual("needs_attention", self._run().load()["status"])
        with self.assertRaises(GrowError):
            self._run().resume(note="")
        data = self._run().resume(note="换掉坏队列，重新导出", by="roy")
        self.assertEqual("active", data["status"])
        again = self._run().tick(self.dir, on="2026-11-03",
                                  runner=lambda p, **k: [{"result": "dry-run"}])
        self.assertEqual("active", again["status"])
        self.assertEqual(1, len(again["ran"]))

    def test_failed_result_pauses_loop(self):
        def runner(queue_path, **kwargs):
            return [{"result": "failed"}]

        summary = self._run().tick(self.dir, on="2026-11-02", runner=runner)
        self.assertEqual("needs_attention", summary["status"])
        self.assertIn("人工", summary["ran"][0]["error"])
        # needs_attention 后续 tick 直接跳过，不自动重试
        again = self._run().tick(self.dir, on="2026-11-03", runner=runner)
        self.assertEqual("needs_attention", again.get("skipped"))

    def test_tick_passes_routine_and_go_flag(self):
        seen = {}

        def runner(queue_path, **kwargs):
            seen.update(kwargs)
            return [{"result": "dry-run"}]

        self._run().tick(self.dir, on="2026-11-02", runner=runner)
        self.assertTrue(seen.get("routine"))
        self.assertFalse(seen.get("go"))              # go 默认关=dry-run
        self._run().arm()
        seen.clear()
        self._run().tick(self.dir, on="2026-11-03", runner=runner)
        self.assertTrue(seen.get("go"))


class WeeklyCardTests(TmpDir):
    def setUp(self):
        super().setUp()
        _authorize(self.dir)
        facts = _write_facts(self.dir)
        self.path = self.dir / "grow" / "g1.json"
        GrowRun.start(self.path, "g1", str(facts), "awareness.followers",
                      ["x", "email"], target=200, started="2026-11-01",
                      data_dir=self.dir)

    def _seed_week(self, committed=20):
        from core.attribution import OutcomeLedger
        from core.ledger import Ledger
        ledger = Ledger(self.dir / "ledger.jsonl")
        for i in range(committed):
            ledger.append(channel="x", action="reply",
                          target=f"tweet:https://x.com/a/{i}",
                          result="committed", campaign="g1-w1",
                          ts="2026-11-03T10:00:00")
        ol = OutcomeLedger(self.dir)
        ol.add_count(metric="x_followers", value=120, window="2026-10-31",
                     source_ref="seed", approximated=True)
        ol.add_count(metric="x_followers", value=153, window="2026-11-06",
                     source_ref="seed", approximated=True)

    def test_week_close_emits_card_and_scales_up(self):
        self._seed_week(committed=20)   # 20/20 ≥ 90% → 加量 20→25（上限 40）
        summary = GrowRun(self.path).tick(self.dir, on="2026-11-08",
                                          runner=lambda p, **k: [])
        card = summary["card"]
        self.assertEqual(1, card["week"])
        self.assertEqual(20, card["published"])
        self.assertEqual(33, card["new_followers"])
        data = GrowRun(self.path).load()
        self.assertEqual(25, data["plan"]["actions_per_week"])
        self.assertTrue(any(a["kind"] == "auto" for a in data["adjustments"]))

    def test_under_half_notes_attention_without_cutting(self):
        self._seed_week(committed=6)    # 6/20 < 50% → 只提示
        GrowRun(self.path).tick(self.dir, on="2026-11-08",
                                runner=lambda p, **k: [])
        data = GrowRun(self.path).load()
        self.assertEqual(20, data["plan"]["actions_per_week"])
        self.assertTrue(any(a["kind"] == "attention" for a in data["adjustments"]))

    def test_scale_up_cap_anchors_to_initial_plan(self):
        from core.ledger import Ledger
        self._seed_week(committed=20)   # 第 1 周：20 → 25
        GrowRun(self.path).tick(self.dir, on="2026-11-08",
                                runner=lambda p, **k: [])
        data = GrowRun(self.path).load()
        self.assertEqual(25, data["plan"]["actions_per_week"])
        data["plan"]["actions_per_week"] = 38   # 模拟前面几周已经加过量
        GrowRun(self.path).save(data)
        ledger = Ledger(self.dir / "ledger.jsonl")
        for i in range(40):                     # 第 2 周完成 40/38
            ledger.append(channel="x", action="reply",
                          target=f"tweet:https://x.com/b/{i}",
                          result="committed", campaign="g1-w2",
                          ts="2026-11-10T10:00:00")
        GrowRun(self.path).tick(self.dir, on="2026-11-15",
                                runner=lambda p, **k: [])
        data = GrowRun(self.path).load()
        self.assertEqual(40, data["plan"]["actions_per_week"])  # 上限 2×初始 20

    def test_dry_run_week_is_not_counted_as_failure(self):
        GrowRun(self.path).tick(self.dir, on="2026-11-08",
                                runner=lambda p, **k: [])
        data = GrowRun(self.path).load()
        self.assertTrue(any(a["kind"] == "dry-run" for a in data["adjustments"]))
        self.assertFalse(any(a["kind"] == "attention" for a in data["adjustments"]))

    def test_status_and_report(self):
        self._seed_week(committed=20)
        GrowRun(self.path).tick(self.dir, on="2026-11-08",
                                runner=lambda p, **k: [])
        run = GrowRun(self.path)
        status = run.status(self.dir, on="2026-11-08")
        self.assertEqual("followers", status["goal"]["metric"])
        self.assertEqual(153, status["goal"]["current"])
        self.assertEqual(25, status["plan_actions_per_week"])
        text = run.report(self.dir, on="2026-11-08")
        self.assertIn("结果单", text)
        self.assertIn("新增粉丝", text)


class SchedulerRoutineTests(TmpDir):
    def test_routine_passes_through_to_runner(self):
        from core import scheduler
        queue = self.dir / "queues" / "q1.json"
        queue.parent.mkdir(parents=True, exist_ok=True)
        queue.write_text("{}", encoding="utf-8")
        seen = []

        def runner(queue_path, **kwargs):
            seen.append(kwargs)
            return []

        scheduler.add(self.dir, schedule_id="s1", queues=[str(queue)],
                      interval_minutes=60, go=False, routine=True, account="")
        scheduler.tick(self.dir, runner=runner)
        self.assertTrue(seen and seen[0].get("routine") is True)

    def test_without_routine_keeps_legacy_call(self):
        from core import scheduler
        queue = self.dir / "queues" / "q2.json"
        queue.parent.mkdir(parents=True, exist_ok=True)
        queue.write_text("{}", encoding="utf-8")
        seen = []

        def runner(queue_path, **kwargs):
            seen.append(kwargs)
            return []

        scheduler.add(self.dir, schedule_id="s2", queues=[str(queue)],
                      interval_minutes=60, go=False)
        scheduler.tick(self.dir, runner=runner)
        self.assertTrue(seen)
        self.assertNotIn("routine", seen[0])


class ReadStatsParseTests(TmpDir):
    def test_parse_followers_formats(self):
        self.assertEqual(1234, read_stats.parse_followers("1,234 Followers\nFollowing 56"))
        self.assertEqual(1200, read_stats.parse_followers("1.2K Followers"))
        self.assertEqual(15000, read_stats.parse_followers("1.5万 粉丝"))
        self.assertEqual(33, read_stats.parse_followers("194 正在关注 | 33 关注者"))
        self.assertIsNone(read_stats.parse_followers("194 正在关注"))  # Following 不是粉丝
        self.assertIsNone(read_stats.parse_followers("no numbers here"))

    def test_parse_impressions_formats(self):
        self.assertEqual(12345, read_stats.parse_impressions("Impressions\n12,345"))
        self.assertEqual(9000, read_stats.parse_impressions("Impressions 9K"))
        self.assertIsNone(read_stats.parse_impressions("nothing"))

    def test_record_writes_approximated_counts(self):
        from core.attribution import OutcomeLedger
        stats = {"saved": "/tmp/stats.json", "followers": 153,
                 "impressions_yesterday": 4321, "impressions_window": "2026-11-13",
                 "read_at": "2026-11-14T09:00:00"}
        written = read_stats.record(self.dir, stats, campaign="g1")
        self.assertEqual({"x_followers": 153, "x_impressions": 4321}, written)
        records = OutcomeLedger(self.dir).records()
        metrics = {r["metric"]: r for r in records}
        self.assertTrue(all(r["approximated"] for r in records))
        self.assertEqual("/tmp/stats.json", metrics["x_followers"]["source_ref"])
        self.assertEqual("2026-11-13", metrics["x_impressions"]["window"])


class GrowCliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / "data"
        self.home.mkdir(parents=True, exist_ok=True)
        self.env = dict(os.environ, MKT_HOME=str(self.home))

    def run_mkt(self, *args, check=True):
        proc = subprocess.run([sys.executable, str(MKT), *args], cwd=ROOT, env=self.env,
                              text=True, capture_output=True)
        if check and proc.returncode:
            self.fail(f"mkt {' '.join(args)} failed ({proc.returncode})"
                      f"\nSTDOUT:{proc.stdout}\nSTDERR:{proc.stderr}")
        return proc

    def _authorize_cli_side(self):
        subprocess.run(
            [sys.executable, "-c",
             "import sys; sys.path.insert(0, '.'); from pathlib import Path; "
             "from core.execution_policy import authorize; "
             "authorize(Path(sys.argv[1]), by='测试授权人', "
             "actions=['x:publish','x:reply','x:message','email:message'], days=30)",
             str(self.home)],
            cwd=ROOT, env=self.env, check=True)

    def test_grow_chain_and_tactic_metrics(self):
        facts = self.home / "facts.json"
        facts.write_text(json.dumps({
            "product": {"name": "Verovo", "what": "test", "url": "https://example.invalid",
                        "stage": "launched", "product_type": "devtool"},
            "icp": [{"hypothesis": "开发者", "tags": ["developers"],
                     "evidence_label": "hypothesis", "evidence": "待验证"}],
            "baseline": {"followers": 120},
            "value_events": [{"name": "激活", "source": "产品事件", "is_approximation": False}],
            "constraints": {"red_lines": ["不做群发"]},
            "owned_assets": {"platforms": ["x", "email"]},
        }, ensure_ascii=False), encoding="utf-8")

        # 没有常驻授权：拒启并给精确命令
        proc = self.run_mkt("grow", "start", "g1", "--facts", str(facts),
                            "--goal", "awareness.followers", check=False)
        self.assertEqual(2, proc.returncode)
        self.assertIn("mkt policy enable", proc.stdout + proc.stderr)

        self._authorize_cli_side()
        out = self.run_mkt("grow", "start", "g1", "--facts", str(facts),
                           "--goal", "awareness.followers")
        self.assertIn("闭环已建", out.stdout)
        self.assertIn("go=False", out.stdout)

        out = self.run_mkt("grow", "status", "g1", "--on", "2026-11-08")
        self.assertIn("目标进度", out.stdout)

        out = self.run_mkt("grow", "tick", "g1", "--on", "2026-11-08")
        self.assertIn("跑了 0 个队列", out.stdout)     # 没有暂存队列

        out = self.run_mkt("grow", "report", "g1", "--on", "2026-11-08")
        self.assertIn("还没有完成整周", out.stdout)

        # tactic log --metrics 补齐的口子
        self.run_mkt("tactic", "start", "xrg-1", "--tactic", "x-reply-guy",
                     "--on", "2026-11-01")
        self.run_mkt("tactic", "log", "xrg-1", "--sent", "18", "--replied", "2",
                     "--on", "2026-11-02",
                     "--metrics", '{"profile_visits": 210, "new_followers": 31}')
        out = self.run_mkt("tactic", "show-run", "xrg-1", "--on", "2026-11-08")
        self.assertIn("周指标", out.stdout)
        self.assertIn("new_followers=31", out.stdout)


if __name__ == "__main__":
    unittest.main()
