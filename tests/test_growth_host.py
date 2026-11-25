import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from core import growth_cycle, growth_host, growth_service, growth_worker
from tests.test_growth_goals import goal

ROOT = Path(__file__).resolve().parents[1]


class GrowthHostWakeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = self.tmp.name
        self.root = Path(self.data)
        self.service = growth_service.start(self.data, goal())
        self.sid = self.service["id"]
        self.cycle = growth_cycle.request(
            self.data, self.sid, actor="tester", cycle_id="cycle-test")
        self.script = self.root / "host.py"
        self.script.write_text(
            "import json,os,sys\n"
            "request=json.load(open(sys.argv[1]))\n"
            "assert request['schema']=='growovo.growth-cycle-request/1'\n"
            "open('invocations','a').write('1\\n')\n"
            "proposal={'schema':'growovo.growth-cycle-proposal/1','producer_id':'fixture-host@1',\n"
            "'proposals':[{'proposal_id':'p1','action_class':'observe','action_id':'diagnose-product','rationale':'diagnose next'}]}\n"
            "json.dump(proposal,open(os.path.join(sys.argv[2],'proposal.json'),'w'))\n"
            "print('proposal ready')\n",
            encoding="utf-8")
        self.adapter = {
            "schema": "growovo.host-adapter/1",
            "adapter_id": "fixture-host", "version": "1",
            "producer_id": "fixture-host@1",
            "argv": [sys.executable, str(self.script), "{request}", "{workspace}"],
            "env_passthrough": [], "timeout_seconds": 10,
            "authorization": {"authorized_by": "test-owner", "scope": "growth_cycle_proposal"},
        }

    def test_wake_persists_host_proposal_and_is_idempotent(self):
        result = growth_host.wake(
            self.data, self.sid, "cycle-test", self.adapter)
        self.assertEqual("proposed", result["status"])
        self.assertEqual(1, len(result["accepted"]))
        again = growth_host.wake(
            self.data, self.sid, "cycle-test", self.adapter)
        self.assertEqual("already_proposed", again["status"])
        markers = list((self.root / "state/host-wake").rglob("invocations"))
        self.assertEqual(1, len(markers))
        self.assertEqual(1, len(markers[0].read_text().splitlines()))
        cycle = growth_cycle.status(self.data, self.sid, "cycle-test")
        self.assertEqual("proposed", cycle["status"])
        self.assertEqual("fixture-host@1", cycle["proposal"]["producer_id"])

    def test_invalid_host_output_keeps_cycle_open(self):
        self.script.write_text("print('not-json')\n", encoding="utf-8")
        result = growth_host.wake(
            self.data, self.sid, "cycle-test", self.adapter)
        self.assertEqual("blocked", result["status"])
        self.assertEqual("open", growth_cycle.status(
            self.data, self.sid, "cycle-test")["status"])

    def test_worker_wakes_due_cycle_without_executing_it(self):
        heartbeat = growth_worker.run_once(
            self.data, self.sid, host_adapter=self.adapter)
        self.assertTrue(heartbeat["components"]["host_wake"]["ok"])
        cycle = growth_cycle.status(self.data, self.sid, "cycle-test")
        self.assertEqual("proposed", cycle["status"])
        self.assertEqual([], cycle["executions"])

    def test_execution_feedback_creates_bounded_followup_cycle(self):
        adapter = dict(self.adapter, feedback_enabled=True, max_feedback_depth=1)
        first = growth_worker.run_once(
            self.data, self.sid, host_adapter=adapter,
            execute_accepted_by="worker-owner")
        self.assertTrue(first["components"]["execution_feedback"]["ok"])
        cycles = growth_cycle.list_cycles(self.data, self.sid)
        self.assertEqual(2, len(cycles))
        followup = next(row for row in cycles if row["cycle_id"] != "cycle-test")
        self.assertEqual("open", followup["status"])
        self.assertEqual(1, followup["feedback_depth"])
        original = growth_cycle.status(self.data, self.sid, "cycle-test")
        self.assertEqual(followup["cycle_id"], original["feedback_followup"]["cycle_id"])
        growth_worker.run_once(
            self.data, self.sid, host_adapter=adapter,
            execute_accepted_by="worker-owner")
        self.assertEqual(2, len(growth_cycle.list_cycles(self.data, self.sid)))

    def test_worker_can_explicitly_execute_accepted_local_action(self):
        heartbeat = growth_worker.run_once(
            self.data, self.sid, host_adapter=self.adapter,
            execute_accepted_by="worker-owner")
        self.assertTrue(heartbeat["components"]["cycle_execute"]["ok"])
        cycle = growth_cycle.status(self.data, self.sid, "cycle-test")
        self.assertEqual("executed", cycle["status"])
        self.assertEqual("worker-owner", cycle["executed_by"])
        self.assertEqual("completed", cycle["executions"][0]["result"]["status"])

    def test_cli_operator_can_execute_accepted_local_action(self):
        adapter_path = self.root / "adapter.json"
        adapter_path.write_text(json.dumps(self.adapter), encoding="utf-8")
        result = subprocess.run([
            sys.executable, str(ROOT / "mkt.py"), "--workspace", self.data,
            "growth", "operator-serve", self.sid, "--once",
            "--host-adapter", str(adapter_path),
            "--execute-accepted-by", "worker-owner",
        ], cwd=ROOT, text=True, capture_output=True)
        self.assertEqual(0, result.returncode, result.stderr + result.stdout)
        cycle = growth_cycle.status(self.data, self.sid, "cycle-test")
        self.assertEqual("executed", cycle["status"])

    def test_cli_cycle_wake(self):
        adapter_path = self.root / "adapter.json"
        adapter_path.write_text(json.dumps(self.adapter), encoding="utf-8")
        result = subprocess.run([
            sys.executable, str(ROOT / "mkt.py"), "--workspace", self.data,
            "growth", "cycle", "wake", self.sid, "--cycle", "cycle-test",
            "--adapter", str(adapter_path),
        ], cwd=ROOT, text=True, capture_output=True)
        self.assertEqual(0, result.returncode, result.stderr + result.stdout)
        payload = json.loads(result.stdout)
        self.assertEqual("proposed", payload["status"])


if __name__ == "__main__":
    unittest.main()
