"""闭环运维：心跳过期、体检阻断项、launchd/crontab 生成。"""
import json
import plistlib
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from core import grow_ops
from core.execution_policy import authorize


def _run(**over):
    run = {
        "schema": "growovo.grow-run/2",
        "id": "g1",
        "status": "active",
        "go": True,
        "started": "2026-11-01",
        "actions": ["x:reply"],
        "quota": {"actions_per_week": 7, "action_weights": {"reply": 1.0}},
        "telemetry": {"days": {}},
        "measurement": {"readers": [{"metric": "x_followers", "handle": "verovo"}]},
        "guards": {},
        "heartbeat": {},
    }
    run.update(over)
    return run


class HeartbeatTests(unittest.TestCase):
    def test_stamp_and_fresh(self):
        run = {}
        grow_ops.stamp_heartbeat(run, at="2026-11-14T10:00:00+00:00",
                                 interval_minutes=30)
        state = grow_ops.heartbeat_state(
            run, now=datetime(2026, 11, 14, 10, 20, tzinfo=timezone.utc))
        self.assertTrue(state["ok"])
        self.assertFalse(state["stale"])
        self.assertEqual(20.0, state["age_minutes"])

    def test_stale_after_two_intervals(self):
        run = {}
        grow_ops.stamp_heartbeat(run, at="2026-11-14T10:00:00+00:00",
                                 interval_minutes=30)
        state = grow_ops.heartbeat_state(
            run, now=datetime(2026, 11, 14, 11, 10, tzinfo=timezone.utc))
        self.assertTrue(state["stale"])
        self.assertFalse(state["ok"])

    def test_missing_heartbeat_is_stale(self):
        state = grow_ops.heartbeat_state({})
        self.assertTrue(state["stale"])
        self.assertIsNone(state["last_tick"])


class DoctorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        (self.dir / "queues").mkdir()
        self.now = datetime(2026, 11, 14, 10, 0, tzinfo=timezone.utc)

    def _queue(self, name="g1-20261114"):
        (self.dir / "queues" / f"{name}.json").write_text(json.dumps({
            "campaign": name, "channel": "x", "action": "reply",
            "items": [{"id": f"{name}-1", "target": "tweet:https://x.com/a/1",
                       "body": "一条具体回复", "scheduled_for": "2026-11-14"}],
        }, ensure_ascii=False), encoding="utf-8")

    def test_healthy_run(self):
        authorize(self.dir, by="tester", actions=["x:reply"], days=30)
        self._queue()
        run = _run()
        grow_ops.stamp_heartbeat(run, at="2026-11-14T09:50:00+00:00")
        result = grow_ops.doctor(self.dir, run, now=self.now)
        self.assertTrue(result["ok"], result)
        names = {c["name"] for c in result["checks"]}
        self.assertTrue({"run", "heartbeat", "policy", "readings",
                         "queues", "supply", "account", "scheduler"} <= names)

    def test_missing_policy_is_blocker(self):
        self._queue()
        run = _run()
        grow_ops.stamp_heartbeat(run, at="2026-11-14T09:50:00+00:00")
        result = grow_ops.doctor(self.dir, run, now=self.now)
        self.assertFalse(result["ok"])
        policy = next(c for c in result["checks"] if c["name"] == "policy")
        self.assertFalse(policy["ok"])
        self.assertIn("mkt policy enable", policy["fix"])

    def test_bad_queue_is_blocker(self):
        authorize(self.dir, by="tester", actions=["x:reply"], days=30)
        (self.dir / "queues" / "g1-bad.json").write_text("{not json",
                                                         encoding="utf-8")
        run = _run()
        grow_ops.stamp_heartbeat(run, at="2026-11-14T09:50:00+00:00")
        result = grow_ops.doctor(self.dir, run, now=self.now)
        self.assertFalse(result["ok"])
        self.assertFalse(next(c for c in result["checks"]
                              if c["name"] == "queues")["ok"])

    def test_tcc_log_marks_scheduler_warning(self):
        authorize(self.dir, by="tester", actions=["x:reply"], days=30)
        self._queue()
        run_dir = self.dir / "grow" / "g1"
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "tick.log").write_text(
            "python3: can't open file '/x/mkt.py': [Errno 1] Operation not permitted\n",
            encoding="utf-8")
        run = _run()
        grow_ops.stamp_heartbeat(run, at="2026-11-14T09:50:00+00:00")
        result = grow_ops.doctor(self.dir, run, now=self.now)
        scheduler = next(c for c in result["checks"] if c["name"] == "scheduler")
        self.assertFalse(scheduler["ok"])
        self.assertIn("完全磁盘访问", scheduler["fix"])

    def test_stale_heartbeat_is_warning_not_blocker(self):
        authorize(self.dir, by="tester", actions=["x:reply"], days=30)
        self._queue()
        run = _run(heartbeat={"last_tick": "2026-11-01T00:00:00",
                              "interval_minutes": 30, "ok": True})
        result = grow_ops.doctor(self.dir, run, now=self.now)
        beat = next(c for c in result["checks"] if c["name"] == "heartbeat")
        self.assertFalse(beat["ok"])
        self.assertEqual("warn", beat["level"])


class InstallTests(unittest.TestCase):
    def test_plist_is_valid_and_contains_interval(self):
        text = grow_ops.launchd_plist(
            label="com.growovo.g1", python="/usr/bin/python3",
            mkt_path="/repo/mkt.py", run_id="g1", interval_minutes=30,
            log_path="/repo/data/grow/g1/tick.log", cwd="/repo")
        payload = plistlib.loads(text.encode("utf-8"))
        self.assertEqual("com.growovo.g1", payload["Label"])
        self.assertEqual(1800, payload["StartInterval"])
        self.assertEqual(["/usr/bin/python3", "/repo/mkt.py", "grow", "tick",
                          "g1", "--deliver"], payload["ProgramArguments"])
        self.assertTrue(payload["RunAtLoad"])

    def test_install_writes_file_without_loading(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "x.plist"
            result = grow_ops.install_launchd(out_path=path, plist="<plist/>",
                                              load=False)
            self.assertTrue(path.is_file())
            self.assertFalse(result["loaded"])
            self.assertIn("launchctl load", result["command"])

    def test_crontab_line(self):
        line = grow_ops.crontab_line(python="/usr/bin/python3",
                                     mkt_path="/repo/mkt.py", run_id="g1",
                                     interval_minutes=30,
                                     log_path="/repo/log", cwd="/repo")
        self.assertIn("*/30 * * * *", line)
        self.assertIn("grow tick g1", line)
        self.assertIn("cd /repo &&", line)

    def test_non_divisor_interval_falls_back_to_hourly(self):
        line = grow_ops.crontab_line(python="py", mkt_path="m", run_id="g1",
                                     interval_minutes=7, log_path="l")
        self.assertTrue(line.startswith("0 * * * *"))


if __name__ == "__main__":
    unittest.main()
