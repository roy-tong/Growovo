"""结果三层、参数回流、执行桥。"""
import json
import tempfile
import unittest
from pathlib import Path

from core import execute, result, revise, strategy


def _ledger():
    return [{"result": "committed"}] * 8 + [{"result": "failed"}] * 1 + [{"result": "unknown"}] * 1


class ResultTests(unittest.TestCase):
    def test_three_layers_are_separate(self):
        r = result.collect(ledger_entries=_ledger(),
                           outcome_events=[{"type": "signup", "verified": True}])
        self.assertEqual(r["platform"]["sent"], 8)
        self.assertEqual(r["platform"]["failed"], 1)
        self.assertEqual(r["platform"]["unknown"], 1)
        self.assertEqual(r["business"]["signup"], 1)

    def test_approximated_events_never_count(self):
        r = result.collect(ledger_entries=_ledger(),
                           outcome_events=[{"type": "signup", "is_approximation": True}])
        self.assertEqual(r["business"]["signup"], 0)

    def test_actions_alone_do_not_produce_business_proof(self):
        r = result.collect(ledger_entries=_ledger(),
                           criteria=[{"name": "付费", "metric": "paid", "target": 1}])
        self.assertNotEqual(r["observation"], "过门")
        self.assertEqual(r["criteria"][0]["actual"], 0)

    def test_missing_metric_is_untested_not_zero(self):
        r = result.collect(ledger_entries=_ledger(),
                           criteria=[{"name": "留存", "metric": "retention", "target": 0.4}])
        self.assertEqual(r["criteria"][0]["actual"], None)
        self.assertEqual(r["criteria"][0]["note"], "这个指标目前取不到数")

    def test_render_keeps_layers_apart(self):
        md = result.render(result.collect(ledger_entries=_ledger()))
        for part in ("三层分开算", "平台动作", "任务结果", "业务结果"):
            self.assertIn(part, md)


class ReviseTests(unittest.TestCase):
    def _params(self):
        return {"actions_per_week": 20, "tier": "AI 生成", "primary": "github"}

    def test_thin_sample_refuses_to_conclude(self):
        r = result.collect(ledger_entries=[{"result": "committed"}] * 3)
        diff = revise.propose(r, self._params())
        self.assertIn("样本量", diff[0]["why"])

    def test_low_response_blames_the_opening_not_the_channel(self):
        r = result.collect(ledger_entries=[{"result": "committed"}] * 100)
        r["task"] = {"replied": 1, "asked": 0, "objects": 1, "moved": 0}
        diff = revise.propose(r, self._params())
        self.assertIn("开口方式", diff[0]["why"])
        self.assertEqual(diff[0]["param"], "第一条消息的开头")

    def test_high_deletion_lowers_frequency_and_tier(self):
        r = result.collect(ledger_entries=[{"result": "committed"}] * 100)
        r["task"] = {"replied": 10, "asked": 0, "objects": 10, "moved": 3}
        diff = revise.propose(r, self._params(),
                              intent={"deleted_rate": 0.22, "response_rate": 0.1})
        params = [d["param"] for d in diff]
        self.assertIn("发布频率", params)
        self.assertIn("内容档位", params)
        tier = next(d for d in diff if d["param"] == "内容档位")
        self.assertEqual(tier["to"], "人写人发")

    def test_no_paid_with_replies_escalates_to_strategy(self):
        r = result.collect(ledger_entries=[{"result": "committed"}] * 50)
        r["task"] = {"replied": 6, "asked": 0, "objects": 6, "moved": 2}
        diff = revise.propose(r, self._params())
        self.assertTrue(any(d["kind"] == revise.STRATEGY for d in diff))


class ExecuteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = Path(self.tmp.name) / "data"

    def _targets(self):
        return [{"target": f"user{i}@example.com",
                 "body": f"看到你在做 X，我这边有个同类问题的复现，发你。第{i}条"}
                for i in range(1, 4)]

    def test_builds_plan_and_queue_that_pass_gates(self):
        s = strategy.build({"name": "Verovo"}, focus={"primary": "email"}, analysis={})
        res = execute.build(s, self._targets(), campaign="verovo-w1", data_dir=self.data)
        self.assertEqual(res["items"], 3)
        plan = json.loads(Path(res["plan"]).read_text(encoding="utf-8"))
        self.assertEqual(plan["plan_id"], "verovo-w1")
        self.assertFalse(plan["review"]["human_approved"])
        q = json.loads(Path(res["queue"]).read_text(encoding="utf-8"))
        self.assertEqual(len(q["items"]), 3)

    def test_empty_targets_refused(self):
        s = strategy.build({"name": "x"}, focus={"primary": "email"}, analysis={})
        with self.assertRaises(execute.ExecuteError):
            execute.build(s, [], campaign="c", data_dir=self.data)

    def test_duplicate_targets_refused(self):
        s = strategy.build({"name": "x"}, focus={"primary": "email"}, analysis={})
        t = self._targets() + [{"target": "user1@example.com", "body": "重复目标"}]
        with self.assertRaises(execute.ExecuteError) as ctx:
            execute.build(s, t, campaign="c", data_dir=self.data)
        self.assertIn("重复", str(ctx.exception))

    def test_manual_channel_gets_a_work_sheet_not_a_fake_queue(self):
        s = strategy.build({"name": "x"}, focus={"primary": "xiaohongshu"}, analysis={})
        res = execute.build(s, self._targets(), campaign="c", data_dir=self.data)
        self.assertTrue(res["manual"])
        self.assertIsNone(res["queue"])
        sheet = Path(res["sheet"])
        self.assertTrue(sheet.exists())
        text = sheet.read_text(encoding="utf-8")
        for part in ("人工作业单", "开跑前确认", "逐条执行", "绝对不能做"):
            self.assertIn(part, text)
        self.assertFalse((self.data / "queues" / "c.json").exists())

    def test_nothing_written_when_gate_fails(self):
        s = strategy.build({"name": "x"}, focus={"primary": "email"}, analysis={})
        bad = [{"target": "a@example.com", "body": ""}]
        with self.assertRaises(execute.ExecuteError):
            execute.build(s, bad, campaign="c", data_dir=self.data)
        self.assertFalse((self.data / "plans" / "c.json").exists())


if __name__ == "__main__":
    unittest.main()
