"""平台风控层：四要素齐全、来源可追、策略卡接线。"""
import unittest

from core import platform_risk, platform_strategy


class PlatformRiskTests(unittest.TestCase):
    def test_every_strategy_platform_has_risk_card(self):
        missing = set(platform_strategy.STRATEGY) - set(platform_risk.PLATFORMS)
        self.assertEqual(missing, set())

    def test_validate_passes(self):
        self.assertEqual(platform_risk.validate(), {})

    def test_signal_has_four_elements_and_source(self):
        for pid in platform_risk.PLATFORMS:
            spec = platform_risk.get(pid)
            source_ids = {s["id"] for s in spec["sources"]}
            for sig in spec["signals"]:
                self.assertTrue(sig.get("signal"), (pid, sig.get("id")))
                self.assertTrue(sig.get("threshold"), (pid, sig.get("id")))
                self.assertTrue(sig.get("action"), (pid, sig.get("id")))
                self.assertIn(sig.get("level"), platform_risk.LEVELS, (pid, sig.get("id")))
                self.assertIn(sig.get("source"), source_ids, (pid, sig.get("id")))

    def test_card_includes_risk(self):
        card = platform_strategy.card("wechat")
        self.assertIn("risk", card)
        self.assertEqual(card["risk"]["platform"], "wechat")
        self.assertTrue(card["risk"]["signals"])

    def test_unknown_platform_raises(self):
        with self.assertRaises(KeyError):
            platform_risk.card("no-such-platform")

    def test_render_card_has_risk_section(self):
        text = platform_strategy.render_card("reddit")
        self.assertIn("## 风控", text)
        self.assertIn("触发后", text)
        self.assertIn("2026-09-30", text)

    def test_every_platform_has_observed_list(self):
        for pid in platform_risk.PLATFORMS:
            self.assertIsInstance(platform_risk.get(pid).get("observed"), list, pid)

    def test_observed_evidence_is_sourceable(self):
        for pid in platform_risk.PLATFORMS:
            for item in platform_risk.get(pid)["observed"]:
                self.assertIn(item["confidence"], platform_risk.OBSERVED_CONFIDENCE, (pid, item["id"]))
                self.assertTrue(platform_risk._as_date(item["as_of"]), (pid, item["id"]))
                self.assertTrue(item["source_urls"], (pid, item["id"]))
                for url in item["source_urls"]:
                    self.assertTrue(url.startswith("https://"), (pid, item["id"], url))

    def test_publish_platforms_carry_observed_entries(self):
        for pid in ("reddit", "x", "github"):
            self.assertGreaterEqual(len(platform_risk.get(pid)["observed"]), 2, pid)

    def test_card_counts_observed_by_confidence(self):
        card = platform_risk.card("reddit")
        self.assertEqual(sum(card["observed_counts"].values()), len(card["observed"]))
        self.assertGreaterEqual(card["observed_counts"]["high"], 1)

    def test_render_card_lists_observed(self):
        text = platform_risk.render_card("reddit")
        self.assertIn("社区经验（只用于放慢节奏）", text)
        self.assertIn("15 分钟", text)

    def test_observed_confidence_rejects_unknown_value(self):
        item = platform_risk.get("reddit")["observed"][0]
        original = item["confidence"]
        item["confidence"] = "certain"
        try:
            self.assertIn("reddit", platform_risk.validate(["reddit"]))
        finally:
            item["confidence"] = original

    def test_missing_observed_list_is_reported(self):
        spec = platform_risk.get("x")
        original = spec.pop("observed")
        try:
            self.assertIn("x", platform_risk.validate(["x"]))
        finally:
            spec["observed"] = original

    def test_new_official_gates_are_wired(self):
        codes = {g["code"] for g in platform_strategy.gates("wechat", scope="execution")}
        self.assertIn("scripted_bulk_publish", codes)
        codes = {g["code"] for g in platform_strategy.gates("xiaohongshu", scope="content")}
        self.assertIn("duplicate_material", codes)
        codes = {g["code"] for g in platform_strategy.gates("x", scope="execution")}
        self.assertIn("link_cost", codes)


if __name__ == "__main__":
    unittest.main()
