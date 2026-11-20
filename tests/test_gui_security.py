#!/usr/bin/env python3
"""G-GUI-01：伴随 GUI 的 Host / Origin / Token 边界。

全部走真实 HTTP；对抗用例覆盖恶意 Host、DNS rebinding 形式 Host、跨站
Origin 与空 token 启动。写请求被拒绝后还要证明业务对象没有被改动。
"""
import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path

try:
    from support_gui_fixture import build
except ImportError:  # python3 -m unittest tests.test_gui_security
    from tests.support_gui_fixture import build

from core import gui_server


class GuiSecurityTests(unittest.TestCase):
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

    def _stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def req(self, method, path, body=None, token="tok", host=None,
            origin=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            payload = None
            headers = {}
            if host is not None:
                headers["Host"] = host
            if origin is not None:
                headers["Origin"] = origin
            if body is not None:
                payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
                headers["Content-Type"] = "application/json"
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

    def review_payload(self):
        return {
            "object_id": self.cid,
            "object_version": self.fx["reply_version"],
            "by": "alice",
            "label": "unsubscribe",
            "reason": "原文明确要求退订",
            "idempotency_key": "idem-security-1",
        }

    def case_version(self):
        status, body, _ = self.req("GET", "/api/replies?case=%s" % self.cid)
        self.assertEqual(200, status)
        return body["case"]["version"]

    # ---- Host 白名单 ----

    def test_loopback_hosts_are_accepted(self):
        for host in ("127.0.0.1:%d" % self.port,
                     "localhost:%d" % self.port,
                     "[::1]:%d" % self.port):
            status, _, _ = self.req("GET", "/runs", host=host)
            self.assertEqual(200, status, host)

    def test_foreign_host_is_rejected_for_get(self):
        status, body, _ = self.req("GET", "/runs", host="evil.example:%d" % self.port)
        self.assertEqual(403, status)
        self.assertIn("HOST_FORBIDDEN", body)

    def test_dns_rebinding_host_is_rejected_even_when_it_resolves_here(self):
        # 请求实际连到 127.0.0.1，但 Host 是攻击者域名：重绑定场景必须拒绝。
        status, body, _ = self.req("GET", "/api/risk",
                                   host="rebind.attacker.test:%d" % self.port)
        self.assertEqual(403, status)
        self.assertEqual("HOST_FORBIDDEN", body["code"])

    def test_loopback_ip_with_foreign_port_is_rejected(self):
        status, body, _ = self.req("GET", "/runs",
                                   host="127.0.0.1:1")
        self.assertEqual(403, status)
        self.assertIn("HOST_FORBIDDEN", body)

    def test_missing_host_header_is_rejected(self):
        status, body, _ = self.req("GET", "/runs", host="")
        self.assertEqual(403, status)
        self.assertIn("HOST_FORBIDDEN", body)

    # ---- Origin 与写接口 ----

    def test_cross_site_origin_is_rejected_and_write_does_not_apply(self):
        before = self.case_version()
        status, body, _ = self.req(
            "POST", "/api/reply/review", body=self.review_payload(),
            origin="https://evil.example")
        self.assertEqual(403, status)
        self.assertEqual("ORIGIN_FORBIDDEN", body["code"])
        self.assertEqual(before, self.case_version())

    def test_null_origin_is_rejected(self):
        status, body, _ = self.req(
            "POST", "/api/reply/review", body=self.review_payload(),
            origin="null")
        self.assertEqual(403, status)
        self.assertEqual("ORIGIN_FORBIDDEN", body["code"])

    def test_same_origin_write_is_accepted(self):
        status, body, _ = self.req(
            "POST", "/api/reply/review", body=self.review_payload(),
            origin="http://127.0.0.1:%d" % self.port)
        self.assertEqual(200, status)
        self.assertEqual("ok", body["status"])

    def test_write_without_origin_is_accepted_for_agent_clients(self):
        status, body, _ = self.req(
            "POST", "/api/reply/review", body=self.review_payload())
        self.assertEqual(200, status)
        self.assertEqual("ok", body["status"])

    def test_wrong_token_is_still_rejected_with_valid_origin(self):
        status, body, _ = self.req(
            "POST", "/api/reply/review", body=self.review_payload(),
            token="wrong", origin="http://127.0.0.1:%d" % self.port)
        self.assertEqual(403, status)
        self.assertEqual("TOKEN_REQUIRED", body["code"])

    # ---- 启动时 token 不得为空 ----

    def test_empty_token_refuses_to_start(self):
        for value in ("", "   "):
            with self.assertRaises(gui_server.GuiError) as ctx:
                gui_server.make_server(self.data, port=0, token=value)
            self.assertEqual("TOKEN_REQUIRED", ctx.exception.code)

    def test_gui_server_constructor_rejects_empty_token(self):
        with self.assertRaises(gui_server.GuiError) as ctx:
            gui_server.GuiServer(self.data, ("127.0.0.1", 0), "")
        self.assertEqual("TOKEN_REQUIRED", ctx.exception.code)


if __name__ == "__main__":
    unittest.main()
