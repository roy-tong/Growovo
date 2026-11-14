"""结果闭环 v2：启动、配额执行、读数、日结果、周结算、决策与恢复。

全部用虚拟时钟 + 注入 runner/reader；零真实发送、不联网。
"""
import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from core.execution_policy import authorize
from core.grow_v2 import GrowError, GrowRunV2
from core.ledger import Ledger

ACTIONS = ["x:reply", "email:message"]


def _facts(tmp: Path) -> Path:
    facts = {
        "product": {"name": "Verovo", "what": "AI 账期分摊",
                    "url": "https://example.invalid", "stage": "launched",
                    "product_type": "devtool"},
        "icp": [{"hypothesis": "开发者", "tags": ["developers"],
                 "evidence_label": "hypothesis", "evidence": "待验证"}],
        "baseline": {"followers": 120},
        "value_events": [{"name": "激活", "source": "产品事件",
                          "is_approximation": False}],
        "constraints": {"red_lines": ["不做群发"]},
        "owned_assets": {"platforms": ["x", "email"]},
    }
    path = tmp / "facts.json"
    path.write_text(json.dumps(facts, ensure_ascii=False), encoding="utf-8")
    return path


def _queue(tmp: Path, run_id: str, day: str, *, count: int = 1,
           channel: str = "x", action: str = "reply",
           scheduled_for: str = "") -> Path:
    name = f"{run_id}-{day.replace('-', '')}"
    items = [{"id": f"{name}-{i}", "target": f"tweet:https://x.com/u/{day}/{i}",
              "body": f"针对 {day} 第 {i} 条的具体回复，没有链接。",
              "scheduled_for": scheduled_for or day}
             for i in range(count)]
    path = tmp / "queues" / f"{name}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"campaign": name, "channel": channel,
                                "action": action, "items": items},
                               ensure_ascii=False), encoding="utf-8")
    return path


def _runner(tmp: Path, day: str, result: str = "committed"):
    def run(queue_path, **kwargs):
        queue = json.loads(Path(queue_path).read_text(encoding="utf-8"))
        ids = set(kwargs.get("only_ids") or [])
        records = []
        ledger = Ledger(tmp / "ledger.jsonl")
        for item in queue["items"]:
            if item["id"] not in ids:
                continue
            if result == "committed":
                ledger.append(channel=queue["channel"], action=queue["action"],
                              target=item["target"], result="committed",
                              campaign=queue["campaign"],
                              ts=f"{day}T04:00:00", ref=item["id"])
            records.append({"result": result, "ref": item["id"],
                            "detail": "" if result == "committed" else "boom"})
        return records
    return run


def _reader(day: str):
    def read(on):
        return {"followers": 120 + int(on[-2:]), "impressions": 500}
    return read


def _now(day: str, hour: int = 4) -> datetime:
    return datetime.fromisoformat(day).replace(hour=hour, tzinfo=timezone.utc)


class LaunchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.facts = _facts(self.dir)
        self.path = self.dir / "grow" / "g1.json"

    def _launch(self, **kwargs):
        options = dict(run_id="g1", facts_path=str(self.facts),
                       goal_id="awareness.followers", channels=["x", "email"],
                       target=20, actions_per_week=7, started="2026-11-01",
                       owner="roy@example.com", handle="verovo",
                       data_dir=self.dir, skip_probe=True)
        options.update(kwargs)
        return GrowRunV2.launch(self.path, **options)

    def test_requires_routine_policy(self):
        with self.assertRaises(GrowError) as ctx:
            self._launch()
        self.assertIn("mkt policy enable", str(ctx.exception))
        self.assertFalse(self.path.exists())

    def test_probe_failure_refuses_to_start(self):
        authorize(self.dir, by="tester", actions=ACTIONS, days=30)

        def probe(_spec):
            raise RuntimeError("CDP 不可用")

        with self.assertRaises(GrowError) as ctx:
            self._launch(probe=probe, skip_probe=False)
        self.assertIn("环境探针失败", str(ctx.exception))

    def test_launch_writes_run_and_supply_briefs(self):
        authorize(self.dir, by="tester", actions=ACTIONS, days=30)
        result = self._launch()
        data = GrowRunV2(self.path).load()
        self.assertEqual("growovo.grow-run/2", data["schema"])
        self.assertEqual(["x", "email"], data["channels"])
        self.assertEqual(["email:message", "x:reply"], data["actions"])
        self.assertFalse(data["go"])
        self.assertEqual(7, data["quota"]["actions_per_week"])
        self.assertTrue((self.dir / "grow" / "g1" / "briefs.md").is_file())
        self.assertGreaterEqual(len(result["summary"]), 5)
        self.assertTrue(any("开闸" in line for line in result["summary"]))


class TickTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.facts = _facts(self.dir)
        self.path = self.dir / "grow" / "g1.json"
        authorize(self.dir, by="tester", actions=ACTIONS, days=60)
        GrowRunV2.launch(self.path, run_id="g1", facts_path=str(self.facts),
                         goal_id="awareness.followers", channels=["x", "email"],
                         target=20, actions_per_week=7, started="2026-11-01",
                         owner="roy@example.com", handle="verovo",
                         data_dir=self.dir, skip_probe=True)
        self.run = GrowRunV2(self.path)
        self.run.arm()

    def test_daily_quota_limits_sends_and_deferrs(self):
        _queue(self.dir, "g1", "2026-11-01", count=3)
        summary = self.run.tick(self.dir, on="2026-11-01",
                                runner=_runner(self.dir, "2026-11-01"),
                                reader=_reader("2026-11-01"),
                                now=_now("2026-11-01"))
        self.assertEqual(1, sum(r.get("published", 0) for r in summary["ran"]))
        self.assertEqual(1, summary["daily"]["published"]["total"])
        self.assertEqual(1, len(Ledger(self.dir / "ledger.jsonl").all()))
        # 同日第二个小时：配额已用完，顺延
        again = self.run.tick(self.dir, on="2026-11-01",
                              runner=_runner(self.dir, "2026-11-01"),
                              reader=_reader("2026-11-01"),
                              now=_now("2026-11-01", 5))
        self.assertEqual(0, sum(r.get("published", 0) for r in again["ran"]))
        self.assertEqual(1, len(Ledger(self.dir / "ledger.jsonl").all()))

    def test_window_outside_does_not_send(self):
        _queue(self.dir, "g1", "2026-11-01", count=1)
        # 10:00Z = 美西 02:00（死区），窗口外不发
        summary = self.run.tick(self.dir, on="2026-11-01",
                                runner=_runner(self.dir, "2026-11-01"),
                                reader=_reader("2026-11-01"),
                                now=_now("2026-11-01", 10))
        self.assertEqual(0, sum(r.get("published", 0) for r in summary["ran"]))
        self.assertEqual(0, len(Ledger(self.dir / "ledger.jsonl").all()))
        # 16:00Z = 美西 08:00，黄金窗内补发
        summary = self.run.tick(self.dir, on="2026-11-01",
                                runner=_runner(self.dir, "2026-11-01"),
                                reader=_reader("2026-11-01"),
                                now=_now("2026-11-01", 16))
        self.assertEqual(1, sum(r.get("published", 0) for r in summary["ran"]))
        self.assertEqual(1, summary["daily"]["published"]["total"])

    def test_readings_recorded_and_sensor_failure_degrades(self):
        _queue(self.dir, "g1", "2026-11-01", count=1)
        summary = self.run.tick(self.dir, on="2026-11-01",
                                runner=_runner(self.dir, "2026-11-01"),
                                reader=_reader("2026-11-01"),
                                now=_now("2026-11-01"))
        self.assertEqual(121, summary["readings"]["followers"])
        data = self.run.load()
        self.assertEqual(121, data["telemetry"]["days"]["2026-11-01"]
                         ["readings"]["followers"])

        def broken(_on):
            raise RuntimeError("CDP 掉线")

        _queue(self.dir, "g1", "2026-11-02", count=1)
        summary = self.run.tick(self.dir, on="2026-11-02",
                                runner=_runner(self.dir, "2026-11-02"),
                                reader=broken, now=_now("2026-11-02"))
        self.assertIn("CDP 掉线", summary["readings"]["error"])
        self.assertEqual("active", summary["status"])   # 传感器失败不停闭环

    def test_failed_result_pauses_and_resume_requires_note(self):
        _queue(self.dir, "g1", "2026-11-01", count=1)
        summary = self.run.tick(self.dir, on="2026-11-01",
                                runner=_runner(self.dir, "2026-11-01", result="failed"),
                                reader=_reader("2026-11-01"),
                                now=_now("2026-11-01"))
        self.assertEqual("needs_attention", summary["status"])
        with self.assertRaises(GrowError):
            self.run.resume(note="")
        data = self.run.resume(note="换掉坏队列", by="roy")
        self.assertEqual("active", data["status"])

    def test_daily_card_after_send_counts_committed(self):
        _queue(self.dir, "g1", "2026-11-01", count=1)
        self.run.tick(self.dir, on="2026-11-01",
                      runner=_runner(self.dir, "2026-11-01"),
                      reader=_reader("2026-11-01"), now=_now("2026-11-01"))
        card = self.run.daily(self.dir, on="2026-11-01")
        self.assertEqual(1, card["published"]["total"])
        self.assertEqual(2, card["published"]["planned"])  # X 85% + Email 15%
        self.assertTrue(card["decision"]["pending"])
        text = GrowRunV2.render_daily(card)
        self.assertIn("日结果", text)
        self.assertIn("continue", text)
        self.assertIn("optimize", text)


class WeeklyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.facts = _facts(self.dir)
        self.path = self.dir / "grow" / "g1.json"
        authorize(self.dir, by="tester", actions=ACTIONS, days=90)
        GrowRunV2.launch(self.path, run_id="g1", facts_path=str(self.facts),
                         goal_id="awareness.followers", channels=["x"],
                         target=20, actions_per_week=7, started="2026-11-01",
                         handle="verovo", data_dir=self.dir, skip_probe=True)
        self.run = GrowRunV2(self.path)
        self.run.arm()
        self._fill_week()

    def _fill_week(self, start: int = 1, end: int = 7):
        for d in range(start, end + 1):
            day = f"2026-11-{d:02d}"
            _queue(self.dir, "g1", day, count=1)
            self.run.tick(self.dir, on=day, runner=_runner(self.dir, day),
                          reader=_reader(day), now=_now(day))

    def test_week_card_scales_and_second_week_does_not_crash(self):
        summary = self.run.tick(self.dir, on="2026-11-08",
                                runner=lambda *a, **k: [],
                                reader=_reader("2026-11-08"),
                                now=_now("2026-11-08"))
        card = summary["weekly"]
        self.assertIsNotNone(card)
        self.assertEqual(1, card["week"])
        self.assertEqual(7, card["published"]["total"])
        self.assertEqual(6, card["followers"]["new"])
        data = self.run.load()
        self.assertEqual(7, data["plan"]["actions_per_week"])  # 等用户决定，不因发完就自动加量
        self.assertTrue(any(a["kind"] == "proposal" for a in data["adjustments"]))
        self.assertEqual(1, len(data["result_cards"]))

        # 第二周：空周也要出卡，且不能崩（v1 在这里 KeyError）
        second = self.run.tick(self.dir, on="2026-11-15",
                               runner=lambda *a, **k: [],
                               reader=_reader("2026-11-15"),
                               now=_now("2026-11-15"))
        self.assertEqual(2, second["weekly"]["week"])
        self.assertEqual(2, len(self.run.load()["result_cards"]))

    def test_decide_optimize_aligns_plan_to_supply(self):
        self.run.tick(self.dir, on="2026-11-08", runner=lambda *a, **k: [],
                      reader=_reader("2026-11-08"), now=_now("2026-11-08"))
        before = self.run.load()["plan"]["actions_per_week"]
        result = self.run.decide(action="optimize", note="供给为 0，先对齐",
                                 by="roy", on="2026-11-08", data_dir=self.dir)
        self.assertTrue(result["proposal"]["rationale"])
        data = self.run.load()
        self.assertEqual(1, data["plan"]["actions_per_week"])
        self.assertLess(data["plan"]["actions_per_week"], before)
        self.assertEqual(before, data["decisions"][-1]["changes"][0]["rollback"])

    def test_decide_continue_records_without_change(self):
        result = self.run.decide(action="continue", note="继续观察",
                                 by="roy", on="2026-11-06", data_dir=self.dir)
        self.assertEqual([], result["decision"]["changes"])
        self.assertEqual("continue", self.run.load()["decisions"][-1]["action"])

    def test_seven_matching_calibrations_promote_readings(self):
        days = [f"2026-11-{d:02d}" for d in range(1, 8)]
        result = None
        for day in days:
            result = self.run.calibrate(followers=120 + int(day[-2:]),
                                        by="tester", on=day)
        self.assertTrue(result["promoted"])
        self.assertEqual("PROVEN", result["verification"])
        self.assertEqual(7, result["streak"])

    def test_status_has_quota_supply_heartbeat_decision(self):
        status = self.run.status(self.dir, on="2026-11-06")
        self.assertIn("today_quota", status)
        self.assertIn("supply", status)
        self.assertIn("heartbeat", status)
        self.assertTrue(status["pending_decision"])
        self.assertIn("goal", status)


