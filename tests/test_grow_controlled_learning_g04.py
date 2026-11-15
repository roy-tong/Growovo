"""G-04 验收冻结：受控学习与策略实验（P1，权限工单）。

先于实现编写。冻结四组语义：

1. `GrowthExperiment` 对象齐全（PRD G-04 十一个字段），预注册后不得改，
   批准前不得开跑，同一 evidence 不得重复记样本；
2. 分析口径诚实：前后对照只能 `non_causal`，只有随机分组且样本达到声明
   门槛才允许实验因果口径；3 个样本只能是探索，不得宣称胜出；
3. 策略修改权限分级：Agent 只能建议；增加上限/扩渠道/换账号/解除 suppress/
   改自动发送范围必须重新人审；极端失败只允许自动减量或停机；
4. 回滚绑定前后策略 hash、同一 evidence 重复提交 no-op、撤销只恢复前一
   批准策略且不撤销已发外部动作。

全部离线：合成数据，零网络、零真实发送（工单 G-04，P1）。
"""
import json
import tempfile
import unittest
from pathlib import Path

from core import controlled_learning as cl
from core import outcome_layers
from core.execution_policy import authorize
from core.grow_v2 import GrowRunV2
from core.ledger import Ledger


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


def design_kwargs(**override):
    base = {
        "exp_id": "g4-mix-weights",
        "hypothesis": "同意+补充式回复比对比式更容易得到实质回应",
        "segment": "KOC 账号下的开发者帖子",
        "mechanism": "content.mix_weights",
        "control_or_baseline": "当前公式配比（两类各半）",
        "primary_metric": "meaningful_replies_per_post",
        "guardrail": "不提高单日发布上限；不解除任何 DNC",
        "window": {"start": "2026-11-01", "end": "2026-11-14"},
        "min_sample_declared": 20,
        "analysis_limitations": "公开指标是相关不是因果；单账号单窗口",
        "stop_conditions": ["连续 3 天零互动", "账号健康 < 60"],
        "decision_rule": "20 条有效样本且 guardrail 未触发后由人判定",
        "owner": "roy",
    }
    base.update(override)
    return base


def run_data(**override):
    """最小 run 状态（data/grow/<id>.json 的策略面）。"""
    data = {
        "id": "g1",
        "channels": ["x"],
        "actions": ["x:reply"],
        "plan": {"actions_per_week": 7},
        "quota": {"actions_per_week": 7,
                  "action_weights": {"reply": 1.0}},
        "guards": {"account_id": "acct-a", "account_health": {"score": 92}},
        "growth_strategy": {"koc_share_target": 0.5},
        "content": {"formula_weights": {"reply.a": 50, "reply.b": 50}},
        "supply": {"briefs": []},
        "targeting": {"audience": "us-west", "queries": ["ai invoice"]},
        "automation": {"enabled": False},
        "status": "active",
        "go": True,
        "adjustments": [],
        "strategy_changes": [],
        "change_approvals": [],
        "applied_evidence": [],
    }
    data.update(override)
    return data


