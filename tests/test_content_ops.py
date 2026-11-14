"""内容运营能力：渠道规则、brief、审稿与多渠道路排期。"""
import unittest

from core import content_ops


GOOD_XHS = """很多团队做AI硬件，第一反应是接一个大模型让设备聊天。用户说“把灯调暗”，设备回了一段很礼貌的话，灯却没动。

聊天框不是产品任务。设备要完成一句话，至少走四步：听清、听懂、找到功能填参数、执行。

我最近看了一个叫 Needle 的开源项目，它专门做中间两步：把一句话变成设备能执行的功能调用。官方文档记录模型大小是 8—29MB，也不支持中文语音。

你可以先做一张表：用户最常说的10句话、对应哪个功能、要哪些参数、听不懂怎么办。10条里如果有6条按钮就能完成，你需要的可能不是大模型。

你今天就可以先试一下，写10条真实指令，标出“必须AI”和“按钮就够”各几条。

后续会继续更新这类具体工作方法，感兴趣可以关注并私信我，我会发你完整方法。"""


class ContentOpsTests(unittest.TestCase):
    def test_channels_cover_three(self):
        self.assertEqual(set(content_ops.CHANNELS), {"xiaohongshu", "wechat", "blog"})

    def test_plan_counts_for_14_days(self):
        p = content_ops.plan(start="2026-11-07", days=14)
        self.assertEqual(p["counts"], {"xiaohongshu": 28, "wechat": 4, "blog": 2})
        self.assertEqual(len(p["rows"]), 34)
        self.assertEqual(p["rows"][0]["date"], "2026-11-07")

    def test_audit_rejects_meta_narration(self):
        text = "很多团队做AI硬件。\n\n这篇只回答一件事：第一步做什么。\n\n先列10条指令。"
        res = content_ops.audit_text("xiaohongshu", text, title="先列10条指令")
        self.assertFalse(res["ok"])
        self.assertIn("meta_narration", [e["code"] for e in res["errors"]])

    def test_audit_rejects_cta_label_and_private_contact(self):
        text = "很多团队做AI硬件。\n\n正文。\n\n文末CTA：关注我。加我微信。"
        res = content_ops.audit_text("xiaohongshu", text, title="硬件加AI")
        codes = [e["code"] for e in res["errors"]]
        self.assertIn("cta_label", codes)
        self.assertIn("private_contact", codes)

    def test_audit_rejects_missing_own_product_setup(self):
        text = "很多团队做AI硬件。\n\nAskovo 可以帮你整理评论。\n\n关注并私信我。"
        res = content_ops.audit_text("xiaohongshu", text, title="硬件加AI")
        self.assertIn("product_setup", [e["code"] for e in res["errors"]])

    def test_audit_rejects_long_xhs_title(self):
        res = content_ops.audit_text("xiaohongshu", GOOD_XHS, title="这是一个超过二十个字的标题用来测试长度限制")
        self.assertIn("title_too_long", [e["code"] for e in res["errors"]])

    def test_audit_accepts_good_post(self):
        res = content_ops.audit_text("xiaohongshu", GOOD_XHS, title="硬件加AI，先列10条指令")
        self.assertTrue(res["ok"], res["errors"])
        self.assertEqual(res["cta"], "B")

    def test_audit_file_ignores_editorial_and_footer(self):
        doc = "# 首周\n\n## D2-01｜硬件加AI，先列10条指令\n\n> 编辑定位（不发布）：写给硬件产品经理。\n\n" + GOOD_XHS + "\n\n## Growovo 复核记录\n\n这不是正文。\n"
        res = content_ops.audit_file("xiaohongshu", doc)
        self.assertTrue(res["ok"], res["documents"])
        self.assertEqual(len(res["documents"]), 1)
        self.assertNotIn("复核记录", res["documents"][0]["title"])

    def test_audit_file_uses_publish_title_map(self):
        doc = "## D2-01｜编辑标题\n\n" + GOOD_XHS + "\n"
        res = content_ops.audit_file("xiaohongshu", doc,
                                     titles={"D2-01": "这是一个超过二十个字的标题用来测试长度限制"})
        self.assertIn("title_too_long", [e["code"] for e in res["documents"][0]["errors"]])

    def test_mcp_exposes_content_tool(self):
        import mcp_server
        names = {t["name"] for t in mcp_server.TOOLS}
        self.assertIn("mkt_content", names)

    def test_mcp_content_rules_dispatch(self):
        import json
        import mcp_server
        response = mcp_server.handle({
            "jsonrpc": "2.0", "id": 9, "method": "tools/call",
            "params": {"name": "mkt_content",
                       "arguments": {"action": "rules", "channel": "xiaohongshu"}},
        })
        self.assertFalse(response["result"].get("isError", False))
        payload = json.loads(response["result"]["content"][0]["text"])
        self.assertIn("rendered", payload)
        self.assertIn("小红书", payload["rendered"])

    def test_public_draft_rejects_evidence_codes(self):
        text = "这件事有 E5 证据，来自 strict_master 文件。\n\n你可以先试一下。\n\n感兴趣的话可以关注我一波，私信我。"
        res = content_ops.audit_text("wechat", text, title="测试标题")
        codes = [e["code"] for e in res["errors"]]
        self.assertIn("evidence_code", codes)
        self.assertIn("internal_term", codes)

    def test_brief_has_structure_and_cta(self):
        b = content_ops.brief("wechat", "硬件加AI", audience="硬件产品经理", cta="B")
        self.assertEqual(b["channel"], "wechat")
        self.assertTrue(b["structure"])
        self.assertEqual(b["cta"]["type"], "B")
        self.assertTrue(any("元叙述" in x for x in b["checklist"]))

    def test_render_rules_and_plan(self):
        self.assertIn("小红书", content_ops.render_rules("xiaohongshu"))
        self.assertIn("28", content_ops.render_plan(content_ops.plan(days=14)))


if __name__ == "__main__":
    unittest.main()
