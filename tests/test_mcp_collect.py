#!/usr/bin/env python3
"""MCP 全渠道 / 统一动作执行器 / 本地 collector 的离线单测。"""
import json
import sys
import tempfile
import threading
import unittest
from http.client import HTTPConnection
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import mcp_server
from core import collector, links, outcome_io
from core.action_runner import ActionError, precheck, run_action
from core.attribution import OutcomeLedger
from core.queue_runner import QueueRunError, run_queue
from core.safety import set_pause


class Tmp(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name) / "data"
        (self.dir / "queues").mkdir(parents=True)
        (self.dir / "state").mkdir(parents=True)
        self.addCleanup(self.tmp.cleanup)


class TestMCPTools(unittest.TestCase):
    def test_tool_surface(self):
        names = {t["name"] for t in mcp_server.TOOLS}
        self.assertTrue({"mkt_run_queue", "mkt_action", "mkt_inbox", "mkt_audit",
                         "mkt_search", "mkt_campaign", "mkt_status"} <= names)
        search = next(t for t in mcp_server.TOOLS if t["name"] == "mkt_search")
        self.assertIn("github", search["inputSchema"]["properties"]["channel"]["enum"])

    def test_memory_mcp_reads_receipts_without_targets(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as d:
            data = Path(d)
            (data / "ledger.jsonl").write_text(json.dumps({
                "channel": "github", "action": "reply",
                "campaign": "research", "result": "committed",
                "target": "issue:secret/repo#2",
            }) + "\n", encoding="utf-8")
            with patch.object(mcp_server, "DATA", data):
                response = mcp_server.handle({
                    "jsonrpc": "2.0", "id": 8, "method": "tools/call",
                    "params": {"name": "mkt_memory", "arguments": {"campaign": "research"}},
                })
            self.assertFalse(response["result"].get("isError", False))
            payload = json.loads(response["result"]["content"][0]["text"])
            self.assertEqual(1, payload["patterns"][0]["confirmed_operations"])
            self.assertFalse(payload["patterns"][0]["automatic_replay_allowed"])
            self.assertNotIn("secret/repo", str(payload))

    def test_tool_stdout_is_clean(self):
        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            res = mcp_server.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                                     "params": {"name": "mkt_action",
                                                "arguments": {"channel": "email",
                                                              "action": "message",
                                                              "text": "hi"}}})
        self.assertTrue(res["result"]["isError"])
        self.assertEqual("", buf.getvalue())

    def test_initialize_and_list(self):
        init = mcp_server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        self.assertEqual("mkt-server", init["result"]["serverInfo"]["name"])
        listed = mcp_server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        self.assertTrue(listed["result"]["tools"])


class TestActionRunner(Tmp):
    def test_unknown_channel_and_read_action_refused(self):
        with self.assertRaises(ActionError):
            precheck("nosuch", "reply", self.dir, "c1", True, True)
        with self.assertRaises(ActionError):
            run_action(channel="x", action="search", data_dir=self.dir)

    def test_unverified_requires_flag_on_go(self):
        with self.assertRaises(ActionError):
            precheck("hn", "reply", self.dir, "c1", True, False)

    def test_pause_blocks_even_with_flag(self):
        set_pause(self.dir, True, reason="测试")
        with self.assertRaises(ActionError):
            precheck("hn", "reply", self.dir, "c1", True, True)

    def test_linkedin_full_auto_guard(self):
        (self.dir / "plans").mkdir(exist_ok=True)
        (self.dir / "plans" / "p1.json").write_text(json.dumps({
            "plan_id": "p1", "goal": "测试", "created": "2026-10-31",
            "hypothesis": "测试假设", "icp": {"beachhead": "开发者"},
            "audience": {"spec": "测试目标"},
            "waves": [{"wave": 1, "channel": "linkedin", "action": "message",
                       "size": 1, "content_policy": "人工确认",
                       "queue": "data/queues/c1.json"}],
            "success_criteria": {"metric": "reply", "target": "1", "stop": "0"},
            "review": {"human_approved": True},
        }), encoding="utf-8")
        with self.assertRaises(ActionError):
            run_action(channel="linkedin", action="message", data_dir=self.dir,
                       campaign="c1", go=True, allow_unverified=True,
                       target="https://www.linkedin.com/in/x", text="hi")

    def test_email_single_action_refused(self):
        with self.assertRaises(ActionError):
            run_action(channel="email", action="message", data_dir=self.dir,
                       campaign="c1", target="a@example.com", text="hi")

    def test_dnc_blocks_direct_action_even_in_dry_run(self):
        target = "https://www.linkedin.com/in/blocked"
        (self.dir / "contacts.jsonl").write_text(json.dumps({
            "id": "c1", "platforms": {"linkedin": target},
            "state": {"dnc": True},
        }) + "\n", encoding="utf-8")
        with self.assertRaisesRegex(ActionError, "抑制"):
            run_action(channel="linkedin", action="message", data_dir=self.dir,
                       campaign="c1", target=target, text="hi")