class ExperimentObjectTests(unittest.TestCase):
    """PRD G-04 实验对象：字段齐全、预注册、批准、样本幂等。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def test_design_requires_every_declared_field(self):
        required = ("hypothesis", "segment", "mechanism", "control_or_baseline",
                    "primary_metric", "guardrail", "window",
                    "min_sample_declared", "analysis_limitations",
                    "stop_conditions")
        for field in required:
            with self.subTest(field=field):
                kwargs = design_kwargs(**{field: ""})
                with self.assertRaises(cl.ControlledLearningError):
                    cl.design_experiment(self.dir, **kwargs)
        # 反例说明前面不是「什么都拒」：完整字段可以建。
        exp = cl.design_experiment(self.dir, **design_kwargs())
        self.assertEqual("planned", exp["status"])
        self.assertEqual(20, exp["min_sample_declared"])
        self.assertTrue(exp["plan_sha256"])
        for field in required:
            self.assertTrue(exp.get(field), field)

    def test_sample_and_window_validation(self):
        with self.assertRaises(cl.ControlledLearningError):
            cl.design_experiment(
                self.dir, **design_kwargs(min_sample_declared=0))
        with self.assertRaises(cl.ControlledLearningError):
            cl.design_experiment(
                self.dir, **design_kwargs(window={"start": "2026-11-14",
                                                  "end": "2026-11-01"}))
        with self.assertRaises(cl.ControlledLearningError):
            cl.design_experiment(
                self.dir, **design_kwargs(randomized=True,
                                          assignment_ref=""))
        exp = cl.design_experiment(
            self.dir, **design_kwargs(exp_id="g4-rnd", randomized=True,
                                      assignment_ref="assignments/a.csv"))
        self.assertTrue(exp["randomized"])
        self.assertEqual("assignments/a.csv", exp["assignment_ref"])

    def test_approval_required_before_start(self):
        cl.design_experiment(self.dir, **design_kwargs())
        with self.assertRaises(cl.ControlledLearningError) as ctx:
            cl.start_experiment(self.dir, "g4-mix-weights")
        self.assertIn("REVIEW_REQUIRED", str(ctx.exception))
        with self.assertRaises(cl.ControlledLearningError):
            cl.approve_experiment(self.dir, "g4-mix-weights", by="")
        exp = cl.approve_experiment(self.dir, "g4-mix-weights", by="roy",
                                    note="预注册通过")
        self.assertEqual("roy", exp["approval"]["by"])
        self.assertEqual(exp["plan_sha256"], exp["approval"]["plan_sha256"])
        started = cl.start_experiment(self.dir, "g4-mix-weights")
        self.assertEqual("running", started["status"])

    def test_tampering_after_start_is_rejected(self):
        cl.design_experiment(self.dir, **design_kwargs())
        cl.approve_experiment(self.dir, "g4-mix-weights", by="roy")
        cl.start_experiment(self.dir, "g4-mix-weights")
        path = self.dir / "experiments" / "g4-mix-weights.json"
        doc = json.loads(path.read_text(encoding="utf-8"))
        doc["segment"] = "事后改的目标人群"
        path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
        with self.assertRaises(cl.ControlledLearningError):
            cl.analyze_experiment(self.dir, "g4-mix-weights")

    def test_same_evidence_observation_counts_once(self):
        cl.design_experiment(self.dir, **design_kwargs())
        cl.approve_experiment(self.dir, "g4-mix-weights", by="roy")
        cl.start_experiment(self.dir, "g4-mix-weights")
        first = cl.record_observation(
            self.dir, "g4-mix-weights", note="第 1 天",
            value=3, evidence_ref="ledger:ref-1")
        self.assertTrue(first["recorded"])
        self.assertEqual(1, first["sample"])
        again = cl.record_observation(
            self.dir, "g4-mix-weights", note="同一份证据又提一次",
            value=3, evidence_ref="ledger:ref-1")
        self.assertFalse(again["recorded"])
        self.assertEqual("duplicate_evidence", again["reason"])
        self.assertEqual(1, again["sample"])
        exp = cl.load_experiment(self.dir, "g4-mix-weights")
        self.assertEqual(1, len(exp["observations"]))

    def test_observation_requires_evidence_or_approximation(self):
        cl.design_experiment(self.dir, **design_kwargs())
        cl.approve_experiment(self.dir, "g4-mix-weights", by="roy")
        cl.start_experiment(self.dir, "g4-mix-weights")
        with self.assertRaises(cl.ControlledLearningError):
            cl.record_observation(self.dir, "g4-mix-weights",
                                  note="无出处", value=3)
        ok = cl.record_observation(self.dir, "g4-mix-weights",
                                   note="近似计数", value=3, approximated=True)
        self.assertTrue(ok["recorded"])


class AnalysisModeTests(unittest.TestCase):
    """分析口径：no_data / exploration / causal_allowed，禁止越级宣称。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def _started(self, **override):
        cl.design_experiment(self.dir, **design_kwargs(**override))
        exp_id = override.get("exp_id", "g4-mix-weights")
        cl.approve_experiment(self.dir, exp_id, by="roy")
        cl.start_experiment(self.dir, exp_id)
        return exp_id

    def test_no_observations_is_no_data_not_zero_result(self):
        exp_id = self._started()
        analysis = cl.analyze_experiment(self.dir, exp_id)
        self.assertEqual("no_data", analysis["analysis_mode"])
        self.assertFalse(analysis["can_conclude"])
        self.assertFalse(analysis["can_claim_causality"])
        self.assertFalse(analysis["can_claim_winner"])

    def test_three_samples_are_exploration_only(self):
        exp_id = self._started(min_sample_declared=20)
        for i in range(3):
            cl.record_observation(self.dir, exp_id, note=f"第 {i + 1} 条",
                                  value=2, evidence_ref=f"ref-{i}")
        analysis = cl.analyze_experiment(self.dir, exp_id)
        self.assertEqual("exploration", analysis["analysis_mode"])
        self.assertEqual(3, analysis["sample"])
        self.assertFalse(analysis["can_claim_causality"])
        self.assertFalse(analysis["can_claim_winner"])
        self.assertIn("探索", analysis["wording"])
        self.assertIn("不能宣称", analysis["wording"])

    def test_randomized_with_declared_sample_allows_causal_wording(self):
        exp_id = self._started(exp_id="g4-rnd", randomized=True,
                               assignment_ref="assignments/a.csv",
                               min_sample_declared=3)
        for i in range(3):
            cl.record_observation(self.dir, exp_id, note=f"第 {i + 1} 条",
                                  value=2, evidence_ref=f"ref-{i}")
        analysis = cl.analyze_experiment(self.dir, exp_id)
        self.assertEqual("causal_allowed", analysis["analysis_mode"])
        self.assertTrue(analysis["can_claim_causality"])
        self.assertIn("随机", analysis["wording"])

    def test_randomized_below_declared_sample_stays_exploration(self):
        exp_id = self._started(exp_id="g4-rnd", randomized=True,
                               assignment_ref="assignments/a.csv",
                               min_sample_declared=20)
        for i in range(5):
            cl.record_observation(self.dir, exp_id, note=f"第 {i + 1} 条",
                                  value=2, evidence_ref=f"ref-{i}")
        analysis = cl.analyze_experiment(self.dir, exp_id)
        self.assertEqual("exploration", analysis["analysis_mode"])
        self.assertFalse(analysis["can_claim_causality"])
        self.assertFalse(analysis["can_conclude"])

    def test_declared_minimum_reached_but_not_randomized_stays_non_causal(self):
        exp_id = self._started(min_sample_declared=5)
        for i in range(5):
            cl.record_observation(self.dir, exp_id, note=f"第 {i + 1} 条",
                                  value=2, evidence_ref=f"ref-{i}")
        analysis = cl.analyze_experiment(self.dir, exp_id)
        self.assertEqual("exploration", analysis["analysis_mode"])
        self.assertTrue(analysis["can_conclude"])
        self.assertFalse(analysis["can_claim_causality"])


