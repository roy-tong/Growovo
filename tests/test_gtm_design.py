"""GTM 设计器：对象×目标×机制×渠道、约束与结果契约。"""
import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core import gtm_design  # noqa: E402

MKT = ROOT / "mkt.py"


def run(*args):
    return subprocess.run([sys.executable, str(MKT), *args], cwd=ROOT,
                          text=True, capture_output=True)


class ObjectAndObjectiveTests(unittest.TestCase):
    def test_content_account_awareness(self):
        plan = gtm_design.design("增长一个小红书摄影账号，目标是拿到目标粉丝而不是泛流量")
        self.assertEqual("content_account", plan["object"]["id"])
        self.assertEqual("awareness", plan["objective"]["id"])
        self.assertEqual("content_led", plan["selected_motion"])
        self.assertIn("播放量", plan["outcome_contract"]["not_success"][0])
        self.assertEqual("unverified", plan["outcome_contract"]["verification"]["level"])

    def test_hardware_revenue_needs_supply(self):
        plan = gtm_design.design("推广一款新上市的智能相机，目标预售订单和实际履约")
        self.assertEqual("hardware", plan["object"]["id"])
        self.assertEqual("revenue", plan["objective"]["id"])
        self.assertEqual("commerce_led", plan["selected_motion"])
        self.assertEqual("ready", plan["design_status"])
        self.assertFalse(plan["execution_readiness"]["ready"])
        gaps = {item["id"] for item in plan["execution_readiness"]["missing_assets"]}
        self.assertIn("supply", gaps)
        self.assertIn("预售订单数", plan["outcome_contract"]["not_success"])

    def test_oss_contribution_rejects_star_as_success(self):
        plan = gtm_design.design("让更多开发者用上我们的开源 CLI 并带来真实贡献")
        self.assertEqual("oss_project", plan["object"]["id"])
        self.assertEqual("contribution", plan["objective"]["id"])
        self.assertEqual("community_led", plan["selected_motion"])
        self.assertIn("Star 数", plan["outcome_contract"]["not_success"])

    def test_b2b_acquisition_uses_sales(self):
        plan = gtm_design.design("获取企业客户线索，建立合格 pipeline")
        self.assertEqual("b2b", plan["object"]["id"])
        self.assertEqual("acquisition", plan["objective"]["id"])
        self.assertEqual("sales_led", plan["selected_motion"])

    def test_explicit_object_and_objective_override_text(self):
        plan = gtm_design.design("做一个增长计划", object_id="hardware", objective_id="revenue")
        self.assertEqual("hardware", plan["object"]["id"])
        self.assertEqual(1.0, plan["object"]["confidence"])
        self.assertEqual("revenue", plan["objective"]["id"])

    def test_unknown_object_is_rejected(self):
        with self.assertRaises(gtm_design.GTMDesignError):
            gtm_design.design("做一个增长计划", object_id="spaceship")

    def test_missing_objective_uses_default_and_discloses_it(self):
        plan = gtm_design.design("增长一个 SaaS 产品")
        self.assertEqual("activation", plan["objective"]["id"])
        self.assertEqual("object_default", plan["objective"]["source"])
        self.assertTrue(any("暂定" in item for item in plan["unknowns"]))

    def test_low_confidence_object_is_flagged_not_hidden(self):
        plan = gtm_design.design("给线下门店做本地获客")
        self.assertEqual("hardware", plan["object"]["id"])
        self.assertTrue(any("置信度较低" in item for item in plan["unknowns"]))

    def test_low_signal_text_needs_clarification(self):
        plan = gtm_design.design("增长")
        self.assertEqual("needs_clarification", plan["status"])
        self.assertTrue(plan["unknowns"])


