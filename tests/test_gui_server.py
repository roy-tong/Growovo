#!/usr/bin/env python3
"""伴随 GUI（本地只读投影＋受控写）端到端回归：G-SLICE-01…12。

全部走真实 HTTP：起一个 ThreadingHTTPServer，用 http.client 请求。
每个用例独立临时数据目录，零真实发送、零外部调用。
"""
import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path

try:
    from support_gui_fixture import LEDGER_REF, NOW, RUN_ID, TARGET, build
except ImportError:  # python3 -m unittest tests.test_gui_server
    from tests.support_gui_fixture import (LEDGER_REF, NOW, RUN_ID, TARGET,
                                           build)

from core import agent_bridge, grow_runbook, gui_server, reply_case, safety


class GuiServerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.fx = build(Path(self.tmp.name))
        self.data = self.fx["data"]
        self.cid = self.fx["reply_case_id"]
        self.server = gui_server.make_server(self.data, port=0, token="tok")
        self.thread = threading.Thread(
            target=self.server.serve_forever,
            kwargs={"poll_interval": 0.05}, daemon=True)
        self.thread.start()
        self.addCleanup(self._stop)
        self.port = self.server.server_address[1]
        self.base = self.server.base_url

    def _stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    # ---- HTTP 助手 ----

    def req(self, method, path, body=None, token="tok",
            ctype="application/json"):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            payload = None
            headers = {}
            if body is not None:
                payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
                headers["Content-Type"] = ctype
                headers["Content-Length"] = str(len(payload))
            if token is not None:
                headers["X-Growovo-Gui-Token"] = token
            conn.request(method, path, body=payload, headers=headers)
            resp = conn.getresponse()
            raw = resp.read()
            hdrs = {k.lower(): v for k, v in resp.getheaders()}
            if "json" in hdrs.get("content-type", ""):
                parsed = json.loads(raw.decode("utf-8")) if raw else None
            else:
                parsed = raw.decode("utf-8", "replace")
            return resp.status, parsed, hdrs
        finally:
            conn.close()

    def make_handoff(self, object_type, object_id, actions, requested=""):
        # 不传 now：过期以真实时钟判定，避免固定 NOW 造成一创建就过期。
        return agent_bridge.create_handoff(
            self.data, object_type=object_type, object_id=object_id,
            requested_action=requested, allowed_actions=list(actions),
            companion_url=self.base, ttl_seconds=3600)

    def review_payload(self, **overrides):
        payload = {
            "object_id": self.cid,
            "object_version": self.fx["reply_version"],
            "by": "alice",
            "label": "unsubscribe",
            "reason": "原文明确要求退订",
            "idempotency_key": "idem-review-1",
        }
        payload.update(overrides)
        return payload

    # ---- G-SLICE-01 深链上下文 ----

    def test_g_slice_01_deeplink_keeps_context(self):
        hop = self.make_handoff(
            "reply_case", self.cid, ["reply.review"], "reply.review")
        ref = hop["handoff_ref"]

        status, _, headers = self.req("GET", "/handoff/%s" % ref)
        self.assertEqual(status, 302)
        location = headers["location"]
        self.assertIn("/replies/", location)
        self.assertIn("handoff=", location)

        status, _, headers = self.req("GET", location)
        self.assertEqual(status, 302)
        location = headers["location"]
        self.assertIn("/runs/%s/replies" % RUN_ID, location)
        self.assertIn("case=%s" % self.cid, location)
        self.assertIn("handoff=", location)

        status, page, _ = self.req("GET", location)
        self.assertEqual(status, 200)
        self.assertIn("本页由 handoff", page)

        status, body, _ = self.req("GET", "/api/handoff/%s" % ref)
        self.assertEqual(status, 200)
        self.assertFalse(body["changed"])
        self.assertIs(body["read_only"], True)
        self.assertIs(body["write_granted"], False)
        self.assertEqual(body["action_receipts"], [])

    # ---- G-SLICE-02 存储损坏显式呈现 ----

    def test_g_slice_02_unreadable_store_is_explicit(self):
        cases = self.data / "replies" / "reply-cases.jsonl"
        with cases.open("a", encoding="utf-8") as fh:
            fh.write("not json\n")
        status, page, _ = self.req("GET", "/runs/%s/replies" % RUN_ID)
        self.assertEqual(status, 200)
        self.assertNotIn("Traceback", page)
        self.assertTrue(any(m in page for m in (
            "回复队列不可读", "投影不可读", "回复存储不可读")), page[:400])

        status, body, _ = self.req("GET", "/api/replies?run=%s" % RUN_ID)
        self.assertEqual(status, 200)
        self.assertEqual(body["projection"]["state"], "unreadable")
        self.assertTrue(body["projection"]["error"])

    # ---- G-SLICE-03 保存必须写后核验 ----

    def test_g_slice_03_write_is_verified(self):
        status, body, _ = self.req(
            "POST", "/api/reply/review", self.review_payload())
        self.assertEqual(status, 200)
        self.assertEqual(body["status"], "ok")
        self.assertIs(body["verification"]["ok"], True)
        self.assertEqual(body["data"]["version"], 2)
        self.assertEqual(body["new_version"], "v2")
        self.assertEqual(body.get("receipt_at") or "", "")
        self.assertIs(body["replayed"], False)

    # ---- G-SLICE-04 具名改判追加新版本 ----

    def test_g_slice_04_review_appends_version(self):
        path = self.data / "replies" / "reply-cases.jsonl"
        before = len(path.read_text(encoding="utf-8").splitlines())
        status, body, _ = self.req(
            "POST", "/api/reply/review", self.review_payload())
        self.assertEqual(status, 200)

        case = reply_case.get_case(self.data, self.cid)
        self.assertEqual(int(case.get("version")), 2)
        self.assertEqual(case.get("human_label"), "unsubscribe")
        self.assertEqual(case.get("reviewer"), "alice")
        self.assertEqual(case.get("review_state"), "overruled")
        after = len(path.read_text(encoding="utf-8").splitlines())
        self.assertEqual(after, before + 1)

    # ---- G-SLICE-05 DNC 生效并落抑制集 ----

    def test_g_slice_05_dnc_active(self):
        status, body, _ = self.req("POST", "/api/reply/dnc", {
            "object_id": self.cid,
            "object_version": self.fx["reply_version"],
            "by": "alice",
            "reason": "对方明确要求停止联系",
            "idempotency_key": "idem-dnc-1",
        })
        self.assertEqual(status, 200)
        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["data"]["dnc_state"], "active")
        self.assertTrue(body["data"]["dnc_ref"])
        self.assertTrue(body["data"]["contact_id"])
        contacts = (self.data / "contacts.jsonl").read_text(encoding="utf-8")
        self.assertIn(TARGET, contacts)
        self.assertFalse(safety.is_paused(self.data))

    # ---- G-SLICE-06 双通道失败显式失败并暂停 ----

    def test_g_slice_06_dnc_double_failure(self):
        (self.data / "contacts.jsonl").mkdir()
        (self.data / "suppress.txt").mkdir()
        status, body, _ = self.req("POST", "/api/reply/dnc", {
            "object_id": self.cid,
            "object_version": self.fx["reply_version"],
            "by": "alice",
            "reason": "对方明确要求停止联系",
            "idempotency_key": "idem-dnc-fail",
        })
        self.assertEqual(status, 422)
        self.assertEqual(body["status"], "failed")
        self.assertEqual(body["data"]["dnc_state"], "failed")
        self.assertTrue(body["data"]["paused_path"])
        self.assertTrue((self.data / "state" / "PAUSED").is_file())
        self.assertTrue(safety.is_paused(self.data))

        case = reply_case.get_case(self.data, self.cid)
        self.assertEqual(case.get("dnc_state"), "failed")
        self.assertEqual(int(case.get("version")), 2)

    # ---- G-SLICE-07 抑制集缺目标时风险可见 ----

    def test_g_slice_07_dnc_unverified_surfaces(self):
        status, body, _ = self.req("POST", "/api/reply/dnc", {
            "object_id": self.cid,
            "object_version": self.fx["reply_version"],
            "by": "alice",
            "reason": "对方明确要求停止联系",
            "idempotency_key": "idem-dnc-verify",
        })
        self.assertEqual(status, 200)
        (self.data / "contacts.jsonl").write_text("", encoding="utf-8")
        self.assertFalse((self.data / "suppress.txt").exists())

        status, summary, _ = self.req("GET", "/api/risk")
        self.assertEqual(status, 200)
        self.assertGreaterEqual(summary["totals"]["dnc_unverified"], 1)
        run = summary["runs"][0]
        self.assertIsInstance(run["replies"]["dnc"]["unverified"], int)
        self.assertGreaterEqual(run["replies"]["dnc"]["unverified"], 1)
        codes = [risk.get("code") for risk in run["risks"]]
        self.assertIn("DNC_UNVERIFIED", codes)

        status, body, _ = self.req("GET", "/api/replies?run=%s" % RUN_ID)
        self.assertEqual(status, 200)
        unverified = body["projection"]["dnc"]["unverified"]
        self.assertIsInstance(unverified, list)
        self.assertTrue(unverified)

    # ---- G-SLICE-08 旧版本写入必须 409 ----

    def test_g_slice_08_stale_version_conflicts(self):
        status, _, _ = self.req(
            "POST", "/api/reply/review", self.review_payload())
        self.assertEqual(status, 200)

        status, body, _ = self.req("POST", "/api/reply/review",
                                   self.review_payload(
                                       idempotency_key="idem-review-2",
                                       reason="第二次改判"))
        self.assertEqual(status, 409)
        self.assertEqual(body["status"], "conflict")
        self.assertEqual(body["code"], "VERSION_CONFLICT")
        self.assertEqual(body["current_version"], "v2")

        case = reply_case.get_case(self.data, self.cid)
        self.assertEqual(int(case.get("version")), 2)

    # ---- G-SLICE-09 未裁决不得恢复 ----

    def test_g_slice_09_resume_blocked_by_incident(self):
        run_version = agent_bridge.object_snapshot(
            self.data, "run", RUN_ID)["object_version"]
        status, body, _ = self.req("POST", "/api/run/pause", {
            "object_id": RUN_ID, "object_version": run_version,
            "by": "alice", "reason": "出现未知效果，先停",
            "idempotency_key": "idem-pause-9",
        })
        self.assertEqual(status, 200)
        self.assertEqual(body["data"]["status"], "paused")

        paused_version = agent_bridge.object_snapshot(
            self.data, "run", RUN_ID)["object_version"]
        status, body, _ = self.req("POST", "/api/run/resume", {
            "object_id": RUN_ID, "object_version": paused_version,
            "by": "alice", "reason": "尝试恢复",
            "idempotency_key": "idem-resume-9",
        })
        self.assertEqual(status, 409)
        self.assertEqual(body["code"], "INDETERMINATE_EXTERNAL_ACTION")

        snap = agent_bridge.object_snapshot(self.data, "run", RUN_ID)
        self.assertEqual(snap["status"], "paused")
        ledger = (self.data / "ledger.jsonl").read_text(encoding="utf-8")
        self.assertEqual(len(ledger.splitlines()), 1)

    # ---- G-SLICE-10 裁决后恢复＋双回执 ----

    def test_g_slice_10_resolve_then_resume(self):
        ledger_hop = self.make_handoff(
            "ledger_ref", LEDGER_REF, ["ledger.resolve"], "ledger.resolve")
        run_hop = self.make_handoff(
            "run", RUN_ID, ["run.resume"], "run.resume")

        run_version = agent_bridge.object_snapshot(
            self.data, "run", RUN_ID)["object_version"]
        status, _, _ = self.req("POST", "/api/run/pause", {
            "object_id": RUN_ID, "object_version": run_version,
            "by": "alice", "reason": "出现未知效果，先停",
            "idempotency_key": "idem-pause-10",
        })
        self.assertEqual(status, 200)
        incident_id = grow_runbook.blocking_incidents(
            self.data, self.data / "grow" / ("%s.json" % RUN_ID))[0]["event_id"]

        ledger_version = agent_bridge.object_snapshot(
            self.data, "ledger_ref", LEDGER_REF)["object_version"]
        status, body, _ = self.req("POST", "/api/ledger/resolve", {
            "object_id": LEDGER_REF, "object_version": ledger_version,
            "by": "alice", "result": "committed",
            "note": "平台后台已确认发出",
            "handoff_ref": ledger_hop["handoff_ref"],
            "idempotency_key": "idem-ledger-10",
        })
        self.assertEqual(status, 200)
        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["data"]["ledger_status"], "committed")
        self.assertTrue(body.get("receipt_at"))
        self.assertTrue(
            (self.data / "ledger_resolutions.jsonl").is_file())

        paused_version = agent_bridge.object_snapshot(
            self.data, "run", RUN_ID)["object_version"]
        status, body, _ = self.req("POST", "/api/run/resume", {
            "object_id": RUN_ID, "object_version": paused_version,
            "by": "alice", "reason": "账本已裁决为 committed",
            "incident_id": incident_id,
            "handoff_ref": run_hop["handoff_ref"],
            "idempotency_key": "idem-resume-10",
        })
        self.assertEqual(status, 200)
        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["data"]["status"], "active")
        self.assertIs(body["data"]["resumed"], True)

        for hop, action in ((ledger_hop, "ledger.resolve"),
                            (run_hop, "run.resume")):
            stored = agent_bridge.get_handoff(
                self.data, hop["handoff_ref"])
            self.assertEqual(len(stored["receipts"]), 1)
            self.assertEqual(stored["receipts"][0]["action"], action)
            resolved = agent_bridge.resolve_handoff(
                self.data, hop["handoff_ref"])
            self.assertIs(resolved["changed"], True)

        run = json.loads(
            (self.data / "grow" / ("%s.json" % RUN_ID))
            .read_text(encoding="utf-8"))
        self.assertEqual([h["kind"] for h in run["history"]],
                         ["launch", "pause", "resume"])

    # ---- G-SLICE-11 外部文本只转义不执行 ----

    def test_g_slice_11_injected_text_is_escaped(self):
        csv = self.data / "replies.csv"
        csv.write_text(
            "ref,text,ts,from,run,action\n"
            'msg-2,"<script>alert(1)</script>",2026-11-19T09:40:00,'
            "evil@example.com,%s,action-2\n" % RUN_ID, encoding="utf-8")
        reply_case.import_replies(
            self.data, csv, account_id="acc-1", channel="email",
            imported_by="owner", authorization_ref="ticket-43",
            run_id=RUN_ID, now=NOW)
        injected = [c for c in reply_case.list_cases(self.data, run_id=RUN_ID)
                    if "<script>" in (c.get("raw_text") or "")]
        self.assertEqual(len(injected), 1)

        status, page, _ = self.req(
            "GET", "/runs/%s/replies?case=%s"
            % (RUN_ID, injected[0]["reply_case_id"]))
        self.assertEqual(status, 200)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", page)
        self.assertNotIn("<script>alert(1)</script>", page)

    # ---- G-SLICE-12 端到端零发送＋回链 ----

    def test_g_slice_12_end_to_end_no_send(self):
        hop = self.make_handoff(
            "reply_case", self.cid, ["reply.review"], "reply.review")
        status, body, _ = self.req("POST", "/api/reply/review",
                                   self.review_payload(
                                       handoff_ref=hop["handoff_ref"]))
        self.assertEqual(status, 200)
        self.assertTrue(body.get("receipt_at"))

        resolved = agent_bridge.resolve_handoff(
            self.data, hop["handoff_ref"])
        self.assertIs(resolved["changed"], True)
        self.assertTrue(resolved["action_receipts"])
        self.assertEqual(resolved["action_receipts"][0]["action"],
                         "reply.review")
        self.assertEqual(resolved["current_version"], "v2")

        self.assertFalse((self.data / "contacts.jsonl").exists())
        for path in self.data.rglob("*"):
            name = path.name.lower()
            self.assertNotIn("outbox", name)
            self.assertNotIn("sent", name)

        other = gui_server.make_server(self.data, port=0, token="tok2")
        other_port = other.server_address[1]
        other.server_close()
        with self.assertRaises(OSError):
            probe = http.client.HTTPConnection(
                "127.0.0.1", other_port, timeout=2)
            try:
                probe.request("GET", "/runs")
                probe.getresponse()
            finally:
                probe.close()

        status, _, _ = self.req("GET", "/runs")
        self.assertEqual(status, 200)

    # ---- 通用守卫 ----

    def test_post_requires_token_and_json(self):
        status, body, _ = self.req(
            "POST", "/api/reply/review", self.review_payload(), token=None)
        self.assertEqual(status, 403)
        self.assertEqual(body["code"], "TOKEN_REQUIRED")

        status, body, _ = self.req(
            "POST", "/api/reply/review", self.review_payload(),
            ctype="text/plain")
        self.assertEqual(status, 400)
        self.assertEqual(body["code"], "CONTENT_TYPE")

    def test_handoff_limits_actions(self):
        hop = self.make_handoff("run", RUN_ID, ["run.pause"])
        version = agent_bridge.object_snapshot(
            self.data, "run", RUN_ID)["object_version"]
        status, body, _ = self.req("POST", "/api/run/resume", {
            "object_id": RUN_ID, "object_version": version,
            "by": "alice", "reason": "越权尝试",
            "handoff_ref": hop["handoff_ref"],
            "idempotency_key": "idem-resume-deny",
        })
        self.assertEqual(status, 403)
        self.assertEqual(body["code"], "ACTION_NOT_ALLOWED")

    def test_idempotent_replay(self):
        version = agent_bridge.object_snapshot(
            self.data, "run", RUN_ID)["object_version"]
        payload = {
            "object_id": RUN_ID, "object_version": version,
            "by": "alice", "reason": "先停一轮",
            "idempotency_key": "idem-pause-replay",
        }
        status, first, _ = self.req("POST", "/api/run/pause", payload)
        self.assertEqual(status, 200)
        self.assertIs(first["replayed"], False)

        status, second, _ = self.req("POST", "/api/run/pause", payload)
        self.assertEqual(status, 200)
        self.assertIs(second["replayed"], True)
        self.assertEqual(second["new_version"], first["new_version"])

        status, third, _ = self.req("POST", "/api/run/pause", dict(
            payload, reason="换个理由"))
        self.assertEqual(status, 409)
        self.assertEqual(third["code"], "IDEMPOTENCY_CONFLICT")

        run = json.loads(
            (self.data / "grow" / ("%s.json" % RUN_ID))
            .read_text(encoding="utf-8"))
        self.assertEqual([h["kind"] for h in run["history"]].count("pause"), 1)

    def test_read_only_projections_and_pages(self):
        status, body, _ = self.req("GET", "/api/replies?run=%s" % RUN_ID)
        self.assertEqual(status, 200)
        self.assertEqual(body["schema"], "growovo.reply-projection/1")
        self.assertEqual(body["projection"]["pending_review"], 1)
        self.assertEqual(body["projection"]["count"], 1)
        self.assertIs(body["read_only"], True)

        status, body, _ = self.req("GET", "/api/replies?case=%s" % self.cid)
        self.assertEqual(status, 200)
        self.assertEqual(body["schema"], "growovo.reply-case/1")

        status, detail, _ = self.req("GET", "/api/run/%s" % RUN_ID)
        self.assertEqual(status, 200)
        self.assertTrue(detail["schema"])

        for path in ("/runs", "/runs/%s" % RUN_ID,
                     "/runs/%s/replies" % RUN_ID,
                     "/runs/%s/incidents" % RUN_ID,
                     "/ledger?ref=%s" % LEDGER_REF):
            status, page, _ = self.req("GET", path)
            self.assertEqual(status, 200, path)
            self.assertNotIn("Traceback", page, path)

        status, page, _ = self.req("GET", "/runs")
        self.assertIn(gui_server.BOUNDARY, page)

        for path in ("/api", "/api/nope", "/api/handoff/nope"):
            status, _, _ = self.req("GET", path)
            self.assertEqual(status, 404, path)

        status, _, headers = self.req("GET", "/")
        self.assertEqual(status, 302)
        self.assertEqual(headers["location"], "/runs")


if __name__ == "__main__":
    unittest.main()