if __name__ == "__main__":
    unittest.main()


class OpsTests(unittest.TestCase):
    """复盘、授权范围、常驻与安装回退。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.facts = _facts(self.dir)
        self.path = self.dir / "grow" / "g1.json"
        authorize(self.dir, by="tester", actions=["x:reply", "x:publish",
                                                  "x:message", "email:message"],
                  days=60)
        GrowRunV2.launch(self.path, run_id="g1", facts_path=str(self.facts),
                         goal_id="awareness.followers", channels=["x"],
                         target=20, actions_per_week=7, started="2026-11-01",
                         handle="verovo", data_dir=self.dir, skip_probe=True)
        self.run = GrowRunV2(self.path)

    def test_review_aggregates_days(self):
        _queue(self.dir, "g1", "2026-11-01", count=1)
        self.run.arm()
        self.run.tick(self.dir, on="2026-11-01",
                      runner=_runner(self.dir, "2026-11-01"),
                      reader=_reader("2026-11-01"), now=_now("2026-11-01"))
        result = self.run.review(self.dir, days=2, on="2026-11-02")
        self.assertEqual(1, result["summary"]["published"])
        self.assertEqual(1, result["summary"]["coverage"])
        text = result["markdown"]
        self.assertIn("业务复盘", text)
        self.assertIn("2026-11-01", text)
        self.assertIn("边界", text)
        self.assertTrue(result["paths"].get("markdown"))

    def test_policy_scope_flags_extra_actions(self):
        scope = self.run.policy_scope(self.dir)
        self.assertTrue(scope["broad"])
        self.assertIn("x:publish", scope["extra"])
        self.assertNotIn("x:reply", scope["extra"])
        status = self.run.status(self.dir, on="2026-11-02")
        self.assertIn("policy_scope", status)

    def test_daemon_once_runs_a_tick_and_clears_lock(self):
        called = []

        def fake_tick():
            called.append(1)
            self.run.tick(self.dir, on="2026-11-02",
                          runner=lambda *a, **k: [],
                          reader=_reader("2026-11-02"), now=_now("2026-11-02"))

        self.run.daemon(self.dir, once=True, tick_fn=fake_tick)
        self.assertEqual([1], called)
        self.assertFalse((self.dir / "grow" / "g1" / "daemon.lock").exists())

    def test_loop_script_written(self):
        path = self.run.write_loop_script(self.dir, interval_minutes=30)
        text = path.read_text(encoding="utf-8")
        self.assertIn("grow tick g1", text)
        self.assertIn("sleep 1800", text)
        self.assertTrue(os.access(path, os.X_OK))

    def test_install_without_load_offers_fallback(self):
        result = self.run.install(self.dir, load=False)
        self.assertIn("fallback", result)
        self.assertTrue(Path(result["fallback"]).is_file())
        self.assertIsNone(result.get("verified"))


class ContentLoopTests(unittest.TestCase):
    """内容结构分、学习提案应用与复盘不足项。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.facts = _facts(self.dir)
        self.path = self.dir / "grow" / "g1.json"
        authorize(self.dir, by="tester", actions=["x:reply", "x:publish"], days=60)
        GrowRunV2.launch(self.path, run_id="g1", facts_path=str(self.facts),
                         goal_id="awareness.followers", channels=["x"],
                         target=20, actions_per_week=7, started="2026-11-01",
                         handle="verovo", data_dir=self.dir, skip_probe=True)
        self.run = GrowRunV2(self.path)
        self.run.arm()

    def _drafts(self, body="We reconcile invoice total, usage export and contract "
                          "price, then park the gap. Which source do you trust most?"):
        return [{"target": "tweet:https://x.com/a/1", "body": body,
                 "target_text": "Our AI invoice does not match usage. How do you "
                                "reconcile token cost per team?",
                 "formula": "reply.agree_extend_ask"}]

    def test_tick_collects_prefixed_targets_and_embeds_daily_learning(self):
        target = "https://x.com/parent/status/1"
        own = "https://x.com/me/status/2"
        data = self.run.load()
        data["post_urls"] = {target: own}
        data["content_index"] = {"tweet:" + target: {"formula": "reply.koc_peer"}}
        self.run.save(data)
        Ledger(self.dir / "ledger.jsonl").append(
            channel="x", action="reply", target="tweet:" + target,
            result="committed", campaign="g1", ts="2026-11-01T04:00:00")
        calls = []
        def read(url):
            calls.append(url)
            return {"views": 100, "replies": 5}
        for day in ("2026-11-01", "2026-11-01", "2026-11-02"):
            result = self.run.tick(self.dir, on=day, runner=lambda *a, **k: [],
                                   reader=_reader(day), metrics_reader=read, now=_now(day))
        self.assertEqual([own, own], calls)
        self.assertEqual(1, result["daily"]["learning"]["sample"])
        self.assertIn("今日复盘", self.run.render_daily(result["daily"]))
        self.assertEqual([], result["daily"]["learning"]["changes"])

    def test_stage_scores_content_and_records_index(self):
        result = self.run.stage(self.dir, self._drafts(),
                                scheduled_for="2026-11-02", by="agent")
        self.assertEqual(1, result["added"])
        self.assertIsNotNone(result["quality"]["average"])
        data = self.run.load()
        self.assertTrue(data["content_index"])
        item = (self.dir / "queues" / "g1-20261102.json")
        queue = json.loads(item.read_text(encoding="utf-8"))
        staged = queue["items"][0]
        self.assertEqual("reply", staged["kind"])
        self.assertIn("content_score", staged)
        self.assertEqual("reply.agree_extend_ask", staged["formula"])

    def test_stage_strict_rejects_low_score(self):
        drafts = [{"target": "tweet:https://x.com/a/2",
                   "body": "Great post!",
                   "target_text": "AI invoice reconciliation"}]
        with self.assertRaises(GrowError):
            self.run.stage(self.dir, drafts, scheduled_for="2026-11-02",
                           strict=True)

    def test_stage_resolves_target_text_when_requested(self):
        from unittest.mock import patch
        drafts = [{"target": "tweet:https://x.com/a/3",
                   "body": "We reconcile invoice total with usage and contract "
                           "price. Which source is canonical for you?"}]
        with patch("core.grow_targets.read_tweet_text",
                   return_value="Our AI invoice does not match usage"):
            result = self.run.stage(self.dir, drafts, scheduled_for="2026-11-02",
                                    resolve_target_text=True)
        self.assertEqual(1, result["added"])
        meta = self.run.load()["content_index"]["tweet:https://x.com/a/3"]
        self.assertEqual("reply.agree_extend_ask", meta["formula"])

    def test_stage_rejects_link_in_reply(self):
        drafts = [{"target": "tweet:https://x.com/a/1",
                   "body": "详见 https://example.com 这里",
                   "target_text": "AI invoice"}]
        with self.assertRaises(GrowError):
            self.run.stage(self.dir, drafts, scheduled_for="2026-11-02")

    def test_stage_records_koc_author_tier(self):
        drafts = [{"target": "tweet:https://x.com/koc/1",
                   "body": "We reconcile invoice total, usage export and contract "
                           "price, then park the gap. Which source do you trust?",
                   "target_text": "AI invoice reconciliation pain",
                   "author_followers": 8000}]
        self.run.stage(self.dir, drafts, scheduled_for="2026-11-02")
        meta = self.run.load()["content_index"]["tweet:https://x.com/koc/1"]
        self.assertEqual("koc", meta["author_tier"])
        self.assertTrue(meta["koc"])

    def test_decide_optimize_applies_learning_mix(self):
        learn_dir = self.dir / "grow" / "g1" / "learning"
        learn_dir.mkdir(parents=True)
        (learn_dir / "latest.json").write_text(json.dumps({
            "sample": 6, "recommendation": "倾斜到同意+补充式",
            "changes": [{"param": "content.mix_weights",
                         "from": "current",
                         "to": {"boost": "reply.agree_extend_ask",
                                "reduce": "reply.contrast_boundary"},
                         "rollback": "恢复原配比",
                         "detail": "A 组回复率更高"}]}, ensure_ascii=False),
            encoding="utf-8")
        result = self.run.decide(action="optimize", note="按学习调整",
                                 by="roy", on="2026-11-02", data_dir=self.dir)
        self.assertTrue(result["proposal"]["rationale"])
        repeated = self.run.decide(action="optimize", on="2026-11-02", data_dir=self.dir)
        self.assertEqual([], repeated["decision"]["changes"])
        data = self.run.load()
        weights = data["content"]["formula_weights"]
        self.assertGreater(weights["reply.agree_extend_ask"],
                           weights["reply.contrast_boundary"])
        self.assertTrue(any(a.get("param") == "content.mix_weights"
                            for a in data["adjustments"]))
        for brief in data["supply"]["briefs"]:
            total = sum(row["count"] for row in brief.get("formulas") or [])
            self.assertEqual(brief["count_per_week"], total)

    def test_decide_applies_koc_share_target(self):
        learn_dir = self.dir / "grow" / "g1" / "learning"
        learn_dir.mkdir(parents=True)
        (learn_dir / "latest.json").write_text(json.dumps({
            "sample": 6, "recommendation": "KOC 反馈更好",
            "changes": [{"param": "target.koc_share", "from": "current",
                         "to": {"koc_share": 0.7}, "rollback": "恢复 50%",
                         "detail": "KOC 组回复率更高"}]}, ensure_ascii=False),
            encoding="utf-8")
        self.run.decide(action="optimize", by="roy", on="2026-11-02",
                        data_dir=self.dir)
        data = self.run.load()
        self.assertEqual(0.7, data["growth_strategy"]["koc_share_target"])
        self.assertTrue(any(a.get("param") == "target.koc_share"
                            for a in data["adjustments"]))

    def test_stage_touch_updates_watchlist(self):
        drafts = [{"target": "tweet:https://x.com/koc/2",
                   "body": "We reconcile invoice total with usage and contract "
                           "price. Which source is canonical for you?",
                   "target_text": "AI invoice reconciliation pain",
                   "author_handle": "kocperson", "author_followers": 4200}]
        self.run.stage(self.dir, drafts, scheduled_for="2026-11-02")
        from core import grow_fit
        accounts = grow_fit.load_watchlist(self.dir, "g1")
        self.assertIn("kocperson", accounts)
        self.assertEqual(1, accounts["kocperson"]["touches"])
        self.assertEqual("2026-11-02", accounts["kocperson"]["last_engaged_at"])

    def test_review_reports_stale_items(self):
        drafts = [{"target": "tweet:https://x.com/old/1",
                   "body": "We reconcile invoice total with usage and contract "
                           "price. Which source is canonical for you?",
                   "target_text": "AI invoice reconciliation pain"}]
        self.run.stage(self.dir, drafts, scheduled_for="2026-11-02")
        result = self.run.review(self.dir, days=1, on="2026-11-06")
        self.assertIn("过期未发", result["markdown"])
        self.assertTrue(any("过期" in item for item in result["summary"]["shortfalls"]))

    def test_review_lists_shortfalls_and_next_actions(self):
        result = self.run.review(self.dir, days=1, on="2026-11-02")
        self.assertTrue(result["summary"]["shortfalls"])
        self.assertTrue(result["summary"]["next_actions"])
        self.assertIn("今日不足", result["markdown"])
        self.assertIn("内容与名单", result["markdown"])
        self.assertIn("content_avg", result["summary"])
        self.assertIn("prospect_date", result["summary"])
        self.assertTrue(self.run.load().get("reviews"))