class ConstraintTests(unittest.TestCase):
    def test_authorized_channel_becomes_primary(self):
        plan = gtm_design.design(
            "增长一个小红书摄影账号",
            constraints={"authorized_channels": ["xiaohongshu"], "assets": ["content"],
                         "data_sources": ["platform_insights"],
                         "data_receipts": [{"source": "platform_insights", "connected": True,
                                            "window": "2026-10", "receipt_id": "r-1"}],
                         "region": "cn"},
        )
        self.assertEqual(["xiaohongshu"], [row["id"] for row in plan["channels"]["primary"]])
        self.assertTrue(plan["motions"][0]["executable"])
        self.assertEqual("verifiable", plan["outcome_contract"]["verification"]["level"])
        self.assertTrue(plan["measurement_readiness"]["ready"])

    def test_region_filters_out_global_platforms(self):
        plan = gtm_design.design(
            "增长一个小红书摄影账号",
            constraints={"authorized_channels": ["xiaohongshu"], "region": "cn"},
        )
        ids = {row["id"] for row in plan["channels"]["unauthorized"]}
        self.assertNotIn("github", ids)
        self.assertTrue(all(row["id"] in gtm_design.REGION_PLATFORMS["cn"]
                            for row in plan["channels"]["unauthorized"]))

    def test_no_data_source_keeps_business_result_unverified(self):
        plan = gtm_design.design("增长我们的 SaaS 产品，提高付费转化",
                                 constraints={"authorized_channels": ["seo"]})
        self.assertEqual("unverified", plan["outcome_contract"]["verification"]["level"])
        self.assertTrue(any("结果核验未就绪" in item for item in plan["unknowns"]))

    def test_budget_and_unknown_assets_are_validated(self):
        with self.assertRaises(gtm_design.GTMDesignError):
            gtm_design.normalize_constraints({"assets": ["magic"]})
        with self.assertRaises(gtm_design.GTMDesignError):
            gtm_design.normalize_constraints({"authorized_channels": ["nope"]})
        with self.assertRaises(gtm_design.GTMDesignError):
            gtm_design.normalize_constraints({"region": "mars"})
        with self.assertRaises(gtm_design.GTMDesignError):
            gtm_design.normalize_constraints({"data_sources": ["magic"]})


class ProposalTests(unittest.TestCase):
    def test_incompatible_motion_is_rejected_with_reason(self):
        plan = gtm_design.design(
            "增长一个小红书账号",
            proposal={"motions": [{"id": "sales_led", "why": "直接卖"}],
                      "channels": ["xiaohongshu"]},
        )
        rejected = {item["id"]: item["reason"] for item in plan["proposal"]["rejected"]}
        self.assertIn("sales_led", rejected)
        self.assertIn("不适用于", rejected["sales_led"])

    def test_unknown_channel_is_rejected(self):
        plan = gtm_design.design(
            "增长一个小红书账号",
            proposal={"motions": [{"id": "content_led"}], "channels": ["not-a-channel"]},
        )
        rejected = {item["id"]: item["reason"] for item in plan["proposal"]["rejected"]}
        self.assertEqual("未知渠道", rejected["not-a-channel"])

    def test_proposal_objective_overrides_text(self):
        plan = gtm_design.design(
            "增长一个小红书账号",
            proposal={"objective": "acquisition", "motions": [{"id": "content_led"}]},
        )
        self.assertEqual("acquisition", plan["objective"]["id"])
        self.assertEqual(1.0, plan["objective"]["confidence"])

    def test_proposed_channel_sorts_first(self):
        plan = gtm_design.design(
            "增长我们的开发者工具",
            proposal={"motions": [{"id": "content_led"}], "channels": ["github"]},
        )
        self.assertEqual("github", plan["channels"]["unauthorized"][0]["id"])
        self.assertTrue(plan["channels"]["unauthorized"][0]["proposed"])


class LintAndRenderTests(unittest.TestCase):
    def test_valid_plan_lints_clean(self):
        plan = gtm_design.design("增长一个小红书账号")
        self.assertEqual([], gtm_design.lint(plan))

    def test_tampered_plan_fails_lint(self):
        plan = gtm_design.design("增长一个小红书账号")
        plan["schema"] = "wrong"
        plan["channels"]["primary"] = [{"id": "a"}, {"id": "b"}]
        problems = gtm_design.lint(plan)
        self.assertTrue(any("schema" in item for item in problems))
        self.assertTrue(any("主渠道" in item for item in problems))

    def test_render_contains_contract_and_boundary(self):
        plan = gtm_design.design("增长一个小红书账号")
        text = gtm_design.render(plan)
        self.assertIn("结果契约", text)
        self.assertIn("不算成功", text)
        self.assertIn("不证明渠道有效", text)


