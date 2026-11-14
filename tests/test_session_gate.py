"""Session gate：账号=浏览器登录态的统一检测（无网络无浏览器操作，全部 fake 注入）。"""
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from core import grow_ops, session_gate
from core.action_runner import ActionError, precheck
from core.execution_policy import authorize
from core.session_gate import CDP_UNAVAILABLE, LOGGED_IN, LOGGED_OUT


class FakePage:
    def __init__(self, landing):
        self._landing = landing  # url -> 落点 url
        self.url = ""

    def locator(self, selector):
        from unittest.mock import Mock
        return Mock()

    def goto(self, url, **kwargs):
        self.url = self._landing(url)
        return self.url


class SessionGateTests(unittest.TestCase):
    def setUp(self):
        session_gate.clear_cache()
        self.addCleanup(session_gate.clear_cache)

    def _connect(self, landing):
        page = FakePage(landing)
        calls = []
        def connect(cdp_url=None):
            calls.append(cdp_url)
            return (object(), page)
        return connect, calls

    def test_logged_in(self):
        connect, calls = self._connect(lambda url: url)
        state = session_gate.check_login("x", connect=connect)
        self.assertEqual(LOGGED_IN, state["status"])
        self.assertEqual(1, len(calls))

    def test_logged_out_marker(self):
        connect, _ = self._connect(lambda url: "https://x.com/login" if "x.com" in url else url)
        state = session_gate.check_login("x", connect=connect)
        self.assertEqual(LOGGED_OUT, state["status"])
        self.assertEqual("/login", state["marker"])

    def test_cdp_unavailable_is_not_logged_out(self):
        def broken(cdp_url=None):
            raise RuntimeError("connection refused")
        state = session_gate.check_login("x", connect=broken)
        self.assertEqual(CDP_UNAVAILABLE, state["status"])
        self.assertNotEqual(LOGGED_OUT, state["status"])

    def test_unknown_channel_short_circuits(self):
        state = session_gate.check_login("email")  # email 走 smtp，无探测表
        self.assertEqual("unknown", state["status"])

    def test_ttl_cache_reuses_result(self):
        connect, calls = self._connect(lambda url: url)
        clock = {"t": 0.0}
        state1 = session_gate.check_login("x", connect=connect, now=lambda: clock["t"])
        state2 = session_gate.check_login("x", connect=connect, now=lambda: clock["t"] + 100)
        self.assertEqual(state1["status"], state2["status"])
        self.assertEqual(1, len(calls))  # TTL 内不重开页
        clock["t"] += session_gate._TTL_SECONDS + 1
        session_gate.check_login("x", connect=connect, now=lambda: clock["t"])
        self.assertEqual(2, len(calls))  # 过期后重新探测


class PrecheckLoginGateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        session_gate.clear_cache()
        self.addCleanup(session_gate.clear_cache)

    def _probe(self, status):
        calls = []
        def probe(channel):
            calls.append(channel)
            return {"channel": channel, "status": status}
        return probe, calls

    def test_missing_login_rejected_before_dispatch(self):
        authorize(self.dir, by="tester", actions=["x:reply"], days=30)
        probe, calls = self._probe(LOGGED_OUT)
        with self.assertRaises(ActionError) as ctx:
            precheck("x", "reply", self.dir, "c1", True, False,
                     routine=True, login_probe=probe)
        self.assertIn("登录态缺失", str(ctx.exception))
        self.assertEqual(["x"], calls)

    def test_cdp_down_is_distinct_error(self):
        authorize(self.dir, by="tester", actions=["x:reply"], days=30)
        probe, _ = self._probe(CDP_UNAVAILABLE)
        with self.assertRaises(ActionError) as ctx:
            precheck("x", "reply", self.dir, "c1", True, False,
                     routine=True, login_probe=probe)
        self.assertIn("通道不可用", str(ctx.exception))
        self.assertIn("launch.command", str(ctx.exception))

    def test_logged_in_passes_gate(self):
        authorize(self.dir, by="tester", actions=["x:reply"], days=30)
        probe, _ = self._probe(LOGGED_IN)
        mode = precheck("x", "reply", self.dir, "c1", True, False,
                        routine=True, login_probe=probe)
        self.assertEqual("routine-policy", mode)

    def test_dry_run_never_opens_browser(self):
        probe, calls = self._probe(LOGGED_OUT)
        precheck("x", "reply", self.dir, "c1", False, False, login_probe=probe)
        self.assertEqual([], calls)  # dry-run 不探测

    def test_non_cdp_channel_skips_gate(self):
        authorize(self.dir, by="tester", actions=["email:message"], days=30)
        probe, calls = self._probe(LOGGED_OUT)
        precheck("email", "message", self.dir, "c1", True, False,
                 routine=True, login_probe=probe)
        self.assertEqual([], calls)  # smtp 渠道无浏览器登录态语义


