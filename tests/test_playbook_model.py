"""Platform × Motion × Vertical 组合契约。"""
import unittest

from core import playbook_model


class PlaybookModelTests(unittest.TestCase):
    def test_compose_reuses_platform_motion_vertical(self):
        pb = playbook_model.compose("reddit", "community_answer", "devtool")
        self.assertEqual("reddit", pb["platform"]["id"])
        self.assertEqual("community_answer", pb["motion"]["id"])
        self.assertEqual("devtool", pb["vertical"]["id"])
        self.assertEqual(("reddit", "reply"), pb["execution"]["transport"])
        self.assertIn("可复现解决方案", playbook_model.render(pb))

    def test_crowdfunding_uses_launch_workflow_not_community_defaults(self):
        pb = playbook_model.compose("kickstarter", "crowdfunding", "hardware")
        self.assertEqual("众筹", pb["motion"]["label"])
        self.assertIn("履约", pb["motion"]["observation"])

    def test_unknown_motion_or_vertical_fails_closed(self):
        with self.assertRaises(ValueError):
            playbook_model.compose("reddit", "unknown", "devtool")
        with self.assertRaises(ValueError):
            playbook_model.compose("reddit", "community_answer", "unknown")


if __name__ == "__main__":
    unittest.main()