class PermissionTierTests(unittest.TestCase):
    """PRD 分级：Agent 只建议；升权必须重新人审；极端失败只许减量/停机。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def test_classify_agent_suggestible_changes(self):
        suggestible = (
            {"param": "target.audience", "from": "us-west", "to": "us-east"},
            {"param": "target.queries", "from": ["a"], "to": ["a", "b"]},
            {"param": "target.koc_share", "from": 0.5, "to": 0.7},
            {"param": "content.mix_weights", "from": "current",
             "to": {"boost": "reply.a", "reduce": "reply.b"}},
            {"param": "content.formula", "from": "reply.a", "to": "reply.c"},
            {"param": "actions_per_week", "from": 7, "to": 3},
        )
        for change in suggestible:
            with self.subTest(param=change["param"]):
                info = cl.classify_change(change)
                self.assertEqual("suggest", info["tier"])
                self.assertFalse(info["requires_human_reapproval"])

    def test_classify_reapproval_required_changes(self):
        risky = (
            {"param": "actions_per_week", "from": 3, "to": 7},
            {"param": "quota.actions_per_week", "from": 3, "to": 7},
            {"param": "quota.max_per_day", "from": 1, "to": 3},
            {"param": "channels.add", "from": ["x"], "to": ["x", "email"]},
            {"param": "account", "from": "acct-a", "to": "acct-b"},
            {"param": "suppression.lift", "from": "dnc@example.com", "to": ""},
            {"param": "automation.scope", "from": "koc", "to": "all"},
            {"param": "unknown.param", "from": 1, "to": 2},
        )
        for change in risky:
            with self.subTest(param=change["param"]):
                info = cl.classify_change(change)
                self.assertEqual("reapprove", info["tier"])
                self.assertTrue(info["requires_human_reapproval"])

    def test_stop_is_auto_safe_but_increase_is_not(self):
        self.assertEqual("auto_safe",
                         cl.classify_change({"param": "status",
                                             "to": "paused"})["tier"])
        self.assertEqual("auto_safe",
                         cl.classify_change({"param": "stop",
                                             "reason": "连续零互动"})["tier"])
        self.assertEqual("reapprove",
                         cl.classify_change({"param": "actions_per_week",
                                             "from": 7, "to": 14})["tier"])

    def test_agent_can_only_suggest_never_mutate(self):
        data = run_data()
        before = cl.strategy_hash(data)
        change = {"param": "target.audience", "from": "us-west",
                  "to": "us-east", "detail": "换受众试试"}
        outcome = cl.apply_changes(data, [change], actor="agent")
        self.assertEqual([], outcome["applied"])
        self.assertEqual(1, len(outcome["held"]))
        self.assertTrue(outcome["suggestion_only"])
        self.assertEqual("human", outcome["next_actor"])
        self.assertEqual(before, cl.strategy_hash(data))
        self.assertEqual("us-west", data["targeting"]["audience"])
        self.assertEqual([], data["strategy_changes"])

    def test_agent_auto_mode_only_reduces_on_extreme_failure(self):
        data = run_data()
        reduce = {"param": "actions_per_week", "from": 7, "to": 3,
                  "detail": "连续失败，先减量"}
        outcome = cl.apply_changes(data, [reduce], actor="agent",
                                   extreme_failure=True)
        self.assertEqual(1, len(outcome["applied"]))
        self.assertEqual(3, data["plan"]["actions_per_week"])
        increase = {"param": "actions_per_week", "from": 3, "to": 7}
        blocked = cl.apply_changes(data, [increase], actor="agent",
                                   extreme_failure=True)
        self.assertEqual([], blocked["applied"])
        self.assertEqual("POLICY_DENIED", blocked["held"][0]["reason_code"])
        self.assertEqual(3, data["plan"]["actions_per_week"])

    def test_agent_auto_mode_without_extreme_failure_is_denied(self):
        data = run_data()
        reduce = {"param": "actions_per_week", "from": 7, "to": 3}
        outcome = cl.apply_changes(data, [reduce], actor="agent")
        self.assertEqual([], outcome["applied"])
        self.assertTrue(outcome["held"])
        self.assertEqual(7, data["plan"]["actions_per_week"])

    def test_human_risky_change_needs_fresh_approval(self):
        data = run_data()
        change = {"param": "quota.max_per_day", "from": 1, "to": 3,
                  "detail": "想发更多"}
        outcome = cl.apply_changes(data, [change], actor="human", by="roy")
        self.assertEqual([], outcome["applied"])
        self.assertEqual("REVIEW_REQUIRED", outcome["held"][0]["reason_code"])
        self.assertEqual("needs_review", outcome["status"])
        self.assertNotIn("max_per_day", data["quota"])

    def test_approval_binds_change_and_strategy_hash(self):
        data = run_data()
        change = {"param": "quota.max_per_day", "from": 1, "to": 3}
        before = cl.strategy_hash(data)
        approval = cl.approve_change(data, change, by="roy", note="同意加一条")
        self.assertEqual(before, approval["before_hash"])
        outcome = cl.apply_changes(data, [change], actor="human", by="roy",
                                   approval=approval)
        self.assertEqual(1, len(outcome["applied"]))
        self.assertEqual(3, data["quota"]["max_per_day"])
        record = data["strategy_changes"][-1]
        self.assertEqual(before, record["before_hash"])
        self.assertNotEqual(before, record["after_hash"])
        self.assertEqual("roy", record["approved_by"])

    def test_approval_for_another_change_does_not_work(self):
        data = run_data()
        approved = {"param": "quota.max_per_day", "from": 1, "to": 3}
        approval = cl.approve_change(data, approved, by="roy")
        other = {"param": "channels.add", "from": ["x"], "to": ["x", "email"]}
        outcome = cl.apply_changes(data, [other], actor="human",
                                   approval=approval)
        self.assertEqual([], outcome["applied"])
        self.assertEqual("REVIEW_REQUIRED", outcome["held"][0]["reason_code"])
        self.assertEqual(["x"], data["channels"])

    def test_approval_goes_stale_after_strategy_changes(self):
        data = run_data()
        risky = {"param": "quota.max_per_day", "from": 1, "to": 3}
        approval = cl.approve_change(data, risky, by="roy")
        cl.apply_changes(data, [{"param": "target.audience",
                                 "from": "us-west", "to": "us-east"}],
                         actor="human", by="roy")
        outcome = cl.apply_changes(data, [risky], actor="human",
                                   approval=approval)
        self.assertEqual([], outcome["applied"])
        self.assertEqual("REVIEW_REQUIRED", outcome["held"][0]["reason_code"])
        self.assertNotIn("max_per_day", data["quota"])

    def test_suppression_is_never_lifted_by_learning(self):
        data = run_data()
        change = {"param": "suppression.lift", "from": "dnc@example.com",
                  "to": "", "detail": "想再联系一次"}
        approval = cl.approve_change(data, change, by="roy")
        outcome = cl.apply_changes(data, [change], actor="human",
                                   approval=approval)
        self.assertEqual([], outcome["applied"])
        self.assertTrue(outcome["manual_actions"])
        for path in ("contacts.jsonl", "suppress.txt"):
            self.assertFalse((self.dir / path).exists())
        self.assertNotIn("suppression_scope", data)


class EvidenceIdempotencyTests(unittest.TestCase):
    """同一 evidence 重复提交 no-op：不多升一次权、不多记一次样本。"""

    def test_same_evidence_twice_is_no_op(self):
        data = run_data()
        change = {"param": "target.audience", "from": "us-west",
                  "to": "us-east"}
        first = cl.apply_changes(data, [change], actor="human", by="roy",
                                 evidence_hash="sha256:ev1")
        self.assertEqual(1, len(first["applied"]))
        second = cl.apply_changes(data, [change], actor="human", by="roy",
                                  evidence_hash="sha256:ev1")
        self.assertTrue(second["no_op"])
        self.assertEqual("evidence_already_applied", second["reason"])
        self.assertEqual(1, len(data["strategy_changes"]))
        self.assertEqual(1, len([a for a in data["adjustments"]
                                 if a.get("param") == "target.audience"]))

    def test_different_evidence_applies_again(self):
        data = run_data()
        change = {"param": "target.audience", "from": "us-west",
                  "to": "us-east"}
        cl.apply_changes(data, [change], actor="human", evidence_hash="ev1")
        second = cl.apply_changes(data, [change], actor="human",
                                  evidence_hash="ev2")
        self.assertEqual(1, len(second["applied"]))
        self.assertEqual(2, len(data["strategy_changes"]))


class RollbackTests(unittest.TestCase):
    """回滚绑定前后 hash；只恢复前一批准策略；不撤销已发外部动作。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def test_rollback_restores_previous_hash_and_quota(self):
        data = run_data()
        before = cl.strategy_hash(data)
        cl.apply_changes(data, [{"param": "actions_per_week", "from": 7,
                                 "to": 3, "detail": "供给不足"}],
                         actor="human", by="roy", evidence_hash="ev-q")
        self.assertNotEqual(before, cl.strategy_hash(data))
        result = cl.rollback_changes(data, by="roy", note="供给补上了")
        self.assertTrue(result["rolled_back"])
        self.assertEqual(before, result["restored_hash"])
        self.assertEqual(before, cl.strategy_hash(data))
        self.assertEqual(7, data["plan"]["actions_per_week"])
        self.assertEqual(7, data["quota"]["actions_per_week"])
        rollback = data["strategy_changes"][-1]
        self.assertEqual("rollback", rollback["kind"])
        self.assertEqual(result["record"]["change_id"], rollback["change_id"])
        self.assertTrue(rollback["reverses"])
        self.assertTrue(result["external_actions_unchanged"])
        self.assertEqual(2, len(data["strategy_changes"]))  # 原记录不被删

    def test_rollback_mix_weights_restores_exact_hash(self):
        data = run_data()
        before = cl.strategy_hash(data)
        cl.apply_changes(data, [{
            "param": "content.mix_weights", "from": "current",
            "to": {"boost": "reply.a", "reduce": "reply.b"},
            "detail": "A 组更好"}], actor="human", by="roy")
        self.assertNotEqual(before, cl.strategy_hash(data))
        cl.rollback_changes(data, by="roy")
        self.assertEqual(before, cl.strategy_hash(data))
        self.assertEqual({"reply.a": 50, "reply.b": 50},
                         data["content"]["formula_weights"])

    def test_rollback_with_nothing_to_revert(self):
        data = run_data()
        result = cl.rollback_changes(data, by="roy")
        self.assertFalse(result["rolled_back"])
        self.assertEqual([], data["strategy_changes"])

    def test_rollback_does_not_delete_external_actions(self):
        data = run_data()
        ledger_path = self.dir / "ledger.jsonl"
        Ledger(ledger_path).append(channel="x", action="reply",
                                   target="tweet:1", result="committed",
                                   campaign="g1", ts="2026-11-03T09:00:00",
                                   ref="sent-1")
        before_rows = ledger_path.read_text(encoding="utf-8")
        cl.apply_changes(data, [{"param": "target.audience",
                                 "from": "us-west", "to": "us-east"}],
                         actor="human", evidence_hash="ev-a")
        cl.rollback_changes(data, by="roy")
        self.assertEqual(before_rows,
                         ledger_path.read_text(encoding="utf-8"))
        self.assertEqual(1, len(Ledger(ledger_path).all()))