class ReadStatsLoginTests(unittest.TestCase):
    """未登录时 read_stats 报「登录态缺失」，不冒充解析不出来的「取不到」。"""

    def setUp(self):
        session_gate.clear_cache()
        self.addCleanup(session_gate.clear_cache)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def _read(self, status):
        from unittest.mock import patch
        from channels.x import read_stats
        with patch.object(read_stats.session_gate, "check_login",
                          return_value={"channel": "x", "status": status}):
            return read_stats.read("verovo", self.tmp.name, log=lambda *a, **k: None)

    def test_logged_out_reports_login_gap(self):
        result = self._read(LOGGED_OUT)
        self.assertIsNone(result["followers"])
        self.assertIn("登录态缺失", result["impressions_note"])
        self.assertEqual(LOGGED_OUT, result["login_status"])

    def test_cdp_down_reports_channel_gap(self):
        result = self._read(CDP_UNAVAILABLE)
        self.assertIn("launch.command", result["impressions_note"])
        self.assertEqual(CDP_UNAVAILABLE, result["login_status"])


class DoctorLoginTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        (self.dir / "queues").mkdir()
        self.now = datetime(2026, 11, 14, 10, 0, tzinfo=timezone.utc)
        session_gate.clear_cache()
        self.addCleanup(session_gate.clear_cache)

    def _run(self, channels):
        return {"schema": "growovo.grow-run/2", "id": "g1", "status": "active",
                "go": True, "channels": channels, "actions": ["x:reply"],
                "telemetry": {"days": {}},
                "measurement": {"readers": [{"metric": "x_followers", "handle": "h"}]},
                "guards": {}, "heartbeat": {}}

    def _login_check(self, run, status):
        result = grow_ops.doctor(self.dir, run, now=self.now,
                                 login_probe=lambda c: {"channel": c, "status": status})
        return next(c for c in result["checks"] if c["name"] == "login"), result

    def test_logged_out_is_blocker(self):
        authorize(self.dir, by="tester", actions=["x:reply"], days=30)
        check, result = self._login_check(self._run(["x", "email"]), LOGGED_OUT)
        self.assertFalse(check["ok"])
        self.assertEqual("blocker", check["level"])
        self.assertFalse(result["ok"])
        self.assertIn("登录", check["fix"])

    def test_cdp_down_is_warn_not_blocker(self):
        authorize(self.dir, by="tester", actions=["x:reply"], days=30)
        check, _ = self._login_check(self._run(["x"]), CDP_UNAVAILABLE)
        self.assertFalse(check["ok"])
        self.assertEqual("warn", check["level"])
        self.assertIn("launch.command", check["fix"])

    def test_logged_in_is_info(self):
        authorize(self.dir, by="tester", actions=["x:reply"], days=30)
        check, _ = self._login_check(self._run(["x"]), LOGGED_IN)
        self.assertTrue(check["ok"])
        self.assertEqual("info", check["level"])

    def test_no_probe_channel_no_login_check(self):
        authorize(self.dir, by="tester", actions=["x:reply"], days=30)
        result = grow_ops.doctor(self.dir, self._run(["email"]), now=self.now,
                                 login_probe=lambda c: {"channel": c, "status": LOGGED_OUT})
        self.assertNotIn("login", {c["name"] for c in result["checks"]})


if __name__ == "__main__":
    unittest.main()
