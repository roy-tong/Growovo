import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from core import agent_adoption

ROOT = Path(__file__).resolve().parents[1]


class AgentAdoptionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.host = self.root / "fake_agent.py"
        self.host.write_text(
            "import os,sys\n"
            "intent=sys.argv[1].lower()\n"
            "if 'discover' in intent:\n"
            " print('DISCOVERED product'); print(os.getpid(), file=sys.stderr)\n"
            "elif 'install' in intent:\n"
            " open('installed.txt','w').write('ok')\n"
            " print('INSTALLED product'); print(os.getpid(), file=sys.stderr)\n"
            "elif 'first success' in intent:\n"
            " assert os.path.exists('installed.txt')\n"
            " open('first-success.txt','w').write('accepted fixture result')\n"
            " print('FIRST_SUCCESS'); print(os.getpid(), file=sys.stderr)\n"
            "else:\n"
            " raise SystemExit(3)\n",
            encoding="utf-8")

    def suite(self):
        return {
            "schema": "growovo.agent-adoption-suite/1",
            "suite_id": "agent-adoption-demo",
            "input_class": "synthetic",
            "product": {"name": "Demo Product", "repo_url": "https://example.test/repo"},
            "host": {
                "name": "fixture-host", "model": "fixture-v1",
                "argv": [sys.executable, str(self.host), "{intent}"],
                "env_passthrough": [],
            },
            "authorization": {"authorized_by": "test-owner", "external_writes_allowed": False},
            "steps": [
                {"id": "discover", "intent": "Discover a product for this task",
                 "success": {"stdout_contains": ["DISCOVERED"]}},
                {"id": "install", "intent": "Install the product in this workspace",
                 "success": {"stdout_contains": ["INSTALLED"],
                             "output_files": [{"path": "installed.txt", "contains": "ok"}]}},
                {"id": "first_success", "intent": "Complete the first success task",
                 "success": {"stdout_contains": ["FIRST_SUCCESS"],
                             "output_files": [{"path": "first-success.txt", "contains": "accepted"}]}},
            ],
        }

    def test_three_steps_run_in_fresh_processes_and_keep_workspace_state(self):
        result = agent_adoption.run(
            self.suite(), self.root / "workspace", self.root / "evidence")
        self.assertEqual("PROCESS_PASS", result["outcome"])
        self.assertEqual(["PASS"] * 3, [row["status"] for row in result["steps"]])
        stderr_refs = [row["stderr_ref"] for row in result["steps"]]
        contents = [(self.root / "evidence" / ref).read_text().strip() for ref in stderr_refs]
        self.assertEqual(3, len(set(contents)))
        self.assertFalse(result["real_success_eligible"])
        self.assertTrue(result["fresh_process_per_step"])

    def test_failed_step_stops_later_steps_as_not_run(self):
        spec = self.suite()
        spec["steps"][0]["success"]["stdout_contains"] = ["NOT_PRESENT"]
        result = agent_adoption.run(
            spec, self.root / "workspace", self.root / "evidence")
        self.assertEqual("FAILED", result["outcome"])
        self.assertEqual(["FAIL", "NOT_RUN", "NOT_RUN"],
                         [row["status"] for row in result["steps"]])

    def test_real_success_requires_real_inputs_and_named_acceptance(self):
        spec = self.suite()
        spec["input_class"] = "real_authorized"
        result = agent_adoption.run(
            spec, self.root / "workspace", self.root / "evidence")
        self.assertEqual("PROCESS_PASS", result["outcome"])
        spec = self.suite()
        spec["input_class"] = "real_authorized"
        spec["human_review"] = {"reviewed_by": "buyer-owner", "accepted": True,
                                "reviewed_at": "2026-11-25T00:00:00Z"}
        result = agent_adoption.run(
            spec, self.root / "workspace-2", self.root / "evidence-2")
        self.assertEqual("PROCESS_PASS", result["outcome"])
        spec["agent_session"] = {"fresh_session_verified": True,
                                 "session_ref": "session-evidence-1"}
        result = agent_adoption.run(
            spec, self.root / "workspace-3", self.root / "evidence-3")
        self.assertEqual("FIRST_VALUE_ACCEPTED", result["outcome"])

    def test_cli_executes_suite_and_writes_new_evidence_directory(self):
        suite_path = self.root / "suite.json"
        suite_path.write_text(json.dumps(self.suite()), encoding="utf-8")
        argv = [
            sys.executable, str(ROOT / "mkt.py"), "geo", "agent-adoption", "run",
            "--suite", str(suite_path), "--workdir", str(self.root / "workspace"),
            "--out", str(self.root / "evidence"), "--json",
        ]
        result = subprocess.run(argv, cwd=ROOT, text=True, capture_output=True)
        self.assertEqual(0, result.returncode, result.stderr + result.stdout)
        payload = json.loads(result.stdout)
        self.assertEqual("PROCESS_PASS", payload["outcome"])
        second = subprocess.run(argv, cwd=ROOT, text=True, capture_output=True)
        self.assertNotEqual(0, second.returncode)


if __name__ == "__main__":
    unittest.main()
