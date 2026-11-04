"""Playbook 注册表：代码里不止一个 playbook，且每个都写明证据与风险。"""
import unittest

from core import playbooks


class PlaybookRegistryTests(unittest.TestCase):
    def test_more_than_one_playbook(self):
        items = playbooks.list_playbooks()
        self.assertGreaterEqual(len(items), 2)
        self.assertIn("reddit-intent", [p["id"] for p in items])

    def test_every_playbook_states_evidence_and_caution(self):
        for p in playbooks.list_playbooks():
            self.assertTrue(p.get("evidence"), p["id"])
            self.assertTrue(p.get("caution"), p["id"])
            self.assertTrue(p.get("steps"), p["id"])

    def test_github_inbox_admits_the_evidence_gap(self):
        spec = playbooks.playbook_spec("github-inbox")
        self.assertIn("假设", spec["caution"])
        self.assertIn("1699", spec["caution"])

    def test_reddit_spec_has_executable_quota(self):
        q = playbooks.playbook_spec("reddit-intent")["quota"]
        for k in ("actions_per_week", "min_sample", "deleted_max"):
            self.assertIn(k, q)
        self.assertEqual(q["actions_per_week"], 20)

    def test_unknown_playbook_returns_none(self):
        self.assertIsNone(playbooks.playbook_spec("nope"))


if __name__ == "__main__":
    unittest.main()