class TestQueueRunner(Tmp):
    def test_lint_failure_blocks_run(self):
        p = self.dir / "queues" / "c1.json"
        p.write_text(json.dumps({"campaign": "c1", "channel": "email", "action": "message",
                                 "items": [{"id": "1", "target": "a@example.com",
                                            "body": "你好 {{NAME}}"}]}), encoding="utf-8")
        with self.assertRaises(QueueRunError):
            run_queue(p, data_dir=self.dir)

    def test_email_queue_dry_run_delegates(self):
        p = self.dir / "queues" / "c1.json"
        p.write_text(json.dumps({"campaign": "c1", "channel": "email", "action": "message",
                                 "items": [{"id": "1", "target": "a@example.com", "body": "正文"}]}),
                     encoding="utf-8")
        res = run_queue(p, data_dir=self.dir, go=False, log=lambda *a, **k: None)
        self.assertEqual(["dry-run"], [r["result"] for r in res])

    def test_x_queue_only_reply(self):
        p = self.dir / "queues" / "c1.json"
        p.write_text(json.dumps({"campaign": "c1", "channel": "x", "action": "publish",
                                 "items": [{"id": "1", "target": "https://x.com/a/status/1",
                                            "body": "hi"}]}), encoding="utf-8")
        with self.assertRaises(QueueRunError):
            run_queue(p, data_dir=self.dir)


class TestCollector(Tmp):
    def _server(self, token=""):
        srv = collector.make_server(self.dir, port=0, token=token)
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        return srv

    def _post(self, port, payload, token=""):
        conn = HTTPConnection("127.0.0.1", port, timeout=5)
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        conn.request("POST", "/events", body=json.dumps(payload), headers=headers)
        r = conn.getresponse()
        body = json.loads(r.read().decode("utf-8"))
        conn.close()
        return r.status, body

    def test_webhook_event_and_token(self):
        srv = self._server(token="secret")
        port = srv.server_address[1]
        code, body = self._post(port, {"subject": "u1", "event_type": "signup",
                                       "campaign": "c1"})
        self.assertEqual(401, code)
        code, body = self._post(port, {"subject": "u1", "event_type": "signup",
                                       "campaign": "c1"}, token="secret")
        self.assertEqual(200, code)
        self.assertEqual("signup", body["record"]["event_type"])
        recs = OutcomeLedger(self.dir).records("c1")
        self.assertEqual(1, len(recs))

    def test_value_event_needs_verification(self):
        srv = self._server()
        code, _ = self._post(srv.server_address[1],
                             {"subject": "u1", "event_type": "paid", "campaign": "c1"})
        self.assertEqual(400, code)

    def test_link_click_records_signal_once(self):
        link = links.make_link(self.dir, url="https://example.com/p",
                               campaign="c1", channel="hn", content="show-hn")
        srv = self._server()
        port = srv.server_address[1]
        for _ in range(2):
            conn = HTTPConnection("127.0.0.1", port, timeout=5)
            conn.request("GET", f"/t/{link['id']}?cid=u1")
            r = conn.getresponse()
            self.assertEqual(302, r.status)
            self.assertIn("utm_campaign=c1", r.getheader("Location"))
            conn.close()
        signals = [r for r in OutcomeLedger(self.dir).records("c1")
                   if r.get("kind") == "event" and r.get("event_type") == "signal"]
        self.assertEqual(1, len(signals))
        self.assertEqual("link:" + link["id"], signals[0]["source_ref"])

    def test_plausible_counts_are_idempotent(self):
        p = self.dir / "plausible.csv"
        p.write_text("date,visitors,utm_campaign\n2026-10-31,12,c1\n", encoding="utf-8")
        res = collector.import_plausible(self.dir, p)
        self.assertEqual(1, res["created"])
        res2 = collector.import_plausible(self.dir, p)
        self.assertEqual(1, res2["skipped"])
        counts = [r for r in OutcomeLedger(self.dir).records() if r.get("kind") == "count"]
        self.assertEqual(12, counts[0]["value"])


class TestPostHogImport(Tmp):
    def test_event_mapping(self):
        p = self.dir / "posthog.csv"
        p.write_text("distinct_id,event,timestamp,utm_campaign\n"
                     "u1,user_signed_up,2026-10-31,c1\n"
                     "u1,activated,2026-10-31,c1\n", encoding="utf-8")
        res = outcome_io.import_csv(self.dir, p, campaign="c1",
                                    event_map={"user_signed_up": "signup",
                                               "activated": "activation"})
        self.assertEqual(1, res["imported"])  # activation 缺核验被拒
        self.assertEqual(1, len(res["errors"]))
        recs = OutcomeLedger(self.dir).records("c1")
        self.assertEqual("signup", recs[0]["event_type"])


if __name__ == "__main__":
    unittest.main()
