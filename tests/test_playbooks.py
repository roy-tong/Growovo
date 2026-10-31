#!/usr/bin/env python3
"""GitHub Inbox Playbook：分流、草稿队列、无自动发送。"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import playbooks
from core.queue import lint_queue


ROWS = [
    {"n": 10, "kind": "issue", "title": "Security vulnerability in auth",
     "labels": ["security"], "age_days": 2, "zero_reply": True,
     "url": "https://github.com/o/r/issues/10"},
    {"n": 11, "kind": "PR", "title": "Fix parser", "labels": [],
     "age_days": 4, "zero_reply": True, "url": "https://github.com/o/r/pull/11"},
    {"n": 12, "kind": "issue", "title": "Crash on startup", "labels": ["bug"],
     "age_days": 8, "zero_reply": True, "url": "https://github.com/o/r/issues/12"},
    {"n": 13, "kind": "issue", "title": "Can I use this with Docker?", "labels": [],
     "age_days": 1, "zero_reply": False, "url": "https://github.com/o/r/issues/13"},
    {"n": 14, "kind": "issue", "title": "Add dark mode", "labels": ["enhancement"],
     "age_days": 2, "zero_reply": True, "url": "https://github.com/o/r/issues/14"},
    {"n": 15, "kind": "issue", "title": "General discussion", "labels": [],
     "age_days": 1, "zero_reply": False, "url": "https://github.com/o/r/issues/15"},
]


class Tmp(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name) / "data"
        self.dir.mkdir()
        self.addCleanup(self.tmp.cleanup)


class TestTriage(unittest.TestCase):
    def test_classification_and_priority(self):
        plan = playbooks.triage_github_inbox(ROWS, "o/r")
        by_n = {x["number"]: x for x in plan["items"]}
        self.assertEqual("escalate", by_n[10]["disposition"])
        self.assertEqual("P0", by_n[10]["priority"])
        self.assertEqual("review", by_n[11]["disposition"])
        self.assertEqual("reply_candidate", by_n[12]["disposition"])
        self.assertEqual("P0", by_n[12]["priority"])
        self.assertEqual("reply_candidate", by_n[13]["disposition"])
        self.assertEqual("reply_candidate", by_n[14]["disposition"])
        self.assertEqual("triage_only", by_n[15]["disposition"])
        self.assertTrue(by_n[13]["draft_required"])
        self.assertEqual("missing", by_n[13]["draft_status"])
        self.assertTrue(by_n[10]["priority"] <= by_n[11]["priority"])

    def test_bad_repo_and_non_list(self):
        with self.assertRaises(ValueError):
            playbooks.triage_github_inbox([], "not-a-slug")
        with self.assertRaises(ValueError):
            playbooks.triage_github_inbox({"n": 1}, "o/r")


class TestPlanAndQueue(Tmp):
    def _plan(self):
        plan = playbooks.triage_github_inbox(ROWS, "o/r")
        return playbooks.save_plan(self.dir, plan, campaign="gh-w1")

    def test_plan_saved_and_rendered(self):
        plan, path = self._plan()
        self.assertTrue(path.is_file())
        self.assertEqual("gh-w1", json.loads(path.read_text(encoding="utf-8"))["campaign"])
        md = playbooks.render_markdown(plan)
        self.assertIn("GitHub Inbox Playbook", md)
        self.assertIn("不会自动发送", md)

    def test_queue_only_contains_drafted_reply_candidates(self):
        plan, _ = self._plan()
        drafts = self.dir / "drafts.json"
        drafts.write_text(json.dumps({
            "gh-12": "可以提供一个最小复现吗？我们会先确认是否影响当前版本。",
            "gh-13": "可以，安装文档里有 Docker 示例。",
            "10": "这条安全报告请走私密渠道，公开区不讨论细节。",
            "14": "感谢建议，我们会先收集使用场景。",
        }), encoding="utf-8")
        out = self.dir / "queues" / "gh-w1.json"
        queue, errs, meta = playbooks.build_reply_queue(
            plan, playbooks.load_drafts(drafts), out)
        self.assertEqual([], errs, errs)
        self.assertTrue(out.is_file())
        self.assertEqual("github", queue["channel"])
        self.assertEqual("reply", queue["action"])
        self.assertEqual({"gh-12", "gh-13", "gh-14"},
                         {i["id"] for i in queue["items"]})
        self.assertTrue(all(i["target"].startswith("issue:o/r#")
                            for i in queue["items"]))
        self.assertEqual([], lint_queue(queue, path=out))
        self.assertIn(10, meta["ignored"])

    def test_placeholder_draft_is_rejected(self):
        plan, _ = self._plan()
        out = self.dir / "queues" / "gh-w1.json"
        queue, errs, _ = playbooks.build_reply_queue(
            plan, {12: "你好 {{NAME}}，请提供复现。"}, out)
        self.assertIsNone(queue)
        self.assertTrue(any("占位符" in e for e in errs))
        self.assertFalse(out.exists())

    def test_no_drafts_is_an_error_not_a_send(self):
        plan, _ = self._plan()
        out = self.dir / "queues" / "gh-w1.json"
        queue, errs, _ = playbooks.build_reply_queue(plan, {}, out)
        self.assertIsNone(queue)
        self.assertTrue(errs)
        self.assertFalse(out.exists())

    def test_cli_works_offline_with_workspace(self):
        rows = self.dir / "rows.json"
        rows.write_text(json.dumps(ROWS), encoding="utf-8")
        cp = subprocess.run(
            [sys.executable, str(Path(__file__).resolve().parents[1] / "mkt.py"),
             "playbook", "github-inbox", "--repo", "o/r",
             "--campaign", "gh-w1", "--input", str(rows),
             "--workspace", str(self.dir), "--json"],
            text=True, capture_output=True)
        self.assertEqual(0, cp.returncode, cp.stdout + cp.stderr)
        self.assertIn("plan", json.loads(cp.stdout))
        self.assertTrue((self.dir / "playbooks" / "github-inbox" / "gh-w1.json").is_file())


if __name__ == "__main__":
    unittest.main()