class ReviewFixTests(unittest.TestCase):
    def test_explicit_objective_beats_proposal(self):
        plan = gtm_design.design(
            "增长我们的 SaaS，提高付费转化",
            objective_id="revenue",
            proposal={"objective": "awareness"},
        )
        self.assertEqual("revenue", plan["objective"]["id"])
        self.assertEqual("explicit", plan["objective"]["source"])

    def test_proposal_with_only_channels_keeps_inferred_motion(self):
        plan = gtm_design.design(
            "增长一个小红书摄影账号，目标是目标粉丝",
            proposal={"channels": ["xiaohongshu"]},
        )
        self.assertFalse(plan["proposal"]["has_motions"])
        self.assertEqual("content_led", plan["selected_motion"])
        self.assertNotEqual("blocked", plan["design_status"])

    def test_proposal_with_only_objective_keeps_inferred_motions(self):
        plan = gtm_design.design(
            "增长一个小红书摄影账号",
            proposal={"objective": "acquisition"},
        )
        self.assertEqual("acquisition", plan["objective"]["id"])
        self.assertTrue(plan["selected_motions"])
        self.assertNotEqual("blocked", plan["design_status"])

    def test_all_rejected_motions_block_the_design(self):
        plan = gtm_design.design(
            "增长一个小红书摄影账号",
            proposal={"motions": [{"id": "sales_led", "why": "直接卖"}]},
        )
        self.assertEqual("blocked", plan["design_status"])
        self.assertTrue(any("均不适用" in item for item in plan["unknowns"]))

    def test_three_readiness_states_are_independent(self):
        plan = gtm_design.design("增长一个小红书摄影账号")
        self.assertEqual("ready", plan["design_status"])
        self.assertFalse(plan["execution_readiness"]["ready"])
        self.assertEqual("unverified", plan["measurement_readiness"]["level"])
        self.assertFalse(plan["measurement_readiness"]["ready"])

    def test_revenue_verification_needs_connection_and_events(self):
        base = {"authorized_channels": ["taobao"], "assets": ["content", "supply", "purchase"]}
        declared = gtm_design.design(
            "推广智能硬件，目标预售订单",
            constraints={**base, "data_sources": ["order_system"]},
        )
        self.assertEqual("declared_only", declared["measurement_readiness"]["level"])
        connected = gtm_design.design(
            "推广智能硬件，目标预售订单",
            constraints={**base, "data_sources": ["order_system"],
                         "data_receipts": [{"source": "order_system", "connected": True}]},
        )
        self.assertEqual("declared_only", connected["measurement_readiness"]["level"])
        verified = gtm_design.design(
            "推广智能硬件，目标预售订单",
            constraints={**base, "data_sources": ["order_system"],
                         "data_receipts": [{"source": "order_system", "connected": True,
                                            "event": "order.paid", "window": "2026-10",
                                            "sample": 42, "receipt_id": "r-2"}]},
        )
        self.assertEqual("verifiable", verified["measurement_readiness"]["level"])
        self.assertTrue(verified["measurement_readiness"]["ready"])

    def test_boolean_flags_alone_do_not_make_results_verifiable(self):
        plan = gtm_design.design(
            "推广智能硬件，目标预售订单",
            constraints={"authorized_channels": ["taobao"],
                         "assets": ["content", "supply", "purchase"],
                         "data_sources": ["order_system"],
                         "data_connected": ["order_system"], "events_verified": True},
        )
        self.assertEqual("declared_only", plan["measurement_readiness"]["level"])
        self.assertFalse(plan["measurement_readiness"]["ready"])

    def test_hardware_combines_content_and_commerce(self):
        plan = gtm_design.design(
            "推广一款新上市的智能硬件，目标预售订单和实际履约",
            constraints={"authorized_channels": ["taobao", "xiaohongshu"],
                         "assets": ["content", "supply", "purchase"],
                         "data_sources": ["order_system"],
                         "data_connected": ["order_system"], "events_verified": True},
        )
        roles = {item["id"]: item["role"] for item in plan["selected_motions"]}
        self.assertEqual("primary", roles["commerce_led"])
        self.assertEqual("supporting", roles["content_led"])
        self.assertEqual("content_led", plan["combination"]["order"][0])

    def test_agent_fit_orders_motions_and_marks_hypotheses(self):
        plan = gtm_design.design(
            "增长我们的 SaaS 产品，提高激活",
            proposal={"motions": [
                {"id": "content_led", "why": "写教程", "fit": {"audience_fit": 0.3}},
                {"id": "community_led", "why": "社区答疑",
                 "fit": {"audience_fit": 0.9, "resource_fit": 0.7},
                 "evidence": "过去 4 周社区回答带来 12 次试用"},
            ]},
        )
        self.assertEqual("community_led", plan["selected_motion"])
        self.assertEqual("community_led", plan["combination"]["primary"])
        self.assertNotIn("community_led", plan["combination"]["evidence_gaps"])
        self.assertIn("content_led", plan["combination"]["evidence_gaps"])

    def test_handoff_refuses_blocked_design(self):
        plan = gtm_design.design(
            "增长一个小红书摄影账号",
            proposal={"motions": [{"id": "sales_led"}]},
        )
        with self.assertRaises(gtm_design.GTMDesignError):
            gtm_design.handoff(plan)

    def test_handoff_keeps_selected_recipe(self):
        plan = gtm_design.design(
            "增长我们的开发者工具，让开发者首次成功",
            constraints={"authorized_channels": ["github"],
                         "assets": ["content", "technical"],
                         "data_sources": ["product_events"]},
        )
        result = gtm_design.handoff(plan, product={"product_type": "devtool", "name": "X"})
        self.assertEqual("app_growth", result["workstream"]["motion"])
        self.assertEqual(result["execution_plan"]["design_sha256"],
                         result["workstream"]["design_ref"])
        self.assertTrue(result["execution_plan"]["instruction"])