class ReviewTriggerTests(unittest.TestCase):
    """3 样本只探索；爆量/零互动触发复审；强反面反馈只建议停止。"""

    @staticmethod
    def _rows(views, replies):
        return [{"target": f"t{i}", "views": v, "replies": r,
                 "formula": "reply.a"}
                for i, (v, r) in enumerate(zip(views, replies))]

    def test_three_samples_are_exploration_not_winner(self):
        rows = self._rows([100, 110, 90], [2, 1, 3])
        result = cl.assess(sample=3, rows=rows, changes=[{"param": "x"}],
                           randomized=False)
        self.assertEqual("exploration", result["analysis_mode"])
        self.assertTrue(result["can_optimize"])
        self.assertFalse(result["can_claim_winner"])
        self.assertIn("探索", result["wording"])

    def test_no_sample_disables_optimization(self):
        result = cl.assess(sample=0, rows=[], changes=[{"param": "x"}])
        self.assertEqual("no_data", result["analysis_mode"])
        self.assertFalse(result["can_optimize"])
        self.assertIn("没有样本", result["next_action"])

    def test_two_samples_are_insufficient(self):
        rows = self._rows([100, 100], [2, 3])
        result = cl.assess(sample=2, rows=rows, changes=[{"param": "x"}])
        self.assertEqual("insufficient", result["analysis_mode"])
        self.assertFalse(result["can_optimize"])

    def test_viral_spike_requires_review(self):
        rows = self._rows([100, 100, 1200], [2, 2, 20])
        result = cl.assess(sample=3, rows=rows, changes=[{"param": "x"}])
        self.assertTrue(result["review_required"])
        self.assertFalse(result["can_optimize"])
        self.assertIn("爆量", "；".join(result["review_reasons"]))

    def test_zero_interaction_requires_review(self):
        rows = self._rows([100, 100, 100], [0, 0, 0])
        result = cl.assess(sample=3, rows=rows, changes=[{"param": "x"}])
        self.assertTrue(result["review_required"])
        self.assertFalse(result["can_optimize"])
        self.assertIn("零互动", "；".join(result["review_reasons"]))

    def test_strong_negative_feedback_only_suggests_stop(self):
        rows = self._rows([100, 110, 90], [2, 1, 3])
        result = cl.assess(
            sample=6, rows=rows, changes=[],
            feedback=[{"sentiment": "negative", "strength": "strong",
                       "note": "对方明确说别再发"}])
        self.assertTrue(result["suggest_stop"])
        self.assertFalse(result["can_optimize"])
        joined = "；".join(result["must_not"])
        self.assertIn("DNC", joined)
        self.assertIn("额度", joined)
        self.assertEqual([], result["auto_changes"])


