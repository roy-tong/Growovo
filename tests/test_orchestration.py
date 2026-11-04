#!/usr/bin/env python3
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.orchestration import infer_motion, lint_orchestration, orchestrate, render_markdown


class TestMinimumRoleSelection(unittest.TestCase):
    def test_developer_task_selects_only_required_roles(self):
        plan = orchestrate({"product_type": "devtool", "name": "API X"},
                           "让开发者在 14 天内首次成功调用",
                           "写技术教程并做 GitHub 发布，观察首次成功",
                           audience="developer", channel="github")
        self.assertEqual("developer_adoption", plan["motion"])
        self.assertEqual(["46", "24", "44", "12"], plan["selection"]["role_families"])
        self.assertEqual([], lint_orchestration(plan))
        self.assertNotIn("07", plan["selection"]["role_families"])
        self.assertNotIn("17", plan["selection"]["role_families"])
        expression = plan["content_plan"]["expression"]
        self.assertEqual("technical_issue_or_comment", expression["format"])
        self.assertIn("首次成功事件", expression["sections"])
        tutorial = next(d for d in plan["content_plan"]["deliverables"] if d["template_id"] == "T30")
        self.assertEqual("reproducible_developer_tutorial", tutorial["expression"]["presentation"])
        self.assertTrue(tutorial["expression"]["required_content"])
        measurement = next(d for d in plan["content_plan"]["deliverables"] if d["template_id"] == "T28")
        self.assertEqual("artifact_default", measurement["expression"]["channel"])

    def test_b2b_poc_selects_sales_not_brand_or_ecommerce(self):
        plan = orchestrate({"product_type": "b2b_saas"},
                           "创建合格 pipeline",
                           "对目标账户做 ABM，SDR 交接后安排 POC，由销售负责人接收",
                           audience="sales")
        roles = plan["selection"]["role_families"]
        self.assertEqual("b2b_pipeline", plan["motion"])
        self.assertIn("28", roles)
        self.assertIn("26", roles)
        self.assertIn("27", roles)
        self.assertNotIn("07", roles)
        self.assertNotIn("29", roles)
        self.assertEqual("sales_handoff", plan["content_plan"]["expression"]["format"])

    def test_sdr_task_does_not_expand_to_full_demand_team(self):
        plan = orchestrate({"product_type": "b2b_saas"}, "获得销售接受会议",
                           "给 SDR 做账户研究和资格交接")
        self.assertEqual("sales_development", plan["motion"])
        self.assertEqual(["26", "27", "42"], plan["selection"]["role_families"])

    def test_retention_selects_only_relevant_lifecycle_roles(self):
        plan = orchestrate({"product_type": "b2b_saas"}, "降低流失并提高续约",
                           "对风险客户做邮件召回、客户成功复盘和工单服务恢复")
        roles = plan["selection"]["role_families"]
        self.assertEqual("retention", plan["motion"])
        self.assertEqual(["34", "35", "38", "40"], roles)
        self.assertNotIn("17", roles)

    def test_seo_social_content_adds_search_not_creative(self):
        plan = orchestrate({"product_type": "consumer"}, "获得自然激活",
                           "做 SEO 内容并在小红书发布", channel="小红书")
        roles = plan["selection"]["role_families"]
        self.assertEqual("organic_discovery", plan["motion"])
        self.assertIn("14", roles)
        self.assertIn("09", roles)
        self.assertNotIn("11", roles)
        self.assertIn("12", roles)  # social is added only because the task explicitly requests it
        self.assertEqual("scenario_card_or_note", plan["content_plan"]["expression"]["format"])


class TestDeliveryExpression(unittest.TestCase):
    def test_same_work_can_be_executive_memo_or_operator_runbook(self):
        base = {"product_type": "b2b_saas"}
        executive = orchestrate(base, "决定本季度 ABM 是否扩量", "给高管做 ABM 决策", audience="executive")
        operator = orchestrate(base, "创建合格 pipeline", "运营团队执行 ABM 和 POC", audience="operator")
        self.assertEqual("decision_memo", executive["content_plan"]["expression"]["format"])
        self.assertEqual("operator_runbook", operator["content_plan"]["expression"]["format"])
        self.assertIn("需要决策的事项", executive["content_plan"]["expression"]["sections"])
        self.assertIn("步骤与日程", operator["content_plan"]["expression"]["sections"])

    def test_email_requires_personalized_single_cta(self):
        plan = orchestrate({"product_type": "b2b_saas"}, "获得合格会议",
                           "给目标账户写一封个性化邮件", channel="email")
        expression = plan["content_plan"]["expression"]
        self.assertEqual("personalized_email", expression["format"])
        self.assertIn("一个 CTA", expression["channel_rules"])
        self.assertIn("无模板占位符", expression["channel_rules"])
        rendered = render_markdown(plan)
        self.assertIn("最小 Workstream", rendered)
        self.assertIn("每份交付物怎么写", rendered)

    def test_motion_inference_uses_product_as_fallback(self):
        self.assertEqual("developer_adoption", infer_motion({"product_type": "devtool"}, "获得使用", "制定推广方案"))
        self.assertEqual("b2b_pipeline", infer_motion({"product_type": "b2b_saas"}, "获得收入", "制定推广方案"))

    def test_product_name_x_is_not_misread_as_twitter_channel(self):
        plan = orchestrate({"product_type": "consumer"}, "内容阅读", "给 API X 写英文使用文档")
        self.assertNotIn("12", plan["selection"]["role_families"])
        explicit = orchestrate({"product_type": "consumer"}, "内容互动", "给 API X 写英文文案", channel="x")
        self.assertIn("12", explicit["selection"]["role_families"])


if __name__ == "__main__":
    unittest.main()
