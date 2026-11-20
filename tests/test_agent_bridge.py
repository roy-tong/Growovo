#!/usr/bin/env python3
"""Agent 主入口桥接（handoff + 只读摘要）回归。

覆盖 G-SLICE-01（深链上下文）、G-SLICE-09（未知效果不冒充完成）
在桥接层的契约：对象快照、handoff 生命周期、TTL、回执幂等、
H0–H3 降级、风险摘要。全部在临时目录，零真实发送。
"""
import json
import tempfile
import unittest
from pathlib import Path

try:
    from support_gui_fixture import (LEDGER_REF, NOW, RUN_ID, TARGET, build)
except ImportError:  # python3 -m unittest tests.test_agent_bridge
    from tests.support_gui_fixture import (LEDGER_REF, NOW, RUN_ID, TARGET,
                                           build)

from core import agent_bridge as ab


class AgentBridgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.fx = build(Path(self.tmp.name))
        self.data = self.fx["data"]

    # ---- 对象快照 ----

    def test_object_snapshot_three_types(self):
        run = ab.object_snapshot(self.data, "run", RUN_ID)
        self.assertTrue(run["exists"])
        self.assertTrue(run["object_version"].startswith("sha256:"))
        self.assertEqual(run["status"], "active")

        case = ab.object_snapshot(self.data, "reply_case",
                                  self.fx["reply_case_id"])
        self.assertTrue(case["exists"])
        self.assertEqual(case["object_version"], self.fx["reply_version"])
        self.assertEqual(case["ux_state"], "NEEDS_REVIEW")
        self.assertEqual(case["detail"]["target"], TARGET)

        led = ab.object_snapshot(self.data, "ledger_ref", LEDGER_REF)
        self.assertTrue(led["exists"])
        self.assertEqual(led["status"], "indeterminate")
        self.assertEqual(led["next_actor"], "human")
        self.assertTrue(led["object_version"].startswith("sha256:"))

    def test_object_snapshot_missing_and_invalid(self):
        snap = ab.object_snapshot(self.data, "run", "missing")
        self.assertFalse(snap["exists"])
        with self.assertRaises(ab.BridgeError) as ctx:
            ab.object_snapshot(self.data, "nope", "x")
        self.assertEqual(ctx.exception.code, "INVALID_OBJECT_TYPE")

    # ---- handoff 生命周期 ----

    def test_create_handoff_defaults(self):
        hop = ab.create_handoff(
            self.data, object_type="run", object_id=RUN_ID,
            requested_action="run.pause", now=NOW)
        self.assertEqual(hop["schema"], ab.SCHEMA)
        self.assertEqual(hop["state"], "active")
        self.assertEqual(hop["entry_route"], "/runs/%s" % RUN_ID)
        self.assertEqual(hop["allowed_actions"], ["run.pause"])
        self.assertEqual(hop["presentation"], "H0")
        self.assertIs(hop["write_granted"], False)
        self.assertEqual(hop["expires_at"], "2026-11-19T10:15:00")

    def test_create_handoff_rejects_missing_object(self):
        with self.assertRaises(ab.BridgeError) as ctx:
            ab.create_handoff(self.data, object_type="ledger_ref",
                              object_id="nope")
        self.assertEqual(ctx.exception.code, "NOT_FOUND")

    def test_create_handoff_action_must_be_allowed(self):
        with self.assertRaises(ab.BridgeError) as ctx:
            ab.create_handoff(
                self.data, object_type="run", object_id=RUN_ID,
                requested_action="reply.dnc", allowed_actions=["run.pause"])
        self.assertEqual(ctx.exception.code, "INVALID_ACTION")
        self.assertIn("allowed_actions", ctx.exception.message)

    def test_create_handoff_ttl_bounds(self):
        for ttl in (ab.TTL_MIN - 1, ab.TTL_MAX + 1):
            with self.assertRaises(ab.BridgeError) as ctx:
                ab.create_handoff(self.data, object_type="run",
                                  object_id=RUN_ID, ttl_seconds=ttl)
            self.assertEqual(ctx.exception.code, "INVALID_TTL")
        with self.assertRaises(ab.BridgeError) as ctx:
            ab.create_handoff(self.data, object_type="run",
                              object_id=RUN_ID, ttl_seconds="soon")
        self.assertEqual(ctx.exception.code, "INVALID_TTL")

    def test_create_handoff_rejects_unsafe_entry_route(self):
        for route in ("//evil.example", "/runs/../secret", "runs/R1",
                      "/runs/R1?x=1 2"):
            with self.assertRaises(ab.BridgeError) as ctx:
                ab.create_handoff(self.data, object_type="run",
                                  object_id=RUN_ID, entry_route=route)
            self.assertEqual(ctx.exception.code, "INVALID_ROUTE", route)

    def test_create_handoff_rejects_remote_companion(self):
        with self.assertRaises(ab.BridgeError) as ctx:
            ab.create_handoff(self.data, object_type="run", object_id=RUN_ID,
                              companion_url="https://example.com")
        self.assertEqual(ctx.exception.code, "INVALID_COMPANION")

    def test_get_and_list_handoffs(self):
        first = ab.create_handoff(self.data, object_type="run",
                                  object_id=RUN_ID, now=NOW)
        second = ab.create_handoff(
            self.data, object_type="reply_case",
            object_id=self.fx["reply_case_id"], now=NOW)
        got = ab.get_handoff(self.data, first["handoff_ref"], now=NOW)
        self.assertEqual(got["handoff_ref"], first["handoff_ref"])
        listed = ab.list_handoffs(self.data, now=NOW)
        self.assertEqual([h["handoff_ref"] for h in listed],
                         [first["handoff_ref"], second["handoff_ref"]])
        with self.assertRaises(ab.BridgeError) as ctx:
            ab.get_handoff(self.data, "hid_missing")
        self.assertEqual(ctx.exception.code, "NOT_FOUND")

    def test_revoke_is_terminal_and_signed(self):
        hop = ab.create_handoff(self.data, object_type="run",
                                object_id=RUN_ID, now=NOW)
        ref = hop["handoff_ref"]
        with self.assertRaises(ab.BridgeError) as ctx:
            ab.revoke_handoff(self.data, ref, by="")
        self.assertEqual(ctx.exception.code, "BY_REQUIRED")

        revoked = ab.revoke_handoff(self.data, ref, by="owner",
                                    reason="换用新入口", now=NOW)
        self.assertEqual(revoked["state"], "revoked")
        self.assertEqual(revoked["revoked"]["by"], "owner")
        with self.assertRaises(ab.BridgeError) as ctx:
            ab.revoke_handoff(self.data, ref, by="owner", now=NOW)
        self.assertEqual(ctx.exception.code, "REVOKED")

    def test_handoff_expires_without_revocation(self):
        hop = ab.create_handoff(self.data, object_type="run",
                                object_id=RUN_ID, ttl_seconds=60, now=NOW)
        ref = hop["handoff_ref"]
        later = "2026-11-19T10:01:01"
        self.assertEqual(ab.get_handoff(self.data, ref, now=NOW)["state"],
                         "active")
        expired = ab.get_handoff(self.data, ref, now=later)
        self.assertEqual(expired["state"], "expired")
        self.assertTrue(expired["expired"])
        resolution = ab.resolve_handoff(self.data, ref, now=later)
        self.assertTrue(resolution["stale"])
        self.assertEqual(resolution["handoff_state"], "expired")

    # ---- 回执与回链 ----

    def test_receipt_idempotency_conflict(self):
        hop = ab.create_handoff(
            self.data, object_type="run", object_id=RUN_ID,
            requested_action="run.pause", now=NOW)
        ref = hop["handoff_ref"]
        record = ab.record_receipt(
            self.data, ref, action="run.pause", by="owner",
            reason="处理回复队列", result="paused",
            new_version="sha256:new", idempotency_key="idem-1",
            request_hash="hash-a", now=NOW)
        self.assertEqual(record["schema"], ab.RECEIPT_SCHEMA)
        found = ab.find_receipt(self.data, "idem-1", "hash-a")
        self.assertEqual(found["result"], "paused")

        with self.assertRaises(ab.BridgeError) as ctx:
            ab.record_receipt(
                self.data, ref, action="run.pause", by="owner",
                reason="重复提交", result="paused", new_version="sha256:new",
                idempotency_key="idem-1", request_hash="hash-a")
        self.assertEqual(ctx.exception.code, "DUPLICATE_RECEIPT")

        with self.assertRaises(ab.BridgeError) as ctx:
            ab.find_receipt(self.data, "idem-1", "hash-b")
        self.assertEqual(ctx.exception.code, "IDEMPOTENCY_CONFLICT")

    def test_receipt_requires_signature_and_version(self):
        hop = ab.create_handoff(
            self.data, object_type="run", object_id=RUN_ID,
            requested_action="run.pause", now=NOW)
        ref = hop["handoff_ref"]
        with self.assertRaises(ab.BridgeError) as ctx:
            ab.record_receipt(self.data, ref, action="run.pause", by="",
                              reason="x", result="paused",
                              new_version="sha256:new")
        self.assertEqual(ctx.exception.code, "BY_REQUIRED")
        with self.assertRaises(ab.BridgeError) as ctx:
            ab.record_receipt(self.data, ref, action="run.pause", by="owner",
                              reason="x", result="paused", new_version="")
        self.assertEqual(ctx.exception.code, "INVALID_VERSION")
        with self.assertRaises(ab.BridgeError) as ctx:
            ab.record_receipt(self.data, ref, action="dnc.release",
                              by="owner", reason="x", result="ok",
                              new_version="sha256:new")
        self.assertEqual(ctx.exception.code, "INVALID_ACTION")

    def test_resolve_reports_change_and_receipts(self):
        hop = ab.create_handoff(
            self.data, object_type="run", object_id=RUN_ID,
            requested_action="run.pause", now=NOW)
        ref = hop["handoff_ref"]
        before = ab.resolve_handoff(self.data, ref, now=NOW)
        self.assertFalse(before["changed"])
        self.assertEqual(before["previous_version"],
                         before["current_version"])
        self.assertIs(before["read_only"], True)
        self.assertIs(before["write_granted"], False)

        path = self.data / "grow" / ("%s.json" % RUN_ID)
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["status"] = "paused"
        path.write_text(json.dumps(payload, ensure_ascii=False),
                        encoding="utf-8")
        ab.record_receipt(
            self.data, ref, action="run.pause", by="owner",
            reason="处理回复队列", result="paused",
            new_version=ab.object_snapshot(self.data, "run", RUN_ID)[
                "object_version"], now=NOW)

        after = ab.resolve_handoff(self.data, ref, now=NOW)
        self.assertTrue(after["changed"])
        self.assertNotEqual(after["previous_version"],
                            after["current_version"])
        self.assertEqual(after["canonical_status"], "paused")
        self.assertEqual(len(after["action_receipts"]), 1)
        self.assertEqual(after["action_receipts"][0]["action"], "run.pause")

    # ---- 呈现级别与深链 ----

    def test_presentation_levels(self):
        no_url = ab.presentation_for("")
        self.assertEqual(no_url["level"], "H0")
        self.assertEqual(no_url["companion_url"], "")

        with_url = ab.presentation_for(
            "agent", companion_url="http://127.0.0.1:8765")
        self.assertEqual(with_url["level"], "H1")
        self.assertEqual(with_url["companion_url"], "http://127.0.0.1:8765")

        embedded = ab.presentation_for(
            "codex", capabilities={"mcp_apps": True},
            companion_url="http://localhost:8765")
        self.assertEqual(embedded["level"], "H2")
        self.assertIs(embedded["embedded"], True)
        self.assertEqual(embedded["ui_resource"], ab.UI_RESOURCE)

        direct = ab.presentation_for(
            "gui", capabilities={"direct_workbench": True},
            companion_url="http://127.0.0.1:8765")
        self.assertEqual(direct["level"], "H3")

        remote = ab.presentation_for(
            "agent", capabilities={"remote": True},
            companion_url="http://127.0.0.1:8765")
        self.assertEqual(remote["level"], "H0")
        self.assertEqual(remote["companion_url"], "")
        self.assertIn("本机", remote["fallback"])

    def test_deep_link_joins_local_base(self):
        self.assertEqual(
            ab.deep_link("http://127.0.0.1:8765/", "/runs/%s" % RUN_ID),
            "http://127.0.0.1:8765/runs/%s" % RUN_ID)
        self.assertEqual(ab.deep_link("", "/runs/%s" % RUN_ID),
                         "/runs/%s" % RUN_ID)
        with self.assertRaises(ab.BridgeError):
            ab.deep_link("https://example.com", "/runs/%s" % RUN_ID)

    # ---- 风险摘要 ----

    def test_risk_summary_structure_and_links(self):
        summary = ab.risk_summary(
            self.data, now=NOW, companion_url="http://127.0.0.1:8765")
        self.assertEqual(summary["schema"], "growovo.agent-risk-summary/1")
        self.assertIs(summary["read_only"], True)
        self.assertEqual(summary["presentation"]["level"], "H1")
        self.assertEqual(summary["totals"]["runs"], 1)
        self.assertEqual(summary["totals"]["blocking_incidents"], 1)
        self.assertEqual(summary["totals"]["replies_pending"], 1)

        run = summary["runs"][0]
        self.assertEqual(run["run_id"], RUN_ID)
        self.assertEqual(run["canonical_status"], "active")
        self.assertEqual(run["ux_state"], "BLOCKED")
        self.assertEqual(run["next_actor"], "human")
        self.assertEqual(run["routes"]["run"], "/runs/%s" % RUN_ID)
        self.assertTrue(run["routes"]["run_link"].startswith(
            "http://127.0.0.1:8765/runs/"))
        self.assertTrue(run["replies"]["pending_review"] >= 1)
        self.assertTrue(run["incidents"]["blocking"] >= 1)

        suggestion = summary["handoff_suggestion"]
        self.assertEqual(suggestion["object_type"], "run")
        self.assertIn(suggestion["requested_action"], ab.ALLOWED_ACTIONS)
        self.assertIn(suggestion["requested_action"],
                      suggestion["allowed_actions"])
        self.assertEqual(suggestion["presentation"], "H1")

    def test_risk_summary_without_companion_uses_routes_only(self):
        summary = ab.risk_summary(self.data, now=NOW)
        self.assertEqual(summary["presentation"]["level"], "H0")
        run = summary["runs"][0]
        self.assertEqual(run["routes"]["run_link"], run["routes"]["run"])
        self.assertEqual(run["routes"]["replies_link"], run["routes"]["replies"])
        self.assertFalse(run["routes"]["run_link"].startswith("http"))
        self.assertEqual(summary["handoff_suggestion"]["companion_url"], "")

    def test_risk_summary_missing_run(self):
        with self.assertRaises(ab.BridgeError) as ctx:
            ab.risk_summary(self.data, run_id="ghost", now=NOW)
        self.assertEqual(ctx.exception.code, "NOT_FOUND")

    # ---- 零发送 ----

    def test_bridge_never_sends_or_touches_suppression(self):
        ab.create_handoff(self.data, object_type="reply_case",
                          object_id=self.fx["reply_case_id"], now=NOW)
        ab.risk_summary(self.data, now=NOW)
        self.assertFalse((self.data / "contacts.jsonl").exists())
        self.assertFalse((self.data / "outbox").exists())
        names = {p.name for p in self.data.rglob("*") if p.is_file()}
        self.assertFalse({n for n in names if "sent" in n.lower()}, names)


if __name__ == "__main__":
    unittest.main()