class FalsificationTests(unittest.TestCase):
    """§12.5：三种替代冻结比较 + 七项结果指标（缺项 unknown 不当 0）。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        cl.design_experiment(self.dir, **design_kwargs())
        cl.approve_experiment(self.dir, "g4-mix-weights", by="roy")

    def test_comparison_freezes_three_arms_and_context(self):
        comparison = cl.freeze_comparison(
            self.dir, "g4-mix-weights",
            product="Verovo", channels=["x"],
            quota={"actions_per_week": 7},
            window={"start": "2026-11-01", "end": "2026-11-28"},
            cost_basis="人工分钟 + 模型/工具成本，按可追溯金额归集")
        self.assertEqual(3, len(comparison["arms"]))
        self.assertEqual(
            ["manual_light_ops", "generic_agent", "growovo"],
            [arm["name"] for arm in comparison["arms"]])
        self.assertTrue(comparison["comparison_hash"])
        again = cl.freeze_comparison(
            self.dir, "g4-mix-weights",
            product="Verovo", channels=["x"],
            quota={"actions_per_week": 7},
            window={"start": "2026-11-01", "end": "2026-11-28"},
            cost_basis="人工分钟 + 模型/工具成本，按可追溯金额归集")
        self.assertEqual(comparison["comparison_hash"],
                         again["comparison_hash"])

    def test_comparison_requires_frozen_context(self):
        with self.assertRaises(cl.ControlledLearningError):
            cl.freeze_comparison(
                self.dir, "g4-mix-weights", product="", channels=["x"],
                quota={"actions_per_week": 7},
                window={"start": "2026-11-01", "end": "2026-11-28"},
                cost_basis="人工+工具")

    def test_metrics_unknown_stays_unknown(self):
        ledger = Ledger(self.dir / "ledger.jsonl")
        for i in range(2):
            ledger.append(channel="x", action="reply", target=f"tweet:{i}",
                          result="committed", campaign="g1",
                          ts="2026-11-03T09:00:00", ref=f"r{i}")
        ledger.append(channel="x", action="reply", target="tweet:old",
                      result="committed", campaign="g1",
                      ts="2026-10-01T09:00:00", ref="old")
        metrics = cl.falsification_metrics(
            self.dir, run_id="g1",
            window={"start": "2026-11-01", "end": "2026-11-07"})
        self.assertEqual(2, metrics["approved_actions"])
        self.assertIsNone(metrics["meaningful_responses"])
        self.assertIsNone(metrics["independently_observed_activations"])
        self.assertIsNone(metrics["total_execution_cost"])
        self.assertIsNone(metrics["policy_incidents"])
        self.assertIn("total_execution_cost", metrics["unknowns"])

    def test_metrics_read_g03_layers_when_available(self):
        path = self.dir / "events.json"
        path.write_text(json.dumps([
            {"event_id": "e-1", "event_type": "activation",
             "subject_id": "u1@example.com",
             "occurred_at": "2026-11-03T10:00:00",
             "source_system": "product_export", "source_ref": "app:1",
             "campaign_id": "g1",
             "evidence_hash": "sha256:" + __import__("hashlib").sha256(
                 json.dumps({"event_id": "e-1", "event_type": "activation",
                             "subject_id": "u1@example.com",
                             "source_system": "product_export",
                             "source_ref": "app:1", "amount": "",
                             "currency": "", "payment_ref": "",
                             "paid_at": ""}, sort_keys=True,
                            ensure_ascii=False,
                            separators=(",", ":")).encode()).hexdigest(),
             "verified_by": "db"},
            {"event_id": "e-2", "event_type": "meaningful_conversation",
             "subject_id": "u2@example.com",
             "occurred_at": "2026-11-04T10:00:00",
             "source_system": "product_export", "source_ref": "app:2",
             "campaign_id": "g1", "evidence_hash": "sha256:y",
             "verified_by": "human"},
        ], ensure_ascii=False), encoding="utf-8")
        outcome_layers.import_events(
            self.dir, path, allowed_sources={"product_export"},
            imported_by="roy")
        metrics = cl.falsification_metrics(
            self.dir, run_id="g1",
            window={"start": "2026-11-01", "end": "2026-11-07"})
        self.assertEqual(1, metrics["meaningful_responses"])
        self.assertEqual(1, metrics["independently_observed_activations"])
        self.assertEqual(2, metrics["unique_qualified_prospects"])


class GrowIntegrationTests(unittest.TestCase):
    """接到 grow v2 decide：未批准不升权、旧策略继续跑、撤销不动外部动作。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.facts = _facts(self.dir)
        authorize(self.dir, by="tester",
                  actions=["x:reply", "email:message"], days=90)
        self.path = self.dir / "grow" / "g1.json"
        GrowRunV2.launch(self.path, run_id="g1", facts_path=str(self.facts),
                         goal_id="awareness.followers", channels=["x"],
                         target=20, actions_per_week=7, started="2026-11-01",
                         handle="acct-a", data_dir=self.dir, skip_probe=True)
        self.run = GrowRunV2(self.path)
        self.run.arm()

    def _seed_committed(self, count=7, day="2026-11-03"):
        ledger = Ledger(self.dir / "ledger.jsonl")
        for i in range(count):
            ledger.append(channel="x", action="reply",
                          target=f"tweet:https://x.com/a/{i}",
                          result="committed", campaign="g1",
                          ts=f"{day}T09:00:00", ref=f"seed-{i}")

    def _queue(self, day: str, count: int) -> None:
        name = f"g1-{day.replace('-', '')}"
        path = self.dir / "queues" / f"{name}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        items = [{"id": f"{name}-{i}",
                  "target": f"tweet:https://x.com/a/{day}/{i}",
                  "body": f"第 {i} 条具体回复：先说结论再给细节。",
                  "scheduled_for": day} for i in range(count)]
        path.write_text(json.dumps({"campaign": name, "channel": "x",
                                    "action": "reply", "items": items},
                                   ensure_ascii=False), encoding="utf-8")

    def _seed_scale_up_proposal(self):
        for day in ("2026-11-03", "2026-11-04", "2026-11-05"):
            self._queue(day, 10)
        self._seed_committed()

    def test_scale_up_proposal_waits_for_human_approval(self):
        self._seed_scale_up_proposal()
        before = self.run.load()["plan"]["actions_per_week"]
        result = self.run.decide(action="optimize", note="完成率高",
                                 by="roy", on="2026-11-03",
                                 data_dir=self.dir)
        held = result["decision"].get("held") or []
        self.assertTrue(held, result)
        self.assertEqual("needs_review", result["decision"]["status"])
        self.assertEqual(before, self.run.load()["plan"]["actions_per_week"])
        self.assertEqual("active", self.run.load()["status"])
        self.assertTrue(held[0]["change_sha256"])

    def test_approved_scale_up_applies_and_records_hashes(self):
        self._seed_scale_up_proposal()
        before = self.run.load()["plan"]["actions_per_week"]
        first = self.run.decide(action="optimize", by="roy",
                                on="2026-11-03", data_dir=self.dir)
        sha = first["decision"]["held"][0]["change_sha256"]
        approval = self.run.approve_change(self.dir, sha, by="roy",
                                           on="2026-11-03")
        self.assertTrue(approval["change_sha256"])
        second = self.run.decide(action="optimize", by="roy",
                                 on="2026-11-03", data_dir=self.dir)
        self.assertEqual([], second["decision"]["held"])
        data = self.run.load()
        self.assertGreater(data["plan"]["actions_per_week"], before)
        record = data["strategy_changes"][-1]
        self.assertTrue(record["before_hash"])
        self.assertNotEqual(record["before_hash"], record["after_hash"])

    def test_learning_with_too_few_samples_is_held(self):
        learn_dir = self.dir / "grow" / "g1" / "learning"
        learn_dir.mkdir(parents=True)
        (learn_dir / "latest.json").write_text(json.dumps({
            "sample": 2,
            "recommendation": "2 条帖子看起来 A 公式更好",
            "changes": [{"param": "content.mix_weights", "from": "current",
                         "to": {"boost": "reply.a", "reduce": "reply.b"},
                         "rollback": "恢复原配比",
                         "detail": "样本不足但想调"}]}, ensure_ascii=False),
            encoding="utf-8")
        before = self.run.load()["content"]["formula_weights"]
        result = self.run.decide(action="optimize", by="roy",
                                 on="2026-11-02", data_dir=self.dir)
        self.assertEqual([], result["decision"]["changes"])
        self.assertTrue(result["decision"]["held"])
        self.assertEqual(before,
                         self.run.load()["content"]["formula_weights"])

    def test_learning_with_six_samples_applies_as_exploration(self):
        learn_dir = self.dir / "grow" / "g1" / "learning"
        learn_dir.mkdir(parents=True)
        (learn_dir / "latest.json").write_text(json.dumps({
            "sample": 6,
            "recommendation": "倾斜到 A",
            "changes": [{"param": "content.mix_weights", "from": "current",
                         "to": {"boost": "reply.agree_extend_ask",
                                "reduce": "reply.contrast_boundary"},
                         "rollback": "恢复原配比",
                         "detail": "A 组回复率更高"}]}, ensure_ascii=False),
            encoding="utf-8")
        result = self.run.decide(action="optimize", by="roy",
                                 on="2026-11-02", data_dir=self.dir)
        self.assertEqual(1, len(result["decision"]["changes"]))
        data = self.run.load()
        weights = data["content"]["formula_weights"]
        self.assertGreater(weights["reply.agree_extend_ask"],
                           weights["reply.contrast_boundary"])
        self.assertEqual(1, len(data["strategy_changes"]))

    def test_rollback_through_run_keeps_ledger(self):
        self._seed_committed(count=1)
        ledger_path = self.dir / "ledger.jsonl"
        before_rows = ledger_path.read_text(encoding="utf-8")
        self.run.decide(action="optimize", note="对齐供给", by="roy",
                        on="2026-11-03", data_dir=self.dir)
        result = self.run.rollback_changes(self.dir, by="roy",
                                           on="2026-11-03")
        self.assertTrue(result["rolled_back"])
        self.assertTrue(result["external_actions_unchanged"])
        self.assertEqual(before_rows, ledger_path.read_text(encoding="utf-8"))
        self.assertEqual(1, len(Ledger(ledger_path).all()))


