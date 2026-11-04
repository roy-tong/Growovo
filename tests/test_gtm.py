"""GTM diagnose engine: schema enforcement, evidence labels, honest reporting."""
import unittest

from core import gtm


def _facts():
    return {
        "product": {
            "name": "demo-eval", "what": "LLM 输出核验", "url": "https://example.com",
            "stage": "launched", "product_type": "devtool",
        },
        "icp": [
            {"hypothesis": "独立开发者在意 eval", "tags": ["developers"],
             "evidence_label": "hypothesis", "evidence": "访谈×3（态度，无支付行为）"},
            {"hypothesis": "OSS 维护者有核验需求", "tags": ["oss-maintainers"],
             "evidence_label": "self_ab", "evidence": "上游交付链复盘 2026-10-31"},
        ],
        "goals": [{"layer": "G2", "metric": "激活", "target": 20}],
        "value_events": [
            {"name": "激活", "source": "产品内事件（PostHog）", "is_approximation": False},
        ],
        "constraints": {"red_lines": ["不做群发"], "human_minutes_budget": 720},
        "owned_assets": {"platforms": ["github"]},
    }


class GtmDiagnoseTests(unittest.TestCase):
    def test_platform_priorities_and_sections(self):
        payload = gtm.diagnose(_facts())
        by_id = {p["id"]: p for p in payload["platforms"]}
        # github: 2 tag 匹配(4) + 类型(2) + 自有(1) = 7 → P0
        self.assertEqual(by_id["github"]["priority"], "P0")
        # hackernews: developers 匹配(2) + 类型(2) = 4 → P1
        self.assertEqual(by_id["hackernews"]["priority"], "P1")
        # product-hunt: 无 tag 交集 + 类型(2) = 2 → P2
        self.assertEqual(by_id["product-hunt"]["priority"], "P2")

        report = gtm.markdown(payload)
        for section in ("# GTM 诊断报告", "## 各平台排位", "## 价值事件口径", "## 下一步"):
            self.assertIn(section, report)
        self.assertIn("Product Hunt", report.split("## 价值事件口径")[0])

    def test_every_motion_carries_evidence_label(self):
        payload = gtm.diagnose(_facts())
        for platform in payload["platforms"]:
            for motion in platform["motions"] + platform["killed_motions"]:
                self.assertIn(motion["label"], gtm.LABELS)
                self.assertTrue(motion["evidence"], motion["motion"])
        report = gtm.markdown(payload)
        # 对外报告只讲人话：内部编码一个都不许出现。
        for code in gtm.LABELS.values():
            self.assertNotIn(code, report)
        self.assertIn("我们自己对比过", report)
        self.assertIn("还没验证", report)

    def test_killed_motions_never_recommended(self):
        payload = gtm.diagnose(_facts())
        for platform in payload["platforms"]:
            alive = {m["motion"] for m in platform["motions"]}
            dead = {m["motion"] for m in platform["killed_motions"]}
            self.assertFalse(alive & dead)
        report = gtm.markdown(payload)
        matrix = report.split("不做什么（以及为什么）")[0]
        self.assertNotIn("买星", matrix)
        self.assertIn("不做什么（以及为什么）", report)

    def test_missing_evidence_label_rejected(self):
        facts = _facts()
        facts["icp"][0].pop("evidence_label")
        with self.assertRaises(ValueError):
            gtm.diagnose(facts)
        facts = _facts()
        facts["icp"][0]["evidence_label"] = "gut_feeling"
        with self.assertRaises(ValueError):
            gtm.diagnose(facts)

    def test_value_events_mandatory_and_explicit(self):
        facts = _facts()
        facts["value_events"] = []
        with self.assertRaises(ValueError):
            gtm.diagnose(facts)
        facts = _facts()
        facts["value_events"][0].pop("is_approximation")
        with self.assertRaises(ValueError):
            gtm.diagnose(facts)
        facts = _facts()
        facts["value_events"][0].pop("source")
        with self.assertRaises(ValueError):
            gtm.diagnose(facts)

    def test_all_hypothesis_confidence_low(self):
        facts = _facts()
        for hyp in facts["icp"]:
            hyp["evidence_label"] = "hypothesis"
        payload = gtm.diagnose(facts)
        self.assertEqual(payload["confidence"]["level"], "低")
        self.assertTrue(any("观察指标" in u for u in payload["unresolved"]))

    def test_approximation_flagged_in_report(self):
        facts = _facts()
        facts["value_events"][0]["is_approximation"] = True
        payload = gtm.diagnose(facts)
        self.assertTrue(any("近似口径" in u for u in payload["unresolved"]))
        self.assertIn("近似口径，已标注", gtm.markdown(payload))

    def test_no_platform_match_is_honest(self):
        facts = _facts()
        facts["icp"] = [
            {"hypothesis": "玩家社群", "tags": ["gamers"],
             "evidence_label": "hypothesis", "evidence": "无"},
        ]
        facts["product"]["product_type"] = "hardware"
        facts["owned_assets"] = {"platforms": []}
        payload = gtm.diagnose(facts)
        # gamers 人群无 tag 交集；但 hardware 类型合理命中众筹平台（P2 弱匹配）
        by_id = {p["id"]: p for p in payload["platforms"]}
        self.assertFalse(any(p["priority"] == "P0" for p in payload["platforms"]))
        self.assertEqual(by_id["kickstarter"]["priority"], "P2")
        self.assertTrue(any("没有平台够到 P0" in u for u in payload["unresolved"]))

    def test_registry_motion_labels_valid(self):
        for platform in gtm.PLATFORMS:
            for motion in platform["motions"]:
                self.assertIn(motion["label"], gtm.LABELS, platform["id"])
                self.assertTrue(motion["evidence"], f"{platform['id']}: {motion['motion']}")
        self.assertIn("self_research", gtm.LABELS)

    def test_consumer_app_matches_xiaohongshu(self):
        facts = _facts()
        facts["product"] = {"name": "demo-app", "what": "健康习惯 App", "url": "https://example.com",
                            "stage": "launched", "product_type": "consumer-app"}
        facts["icp"] = [
            {"hypothesis": "年轻用户在意健康记录", "tags": ["consumers", "indie-makers"],
             "evidence_label": "hypothesis", "evidence": "无（首轮判据收集）"},
        ]
        facts["owned_assets"] = {"platforms": ["xiaohongshu"]}
        payload = gtm.diagnose(facts)
        by_id = {p["id"]: p for p in payload["platforms"]}
        self.assertEqual(by_id["xiaohongshu"]["priority"], "P0")
        # B2B 导流死路必须出现在判死清单里
        killed = {m["motion"] for m in by_id["xiaohongshu"]["killed_motions"]}
        self.assertTrue(any("导流" in m for m in killed))

    def test_deterministic_output(self):
        first = gtm.markdown(gtm.diagnose(_facts()))
        second = gtm.markdown(gtm.diagnose(_facts()))
        self.assertEqual(first, second)

    def test_focus_picks_one_primary_and_never_a_launch_platform(self):
        facts = _facts()
        # 多个平台都够到 P0 时，报告必须只给一个主渠道，而不是一串 P0。
        facts["icp"] = [
            {"hypothesis": "开发者在意 eval", "tags": ["developers", "oss-maintainers", "technical-founders"],
             "evidence_label": "self_ab", "evidence": "上游交付链复盘"},
        ]
        payload = gtm.diagnose(facts)
        p0 = [p["id"] for p in payload["platforms"] if p["priority"] == "P0"]
        self.assertGreater(len(p0), 1, "该用例需要多个 P0 才能验证收敛")

        focus = payload["focus"]
        self.assertIsNotNone(focus["primary"])
        self.assertNotIn(focus["primary"], gtm.LAUNCH_PLATFORMS)
        # 发射点与主渠道不能是同一个
        self.assertNotEqual(focus["launch"], focus["primary"])
        # 其余 P0 进入「本轮不做」，且不包含主渠道与发射点
        deferred_ids = {p["id"] for p in payload["platforms"] if p["name"] in focus["deferred"]}
        self.assertNotIn(focus["primary"], deferred_ids)
        if focus["launch"]:
            self.assertNotIn(focus["launch"], deferred_ids)

        report = gtm.markdown(payload)
        self.assertIn("## 本轮怎么打", report)
        self.assertIn("主渠道（只打这一个）", report)
        self.assertIn("也值得做、但这一轮不碰", report)

    def test_focus_prefers_owned_account_when_scores_tie(self):
        facts = _facts()
        facts["icp"] = [
            {"hypothesis": "开发者在 GitHub 和 HN", "tags": ["developers"],
             "evidence_label": "self_ab", "evidence": "上游交付链复盘"},
        ]
        facts["owned_assets"] = {"platforms": ["github"]}
        payload = gtm.diagnose(facts)
        self.assertEqual(payload["focus"]["primary"], "github")

    def test_focus_absent_when_nothing_scores(self):
        facts = _facts()
        facts["icp"] = [
            {"hypothesis": "无人区人群", "tags": ["nobody"],
             "evidence_label": "hypothesis", "evidence": "无"},
        ]
        facts["product"] = {"name": "x", "what": "x", "url": "https://example.com",
                            "stage": "launched", "product_type": "unknown-type"}
        facts["owned_assets"] = {"platforms": []}
        payload = gtm.diagnose(facts)
        self.assertIsNone(payload["focus"]["primary"])
        self.assertNotIn("## 本轮怎么打", gtm.markdown(payload))


if __name__ == "__main__":
    unittest.main()
