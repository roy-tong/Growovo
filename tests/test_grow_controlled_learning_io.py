"""G-04 实现侧补充测试：自动减量/停机、学习复审、CLI 改动审批与回滚。

冻结契约在 ``test_grow_controlled_learning_g04.py``；这里补它没覆盖的实现面：

1. 极端失败守卫与自动减量（只能向下，记录 kind=auto_safety）；
2. grow_learning 的复审触发与口径字段、learn 附带的 assessment；
3. CLI：`mkt grow change ...` 的 dry-run/apply、批准后才会升权；
4. §12.5 渲染与人工分钟口径。

全部离线：合成数据，零网络、零真实发送。
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from core import controlled_learning as cl
from core import grow_learning
from core import outcome_layers
from core.execution_policy import authorize
from core.grow_v2 import GrowRunV2
from core.ledger import Ledger

ROOT = Path(__file__).resolve().parents[1]
MKT = ROOT / "mkt.py"


def facts(tmp: Path) -> Path:
    payload = {
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
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def run_data(**override):
    data = {
        "id": "g1", "channels": ["x"], "actions": ["x:reply"],
        "plan": {"actions_per_week": 7},
        "quota": {"actions_per_week": 7, "action_weights": {"reply": 1.0}},
        "guards": {"account_id": "acct-a"},
        "growth_strategy": {"koc_share_target": 0.5},
        "content": {"formula_weights": {"reply.a": 50, "reply.b": 50}},
        "supply": {"briefs": []},
        "targeting": {"audience": "us-west", "queries": ["ai invoice"]},
        "automation": {"enabled": False},
        "status": "active", "go": True,
        "adjustments": [], "strategy_changes": [],
        "change_approvals": [], "applied_evidence": [],
    }
    data.update(override)
    return data


class GuardTests(unittest.TestCase):
    def test_extreme_failure_stops_without_touching_quota_or_suppression(self):
        data = run_data()
        quota_before = dict(data["quota"])
        result = cl.guard_extreme_failure(
            data, failed=2, indeterminate=1, on="2026-11-05")
        self.assertEqual("stop", result["action"])
        self.assertEqual("paused", data["status"])
        self.assertEqual(quota_before["actions_per_week"],
                         data["quota"]["actions_per_week"])
        self.assertEqual([], data.get("change_approvals"))
        record = data["strategy_changes"][-1]
        self.assertEqual("auto_stop", record["kind"])
        self.assertFalse(record["external_actions_changed"])
        for path in ("contacts.jsonl", "suppress.txt"):
            self.assertFalse((self.dir / path).exists())

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def test_no_failure_no_action(self):
        data = run_data()
        result = cl.guard_extreme_failure(data, failed=0, indeterminate=0)
        self.assertEqual("none", result["action"])
        self.assertEqual("active", data["status"])
        self.assertEqual([], data["strategy_changes"])

    def test_auto_reduce_only_goes_down_and_records_hashes(self):
        data = run_data()
        before = cl.strategy_hash(data)
        record = cl.record_auto_reduce(
            data, param="actions_per_week", from_value=7, to_value=3,
            reason="账号健康 40 < 60", on="2026-11-05")
        self.assertEqual(3, data["plan"]["actions_per_week"])
        self.assertEqual("auto_safety", data["strategy_changes"][-1]["kind"])
        self.assertTrue(record["before_hash"])
        self.assertNotEqual(before, cl.strategy_hash(data))
        with self.assertRaises(cl.ControlledLearningError):
            cl.record_auto_reduce(data, param="actions_per_week",
                                  from_value=3, to_value=7, reason="想加回来")


class LearningReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def _seed_metrics(self, rows, index):
        run_dir = Path(self.dir) / "grow" / "g1"
        run_dir.mkdir(parents=True)
        (run_dir / "post_metrics.jsonl").write_text(
            "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
        return {"content_index": index}

    def test_viral_spike_blocks_weight_changes(self):
        rows, index = [], {}
        for i, views in enumerate((100, 100, 1200)):
            rows.append({"url": f"https://x.com/a/status/{i}",
                         "views": views, "replies": 3, "likes": 5})
            index[f"https://x.com/a/status/{i}"] = {
                "formula": "reply.agree_extend_ask"}
        payload = grow_learning.analyze(
            self.dir, "g1", self._seed_metrics(rows, index))
        self.assertTrue(payload["review_required"])
        self.assertEqual([], payload["changes"])
        self.assertIn("爆量", "；".join(payload["review_reasons"]))
        self.assertEqual("non_causal", payload["analysis_mode"])
        text = grow_learning.render(payload)
        self.assertIn("复审", text)
        self.assertIn("不宣称策略胜出", text)

    def test_zero_interaction_blocks_weight_changes(self):
        rows, index = [], {}
        for i in range(3):
            rows.append({"url": f"https://x.com/b/status/{i}",
                         "views": 100, "replies": 0, "likes": 1})
            index[f"https://x.com/b/status/{i}"] = {
                "formula": "reply.contrast_boundary"}
        payload = grow_learning.analyze(
            self.dir, "g1", self._seed_metrics(rows, index))
        self.assertTrue(payload["review_required"])
        self.assertEqual([], payload["changes"])
        self.assertIn("零互动", "；".join(payload["review_reasons"]))

    def test_clean_sample_keeps_exploration_fields(self):
        rows, index = [], {}
        for i in range(3):
            rows.append({"url": f"https://x.com/c/status/{i}",
                         "views": 100, "replies": 10, "likes": 8})
            index[f"https://x.com/c/status/{i}"] = {
                "formula": "reply.agree_extend_ask"}
        payload = grow_learning.analyze(
            self.dir, "g1", self._seed_metrics(rows, index))
        self.assertFalse(payload["review_required"])
        self.assertEqual("non_causal", payload["analysis_mode"])
        self.assertTrue(payload["limitations"])


class LearnAndDecideTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        authorize(self.dir, by="tester", actions=["x:reply", "email:message"],
                  days=90)
        self.path = self.dir / "grow" / "g1.json"
        GrowRunV2.launch(self.path, run_id="g1", facts_path=str(facts(self.dir)),
                         goal_id="awareness.followers", channels=["x"],
                         target=20, actions_per_week=7, started="2026-11-01",
                         handle="acct-a", data_dir=self.dir, skip_probe=True)
        self.run = GrowRunV2(self.path)
        self.run.arm(by="roy")

    def _latest_learning(self, payload):
        directory = self.dir / "grow" / "g1" / "learning"
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "latest.json").write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    def test_learn_attaches_assessment_and_writes_it(self):
        result = self.run.learn(self.dir, on="2026-11-02")
        self.assertEqual("no_data", result["assessment"]["analysis_mode"])
        stored = json.loads(
            (self.dir / "grow" / "g1" / "learning" / "latest.json")
            .read_text(encoding="utf-8"))
        self.assertIn("assessment", stored)
        self.assertFalse(stored["assessment"]["can_optimize"])

    def test_same_learning_evidence_twice_is_no_op(self):
        self._latest_learning({
            "sample": 6, "recommendation": "倾斜 A",
            "changes": [{"param": "target.audience", "from": "us-west",
                         "to": "us-east", "rollback": "us-west",
                         "detail": "换受众"}]})
        first = self.run.decide(action="optimize", by="roy",
                                on="2026-11-02", data_dir=self.dir)
        self.assertEqual(1, len(first["decision"]["changes"]))
        second = self.run.decide(action="optimize", by="roy",
                                 on="2026-11-02", data_dir=self.dir)
        self.assertEqual([], second["decision"]["changes"])
        data = self.run.load()
        self.assertEqual(1, len(data["strategy_changes"]))
        self.assertEqual("us-east", data["targeting"]["audience"])

    def test_thin_sample_learning_is_needs_review_and_keeps_old_strategy(self):
        self._latest_learning({
            "sample": 2, "recommendation": "两条帖子看着不错",
            "changes": [{"param": "target.audience", "from": "us-west",
                         "to": "us-east", "detail": "换受众"}]})
        before = cl.strategy_hash(self.run.load())
        result = self.run.decide(action="optimize", by="roy",
                                 on="2026-11-02", data_dir=self.dir)
        self.assertEqual("needs_review", result["decision"]["status"])
        self.assertEqual([], result["decision"]["changes"])
        self.assertEqual(before, cl.strategy_hash(self.run.load()))
        self.assertEqual("active", self.run.load()["status"])

    def test_dry_run_decide_does_not_mutate(self):
        self._latest_learning({
            "sample": 6, "recommendation": "倾斜 A",
            "changes": [{"param": "target.audience", "from": "us-west",
                         "to": "us-east", "detail": "换受众"}]})
        before = cl.strategy_hash(self.run.load())
        result = self.run.decide(action="optimize", by="roy", dry_run=True,
                                 on="2026-11-02", data_dir=self.dir)
        self.assertEqual("preview", result["decision"]["status"])
        self.assertEqual(before, cl.strategy_hash(self.run.load()))

    def test_risky_learning_change_is_held_then_approved(self):
        self._latest_learning({
            "sample": 6, "recommendation": "想扩渠道",
            "changes": [{"param": "channels.add", "from": ["x"],
                         "to": ["x", "email"], "detail": "加 email"}]})
        first = self.run.decide(action="optimize", by="roy",
                                on="2026-11-02", data_dir=self.dir)
        held = first["decision"]["held"]
        self.assertEqual(1, len(held))
        self.assertEqual("REVIEW_REQUIRED", held[0]["reason_code"])
        self.assertEqual(["x"], self.run.load()["channels"])
        record = self.run.approve_change(self.dir, held[0]["change_sha256"],
                                         by="roy", on="2026-11-02")
        self.assertEqual(held[0]["change_sha256"], record["change_sha256"])
        second = self.run.decide(action="optimize", by="roy",
                                 on="2026-11-02", data_dir=self.dir)
        self.assertEqual([], second["decision"]["held"])
        self.assertEqual(["x", "email"], self.run.load()["channels"])

    def test_rollback_rebuilds_briefs_and_keeps_ledger(self):
        Ledger(self.dir / "ledger.jsonl").append(
            channel="x", action="reply", target="tweet:1",
            result="committed", campaign="g1", ts="2026-11-03T09:00:00",
            ref="sent-1")
        ledger_before = (self.dir / "ledger.jsonl").read_text(encoding="utf-8")
        self._latest_learning({
            "sample": 6, "recommendation": "倾斜 A",
            "changes": [{"param": "content.mix_weights", "from": "current",
                         "to": {"boost": "reply.agree_extend_ask",
                                "reduce": "reply.contrast_boundary"},
                         "detail": "A 组更好"}]})
        self.run.decide(action="optimize", by="roy", on="2026-11-02",
                        data_dir=self.dir)
        data = self.run.load()
        briefs = data["supply"]["briefs"]
        boosted = data["content"]["formula_weights"]
        result = self.run.rollback_changes(self.dir, by="roy",
                                           on="2026-11-03")
        self.assertTrue(result["rolled_back"])
        data = self.run.load()
        self.assertEqual(ledger_before,
                         (self.dir / "ledger.jsonl").read_text(encoding="utf-8"))
        self.assertNotEqual(boosted, data["content"]["formula_weights"])
        for brief in data["supply"]["briefs"]:
            total = sum(row["count"] for row in brief.get("formulas") or [])
            self.assertEqual(brief["count_per_week"], total)

    def test_second_default_rollback_does_not_undo_older_change(self):
        self._latest_learning({
            "sample": 6, "recommendation": "换受众",
            "changes": [{"param": "target.audience", "from": "us-west",
                         "to": "us-east", "detail": "换受众"}]})
        self.run.decide(action="optimize", by="roy", on="2026-11-02",
                        data_dir=self.dir)
        first = self.run.rollback_changes(self.dir, by="roy",
                                          on="2026-11-03")
        self.assertTrue(first["rolled_back"])
        second = self.run.rollback_changes(self.dir, by="roy",
                                           on="2026-11-04")
        self.assertFalse(second["rolled_back"])
        self.assertEqual(2, len(self.run.load()["strategy_changes"]))

    def test_default_rollback_refuses_after_auto_safety_action(self):
        data = run_data()
        cl.record_auto_reduce(data, param="actions_per_week", from_value=7,
                              to_value=4, reason="账号健康 40", on="2026-11-05")
        result = cl.rollback_changes(data, by="roy")
        self.assertFalse(result["rolled_back"])
        self.assertEqual(4, data["plan"]["actions_per_week"])

    def test_list_changes_reports_hashes(self):
        self._latest_learning({
            "sample": 6, "recommendation": "换受众",
            "changes": [{"param": "target.audience", "from": "us-west",
                         "to": "us-east", "detail": "换受众"}]})
        self.run.decide(action="optimize", by="roy", on="2026-11-02",
                        data_dir=self.dir)
        listing = self.run.list_changes()
        self.assertTrue(listing["current_hash"])
        self.assertEqual(1, len(listing["changes"]))
        self.assertEqual(1, len(listing["applied_evidence"]))


class FalsificationRenderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def test_render_marks_unknown_not_zero(self):
        metrics = cl.falsification_metrics(
            self.dir, run_id="g1",
            window={"start": "2026-11-01", "end": "2026-11-07"})
        text = cl.render_falsification(metrics)
        self.assertIn("取不到（unknown）", text)
        self.assertIn("不按粉丝数、发送数或未经确认的潜客数收费", text)
        self.assertEqual(0, metrics["approved_actions"])

    def test_human_minutes_from_run_telemetry(self):
        grow = self.dir / "grow"
        grow.mkdir(parents=True)
        (grow / "g1.json").write_text(json.dumps({
            "id": "g1",
            "telemetry": {"weeks": {"2026-11-01": {"human_minutes": 25}}}},
            ensure_ascii=False), encoding="utf-8")
        metrics = cl.falsification_metrics(
            self.dir, run_id="g1",
            window={"start": "2026-11-01", "end": "2026-11-07"})
        self.assertEqual(25, metrics["human_minutes"])

    def test_unknown_product_source_keeps_layers_unknown(self):
        metrics = cl.falsification_metrics(
            self.dir, run_id="g1",
            window={"start": "2026-11-01", "end": "2026-11-07"})
        self.assertIsNone(metrics["independently_observed_activations"])
        self.assertIsNone(metrics["meaningful_responses"])
        self.assertIn("independently_observed_activations", metrics["unknowns"])


class CliChangeFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / "data"
        self.home.mkdir(parents=True, exist_ok=True)
        self.env = dict(os.environ, MKT_HOME=str(self.home))
        self.facts = facts(Path(self.tmp.name))

    def run_mkt(self, *args, check=True):
        proc = subprocess.run([sys.executable, str(MKT), *args],
                              cwd=str(ROOT), env=self.env, text=True,
                              capture_output=True)
        if check and proc.returncode:
            self.fail(f"mkt {' '.join(args)} failed ({proc.returncode})"
                      f"\nSTDOUT:{proc.stdout}\nSTDERR:{proc.stderr}")
        return proc

    def _launch(self):
        authorize(self.home, by="tester",
                  actions=["x:reply", "email:message"], days=90)
        self.run_mkt("grow", "launch", "g1", "--facts", str(self.facts),
                     "--goal", "awareness.followers", "--target", "20",
                     "--channels", "x", "--handle", "verovo", "--skip-probe")

    def test_change_list_and_rollback_cli(self):
        self._launch()
        learning = self.home / "grow" / "g1" / "learning"
        learning.mkdir(parents=True, exist_ok=True)
        (learning / "latest.json").write_text(json.dumps({
            "sample": 6, "recommendation": "换受众",
            "changes": [{"param": "target.audience", "from": "us-west",
                         "to": "us-east", "detail": "换受众"}]},
            ensure_ascii=False), encoding="utf-8")
        self.run_mkt("grow", "decide", "g1", "--action", "optimize",
                     "--by", "roy", "--on", "2026-11-02")
        listing = json.loads(self.run_mkt("grow", "change", "list", "g1",
                                          "--json").stdout)
        self.assertEqual(1, len(listing["changes"]))
        dry = self.run_mkt("grow", "change", "rollback", "g1", "--by", "roy",
                           "--on", "2026-11-03")
        self.assertIn("预演", dry.stdout)
        listing = json.loads(self.run_mkt("grow", "change", "list", "g1",
                                          "--json").stdout)
        self.assertEqual(1, len(listing["changes"]))
        applied = self.run_mkt("grow", "change", "rollback", "g1", "--by",
                               "roy", "--on", "2026-11-03", "--apply")
        self.assertIn("已回滚", applied.stdout)
        listing = json.loads(self.run_mkt("grow", "change", "list", "g1",
                                          "--json").stdout)
        self.assertEqual(2, len(listing["changes"]))
        self.assertEqual("rollback", listing["changes"][-1]["kind"])

    def test_change_approve_cli_unlocks_risky_change(self):
        self._launch()
        learning = self.home / "grow" / "g1" / "learning"
        learning.mkdir(parents=True, exist_ok=True)
        (learning / "latest.json").write_text(json.dumps({
            "sample": 6, "recommendation": "扩渠道",
            "changes": [{"param": "channels.add", "from": ["x"],
                         "to": ["x", "email"], "detail": "加 email"}]},
            ensure_ascii=False), encoding="utf-8")
        held = self.run_mkt("grow", "decide", "g1", "--action", "optimize",
                            "--by", "roy", "--on", "2026-11-02", "--json")
        payload = json.loads(held.stdout)
        sha = payload["decision"]["held"][0]["change_sha256"]
        self.run_mkt("grow", "change", "approve", "g1", "--change", sha,
                     "--by", "roy", "--apply")
        self.run_mkt("grow", "decide", "g1", "--action", "optimize",
                     "--by", "roy", "--on", "2026-11-02")
        data = json.loads((self.home / "grow" / "g1.json").read_text(
            encoding="utf-8"))
        self.assertEqual(["x", "email"], data["channels"])

    def test_change_approve_requires_signature(self):
        self._launch()
        proc = self.run_mkt("grow", "change", "approve", "g1",
                            "--change", "sha256:deadbeef", "--by", "",
                            check=False)
        self.assertEqual(2, proc.returncode)


class CliExperimentFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / "data"
        self.home.mkdir(parents=True, exist_ok=True)
        self.env = dict(os.environ, MKT_HOME=str(self.home))

    def run_mkt(self, *args, check=True):
        proc = subprocess.run([sys.executable, str(MKT), *args],
                              cwd=str(ROOT), env=self.env, text=True,
                              capture_output=True)
        if check and proc.returncode:
            self.fail(f"mkt {' '.join(args)} failed ({proc.returncode})"
                      f"\nSTDOUT:{proc.stdout}\nSTDERR:{proc.stderr}")
        return proc

    def _design(self):
        return self.run_mkt(
            "grow", "experiment", "design", "--id", "g4-exp",
            "--hypothesis", "A 公式更好", "--segment", "开发者",
            "--mechanism", "content.mix_weights", "--baseline", "当前配比",
            "--metric", "m", "--guardrail", "不提高上限",
            "--start", "2026-11-01", "--end", "2026-11-14",
            "--min-sample", "3", "--limitations", "相关不是因果",
            "--stop-conditions", "零互动", "--apply")

    def test_approve_start_observe_dedupes_evidence(self):
        self._design()
        self.run_mkt("grow", "experiment", "approve", "g4-exp",
                     "--by", "roy", "--apply")
        self.run_mkt("grow", "experiment", "start", "g4-exp", "--apply")
        first = self.run_mkt("grow", "experiment", "observe", "g4-exp",
                             "--note", "第 1 条", "--value", "2",
                             "--evidence-ref", "ref-1", "--json")
        self.assertTrue(json.loads(first.stdout)["recorded"])
        again = self.run_mkt("grow", "experiment", "observe", "g4-exp",
                             "--note", "重复", "--value", "2",
                             "--evidence-ref", "ref-1", "--json")
        self.assertFalse(json.loads(again.stdout)["recorded"])
        analysis = json.loads(self.run_mkt(
            "grow", "experiment", "analyze", "g4-exp", "--json").stdout)
        self.assertEqual(1, analysis["sample"])
        self.assertEqual("exploration", analysis["analysis_mode"])

    def test_compare_and_metrics_cli(self):
        self._design()
        dry = self.run_mkt(
            "grow", "experiment", "compare", "g4-exp",
            "--product", "Verovo", "--channels", "x", "--start", "2026-11-01",
            "--end", "2026-11-28", "--cost-basis", "人工+工具")
        self.assertIn("预演", dry.stdout)
        stored = json.loads(
            (self.home / "experiments" / "g4-exp.json").read_text(
                encoding="utf-8"))
        self.assertNotIn("comparison", stored)
        applied = self.run_mkt(
            "grow", "experiment", "compare", "g4-exp",
            "--product", "Verovo", "--channels", "x", "--start", "2026-11-01",
            "--end", "2026-11-28", "--cost-basis", "人工+工具", "--apply")
        self.assertIn("已冻结", applied.stdout)
        metrics = json.loads(self.run_mkt(
            "grow", "experiment", "metrics", "--run", "g1",
            "--start", "2026-11-01", "--end", "2026-11-07", "--json").stdout)
        self.assertIn("total_execution_cost", metrics["unknowns"])
        text = self.run_mkt("grow", "experiment", "metrics", "--run", "g1",
                            "--start", "2026-11-01", "--end", "2026-11-07").stdout
        self.assertIn("取不到（unknown）", text)

    def test_start_without_approval_fails(self):
        self._design()
        proc = self.run_mkt("grow", "experiment", "start", "g4-exp",
                            "--apply", check=False)
        self.assertEqual(2, proc.returncode)
        self.assertIn("REVIEW_REQUIRED", proc.stdout)


if __name__ == "__main__":
    unittest.main()