class CliTests(unittest.TestCase):
    """CLI 默认 dry-run/只读；--apply 才落盘。"""

    def setUp(self):
        import os
        import subprocess
        self.subprocess = subprocess
        self.os = os
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(__file__).resolve().parents[1]
        self.home = Path(self.tmp.name) / "data"
        self.home.mkdir(parents=True, exist_ok=True)
        self.env = dict(os.environ, MKT_HOME=str(self.home))

    def run_mkt(self, *args, check=True):
        proc = self.subprocess.run(
            [__import__("sys").executable, str(self.root / "mkt.py"), *args],
            cwd=str(self.root), env=self.env, text=True, capture_output=True)
        if check and proc.returncode:
            self.fail(f"mkt {' '.join(args)} failed ({proc.returncode})"
                      f"\nSTDOUT:{proc.stdout}\nSTDERR:{proc.stderr}")
        return proc

    def test_experiment_design_is_dry_run_by_default(self):
        proc = self.run_mkt(
            "grow", "experiment", "design", "--id", "g4-mix",
            "--hypothesis", "A 公式更好", "--segment", "开发者",
            "--mechanism", "content.mix_weights", "--baseline", "当前配比",
            "--metric", "meaningful_replies_per_post",
            "--guardrail", "不提高上限", "--start", "2026-11-01",
            "--end", "2026-11-14", "--min-sample", "20",
            "--limitations", "相关不是因果", "--stop-conditions", "零互动")
        self.assertIn("预演", proc.stdout)
        self.assertFalse((self.home / "experiments" / "g4-mix.json").exists())
        self.run_mkt(
            "grow", "experiment", "design", "--id", "g4-mix",
            "--hypothesis", "A 公式更好", "--segment", "开发者",
            "--mechanism", "content.mix_weights", "--baseline", "当前配比",
            "--metric", "meaningful_replies_per_post",
            "--guardrail", "不提高上限", "--start", "2026-11-01",
            "--end", "2026-11-14", "--min-sample", "20",
            "--limitations", "相关不是因果", "--stop-conditions", "零互动",
            "--apply")
        self.assertTrue((self.home / "experiments" / "g4-mix.json").is_file())

    def test_experiment_analyze_is_read_only_json(self):
        self.run_mkt(
            "grow", "experiment", "design", "--id", "g4-mix",
            "--hypothesis", "A 公式更好", "--segment", "开发者",
            "--mechanism", "content.mix_weights", "--baseline", "当前配比",
            "--metric", "m", "--guardrail", "不提高上限",
            "--start", "2026-11-01", "--end", "2026-11-14",
            "--min-sample", "20", "--limitations", "相关不是因果",
            "--stop-conditions", "零互动", "--apply")
        proc = self.run_mkt("grow", "experiment", "analyze", "g4-mix",
                            "--json")
        payload = json.loads(proc.stdout)
        self.assertEqual("no_data", payload["analysis_mode"])
        proc = self.run_mkt("grow", "experiment", "list", "--json")
        self.assertIn("g4-mix", proc.stdout)


if __name__ == "__main__":
    unittest.main()