class HandoffTests(unittest.TestCase):
    def test_handoff_without_product_returns_contract_only(self):
        plan = gtm_design.design("增长一个小红书摄影账号，目标是目标粉丝",
                                 constraints={"authorized_channels": ["xiaohongshu"],
                                              "assets": ["content"]})
        result = gtm_design.handoff(plan)
        self.assertIsNone(result["workstream"])
        self.assertEqual("awareness", result["goal_hint"]["goal_id"])
        self.assertTrue(any("未提供产品事实包" in item for item in result["notes"]))
        self.assertTrue(result["next_commands"])
        self.assertIn("不代表执行", result["boundary"])

    def test_handoff_with_product_builds_workstream(self):
        plan = gtm_design.design("增长我们的开发者工具，让开发者首次成功",
                                 constraints={"authorized_channels": ["github"],
                                              "assets": ["content", "technical"],
                                              "data_sources": ["product_events"]})
        result = gtm_design.handoff(plan, product={"product_type": "devtool", "name": "X"})
        self.assertTrue(result["workstream"]["validation"]["ok"])
        self.assertEqual("adoption", result["goal_hint"]["goal_id"])
        orders = result["workstream"]["workstream"]["work_orders"]
        self.assertGreaterEqual(len(orders), 2)
        self.assertEqual("app_growth", result["workstream"]["motion"])
        self.assertEqual(result["execution_plan"]["design_sha256"],
                         result["workstream"]["design_ref"])

    def test_inconsistent_design_refuses_handoff(self):
        plan = gtm_design.design("增长一个小红书账号")
        plan["schema"] = "wrong"
        with self.assertRaises(gtm_design.GTMDesignError):
            gtm_design.handoff(plan)

    def test_render_handoff_mentions_mapping(self):
        plan = gtm_design.design("增长一个小红书账号，目标是目标粉丝")
        text = gtm_design.render_handoff(gtm_design.handoff(plan))
        self.assertIn("目标契约映射", text)
        self.assertIn("下一步", text)


class CliTests(unittest.TestCase):
    def test_design_json(self):
        proc = run("gtm", "design", "增长一个小红书摄影账号", "--json")
        self.assertEqual(0, proc.returncode, proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertEqual("content_account", payload["object"]["id"])

    def test_taxonomy_json(self):
        proc = run("gtm", "taxonomy", "--json")
        self.assertEqual(0, proc.returncode, proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertEqual(6, len(payload["objects"]))
        self.assertEqual(6, len(payload["motions"]))

    def test_lint_command_exit_codes(self):
        plan = gtm_design.design("增长一个小红书账号")
        good = ROOT / "tests" / "_tmp_gtm_design.json"
        good.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
        try:
            proc = run("gtm", "lint", str(good))
            self.assertEqual(0, proc.returncode, proc.stdout)
            plan["schema"] = "wrong"
            good.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
            proc = run("gtm", "lint", str(good))
            self.assertEqual(1, proc.returncode)
        finally:
            good.unlink(missing_ok=True)

    def test_design_save_then_handoff(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            design_path = Path(tmp) / "design.json"
            product_path = Path(tmp) / "product.json"
            product_path.write_text(json.dumps({"product_type": "devtool", "name": "X"}),
                                    encoding="utf-8")
            proc = run("gtm", "design", "增长我们的开发者工具，让开发者首次成功",
                       "--constraints", '{"authorized_channels":["github"],"assets":["content","technical"]}',
                       "--save", str(design_path))
            self.assertEqual(0, proc.returncode, proc.stderr)
            self.assertTrue(design_path.is_file())
            proc = run("gtm", "handoff", str(design_path), "--product", str(product_path), "--json")
            self.assertEqual(0, proc.returncode, proc.stderr)
            payload = json.loads(proc.stdout)
            self.assertTrue(payload["workstream"]["validation"]["ok"])

    def test_bad_constraints_exit_nonzero(self):
        proc = run("gtm", "design", "增长一个小红书账号",
                   "--constraints", '{"assets":["magic"]}')
        self.assertEqual(2, proc.returncode)
        self.assertIn("设计失败", proc.stderr)


if __name__ == "__main__":
    unittest.main()
