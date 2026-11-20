#!/usr/bin/env python3
"""G-01b 冻结测试：真实 CDP 身份读取、旧授权收敛、三入口一致。

本文件先于修复编写，针对当前三条现状应当失败：

1. `core/account_identity.read_proof_from_browser()` 直接返回 None，没有可测的
   真实读取器；这里冻结一个「只走回环 CDP、默认关闭、拿不到稳定数字 ID 就
   fail closed」的读取契约（假客户端 + 只绑 127.0.0.1 临时端口的假服务端，
   零外网）。
2. 旧 policy（可读、但没有 require_identity / allow_accounts / 身份记录）在
   真实发送路径上 `enforce_identity` 返回 `enforced=False`，x vendor 照常被
   调用。这里冻结：旧授权 + 真实发送必须拒绝（AUTH_REQUIRED），外部调用 0 次；
   dry-run 只报阻塞；`mkt policy migrate-identity` 提供迁移入口；`doctor` 可见。
3. 队列 / 单动作 `mkt x --go` / MCP 三条入口必须走同一身份门禁，回执带
   authorized_account / observed_platform_identity / identity_proof_ref。

所有发送边界都是本机假 vendor（真实 subprocess，不 mock run_action/adapter）；
所有 CDP 服务端只监听 127.0.0.1 临时端口，不联网。
"""
import base64
import contextlib
import hashlib
import io
import json
import os
import re
import socket
import socketserver
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import mkt  # noqa: E402
import mcp_server  # noqa: E402
from channels.x import adapter  # noqa: E402
from core import account_identity, cdp_identity  # noqa: E402
try:  # discover -s tests puts tests/ on sys.path
    from support_compliance import bypass_x_compliance
except ImportError:  # python -m unittest tests.<module>
    from tests.support_compliance import bypass_x_compliance
from core.account_identity import AccountIdentityProof  # noqa: E402
from core.execution_policy import authorize, get_policy  # noqa: E402
from core.queue_runner import QueueRunError, run_queue  # noqa: E402

X_REPLY_TARGET = "https://x.com/example/status/1"


# --------------------------------------------------------------------------- #
# 本机假 CDP 服务端（只绑 127.0.0.1 临时端口）
# --------------------------------------------------------------------------- #

_WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


def _recv_exact(sock, size):
    buf = b""
    while len(buf) < size:
        chunk = sock.recv(size - len(buf))
        if not chunk:
            raise ConnectionError("closed")
        buf += chunk
    return buf


def _read_frame(sock):
    first = _recv_exact(sock, 2)
    opcode, masked, length = first[0] & 0x0F, bool(first[1] & 0x80), first[1] & 0x7F
    if length == 126:
        length = struct.unpack("!H", _recv_exact(sock, 2))[0]
    elif length == 127:
        length = struct.unpack("!Q", _recv_exact(sock, 8))[0]
    mask = _recv_exact(sock, 4) if masked else b"\x00\x00\x00\x00"
    payload = _recv_exact(sock, length) if length else b""
    if masked:
        payload = bytes(byte ^ mask[i % 4] for i, byte in enumerate(payload))
    return opcode, payload


def _send_text(sock, text):
    data = text.encode("utf-8")
    header = bytearray([0x81])
    if len(data) < 126:
        header.append(len(data))
    elif len(data) < 65536:
        header.extend([126])
        header.extend(struct.pack("!H", len(data)))
    else:
        header.extend([127])
        header.extend(struct.pack("!Q", len(data)))
    sock.sendall(bytes(header) + data)


def start_fake_ws(payloads, *, mode="ok"):
    """起一个只监听 127.0.0.1 的假 CDP WebSocket 服务端，返回 (server, port)。"""

    class Handler(socketserver.BaseRequestHandler):
        def handle(self):  # noqa: C901 - 协议桩，保持直白
            self.request.settimeout(10)
            raw = b""
            while b"\r\n\r\n" not in raw:
                chunk = self.request.recv(4096)
                if not chunk:
                    return
                raw += chunk
            head = raw.split(b"\r\n\r\n")[0].decode("latin-1")
            key = re.search(r"(?im)^Sec-WebSocket-Key:\s*(.+?)\s*$", head).group(1)
            accept = base64.b64encode(
                hashlib.sha1((key + _WS_GUID).encode()).digest()).decode()
            self.request.sendall((
                "HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n"
                "Connection: Upgrade\r\nSec-WebSocket-Accept: %s\r\n\r\n" % accept
            ).encode())
            if mode == "stall":
                time.sleep(2.0)
                return
            while True:
                try:
                    opcode, payload = _read_frame(self.request)
                except (OSError, ConnectionError):
                    return
                if opcode == 8:
                    return
                if opcode == 9:
                    self.request.sendall(b"\x8a\x00")
                    continue
                if opcode != 1:
                    continue
                message = json.loads(payload.decode("utf-8"))
                expression = message["params"]["expression"]
                # The verify_credentials expression also reads document.cookie
                # (for the csrf token), so the API call is the discriminator.
                value = (payloads["api"] if "verify_credentials" in expression
                         else payloads["cookie"])
                _send_text(self.request, json.dumps({
                    "id": message["id"],
                    "result": {"result": {"type": "string", "value": value}},
                }))

    server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, server.server_address[1]


def start_fake_http(targets, *, status=200):
    """起一个只监听 127.0.0.1 的假 /json 端点，返回 (server, port)。"""
    body = json.dumps(targets).encode("utf-8")

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - http.server 约定
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            return

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, server.server_address[1]


def free_loopback_port():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def _x_target(port, *, url="https://x.com/home", context="ctx-1", target_id="T1"):
    return {"id": target_id, "type": "page", "url": url,
            "title": "X", "webSocketDebuggerUrl":
            "ws://127.0.0.1:%d/devtools/page/%s" % (port, target_id),
            "browserContextId": context}


class _FakeWsClient:
    """注入用假 WS 客户端：按请求 id 回放固定 CDP 响应。"""

    def __init__(self, handler):
        self.handler = handler
        self.sent = []
        self.closed = False

    def send_json(self, payload):
        self.sent.append(payload)

    def recv_json(self):
        return self.handler(self.sent[-1])

    def close(self):
        self.closed = True


def _canned(values):
    """values: {1: cookie 字符串/异常, 2: api 字符串/异常}。"""

    def handler(payload):
        value = values[payload["id"]]
        if isinstance(value, Exception):
            raise value
        return {"id": payload["id"],
                "result": {"result": {"type": "string", "value": value}}}

    return _FakeWsClient(handler)


def _http_returning(targets):
    body = json.dumps(targets)

    def get(url, timeout):
        return 200, body

    return get


def _fake_identity_proof(user_id, *, channel="x", handle=None, age_seconds=0.0,
                         source="test:reader"):
    observed = time.strftime("%Y-%m-%dT%H:%M:%S",
                             time.gmtime(time.time() - age_seconds)) + "+00:00"
    return AccountIdentityProof(
        channel=channel, user_id=user_id, handle=handle or ("h-" + user_id),
        observed_at=observed, browser_context="cdp:test:ctx-1",
        source_ref=source)


# --------------------------------------------------------------------------- #
# 第 1 块：真实 CDP 身份读取器（离线）
# --------------------------------------------------------------------------- #

class CdpIdentityReaderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        bypass_x_compliance(self)
        self.dir = Path(self.tmp.name)
        account_identity.reset_reader()
        self.addCleanup(account_identity.reset_reader)

    def _env(self, **extra):
        env = {"MKT_CDP_IDENTITY": "1"}
        env.update(extra)
        return env

    # ---- 默认关闭 / 回环限定 ------------------------------------------

    def test_disabled_by_default_fails_closed_and_records_probe(self):
        proof = account_identity.read_proof_from_browser("x", data_dir=self.dir, env={})
        self.assertIsNone(proof)
        probe = account_identity.last_probe()
        self.assertEqual(cdp_identity.DISABLED, probe["reason"])
        audit = (self.dir / "identity" / "probes.jsonl")
        self.assertTrue(audit.is_file(), "探测失败必须归类留痕，不能静默吞掉")
        row = json.loads(audit.read_text(encoding="utf-8").splitlines()[-1])
        self.assertEqual("disabled", row["reason"])

    def test_non_loopback_endpoints_are_rejected(self):
        for raw in ("http://10.0.0.5:9222", "http://192.168.1.9:9222",
                    "http://localhost:9222", "https://127.0.0.1:9222",
                    "http://0.0.0.0:9222", "http://example.com:9222"):
            with self.subTest(raw=raw):
                with self.assertRaises(cdp_identity.EndpointRejected):
                    cdp_identity.parse_endpoint(raw)
        result = cdp_identity.probe(
            "x", env=self._env(MKT_CDP_IDENTITY_URL="http://10.0.0.5:9222"))
        self.assertIsNone(result.proof)
        self.assertEqual(cdp_identity.ENDPOINT_NOT_LOOPBACK, result.reason)
        # 只接受回环字面量，并允许 ::1；端口来自配置（默认 9222）。
        self.assertEqual("http://127.0.0.1:9222",
                         cdp_identity.parse_endpoint("http://127.0.0.1:9222"))
        self.assertEqual("http://[::1]:9333",
                         cdp_identity.parse_endpoint("http://[::1]:9333/"))
        self.assertEqual(9222, cdp_identity.port_of(cdp_identity.resolve_endpoint({})))

    # ---- 假服务端：真实传输层（握手/帧/超时） -------------------------

    def test_local_fake_cdp_returns_stable_numeric_id(self):
        ws_server, ws_port = start_fake_ws({
            "cookie": "ct0=abc; twid=u%3D1234567890; auth_token=secret",
            "api": json.dumps({"ok": False, "status": 401}),
        })
        self.addCleanup(ws_server.shutdown)
        self.addCleanup(ws_server.server_close)
        http_server, http_port = start_fake_http([_x_target(ws_port)])
        self.addCleanup(http_server.shutdown)
        self.addCleanup(http_server.server_close)

        result = cdp_identity.probe("x", endpoint="http://127.0.0.1:%d" % http_port,
                                    timeout=2, env=self._env())
        self.assertIsNotNone(result.proof, result)
        self.assertEqual("1234567890", result.proof.user_id)
        self.assertEqual("x", result.proof.channel)
        self.assertIn("cookie:twid", result.proof.source_ref)
        self.assertIn("127.0.0.1:%d" % http_port, result.proof.source_ref)
        self.assertIn("cdp:", result.proof.browser_context)

    def test_local_fake_cdp_uses_verified_api_identity(self):
        ws_server, ws_port = start_fake_ws({
            "cookie": "ct0=abc",
            "api": json.dumps({"ok": True, "id_str": "555", "screen_name": "alice"}),
        })
        self.addCleanup(ws_server.shutdown)
        self.addCleanup(ws_server.server_close)
        http_server, http_port = start_fake_http([_x_target(ws_port)])
        self.addCleanup(http_server.shutdown)
        self.addCleanup(http_server.server_close)

        result = cdp_identity.probe("x", endpoint="http://127.0.0.1:%d" % http_port,
                                    timeout=2, env=self._env())
        self.assertIsNotNone(result.proof, result)
        self.assertEqual("555", result.proof.user_id)
        self.assertEqual("alice", result.proof.handle)
        self.assertIn("api:verify_credentials", result.proof.source_ref)

    def test_local_fake_cdp_timeout_is_classified(self):
        ws_server, ws_port = start_fake_ws({"cookie": "", "api": ""}, mode="stall")
        self.addCleanup(ws_server.shutdown)
        self.addCleanup(ws_server.server_close)
        http_server, http_port = start_fake_http([_x_target(ws_port)])
        self.addCleanup(http_server.shutdown)
        self.addCleanup(http_server.server_close)

        started = time.monotonic()
        result = cdp_identity.probe("x", endpoint="http://127.0.0.1:%d" % http_port,
                                    timeout=0.3, env=self._env())
        elapsed = time.monotonic() - started
        self.assertIsNone(result.proof)
        self.assertEqual(cdp_identity.TIMEOUT, result.reason)
        self.assertLess(elapsed, 2.0, "超时必须真的按配置中断，不能挂死")

    def test_endpoint_500_is_classified(self):
        http_server, http_port = start_fake_http([], status=500)
        self.addCleanup(http_server.shutdown)
        self.addCleanup(http_server.server_close)
        result = cdp_identity.probe("x", endpoint="http://127.0.0.1:%d" % http_port,
                                    timeout=2, env=self._env())
        self.assertIsNone(result.proof)
        self.assertEqual(cdp_identity.ENDPOINT_HTTP_ERROR, result.reason)
        self.assertIn("500", result.detail)

    def test_ws_unreachable_is_classified(self):
        port = free_loopback_port()
        http_server, http_port = start_fake_http([_x_target(port)])
        self.addCleanup(http_server.shutdown)
        self.addCleanup(http_server.server_close)
        result = cdp_identity.probe("x", endpoint="http://127.0.0.1:%d" % http_port,
                                    timeout=1, env=self._env())
        self.assertIsNone(result.proof)
        self.assertEqual(cdp_identity.WS_UNAVAILABLE, result.reason)

    # ---- 注入客户端：语义边界 ---------------------------------------

    def _probe_with(self, cookie, api, *, targets=None, timeout=1):
        targets = targets if targets is not None else [
            _x_target(9222)]
        return cdp_identity.probe(
            "x", timeout=timeout, env=self._env(),
            http_get=_http_returning(targets),
            ws_factory=lambda url, ttl: _canned({1: cookie, 2: api}))

    def test_handle_only_is_never_promoted_to_identity(self):
        result = self._probe_with("", json.dumps(
            {"ok": True, "id_str": "", "screen_name": "alice"}))
        self.assertIsNone(result.proof)
        self.assertEqual(cdp_identity.IDENTITY_MISSING, result.reason)
        self.assertIn("handle_only", result.detail)

    def test_target_missing_is_classified(self):
        result = self._probe_with("", "", targets=[
            {"id": "p1", "type": "page", "url": "https://example.com/home",
             "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/page/p1"}])
        self.assertIsNone(result.proof)
        self.assertEqual(cdp_identity.TARGET_MISSING, result.reason)

    def test_multiple_browser_contexts_do_not_guess(self):
        result = self._probe_with("", "", targets=[
            _x_target(9222, context="ctx-a", target_id="A", url="https://x.com/home"),
            _x_target(9222, context="ctx-b", target_id="B", url="https://x.com/home")])
        self.assertIsNone(result.proof)
        self.assertEqual(cdp_identity.TARGET_AMBIGUOUS, result.reason)

    def test_conflicting_cookie_and_api_identity_fails_closed(self):
        result = self._probe_with(
            "twid=u%3D111",
            json.dumps({"ok": True, "id_str": "222", "screen_name": "alice"}))
        self.assertIsNone(result.proof)
        self.assertEqual(cdp_identity.IDENTITY_CONFLICT, result.reason)

    def test_endpoint_unreachable_is_classified(self):
        port = free_loopback_port()
        result = cdp_identity.probe(
            "x", endpoint="http://127.0.0.1:%d" % port, timeout=0.5, env=self._env())
        self.assertIsNone(result.proof)
        self.assertEqual(cdp_identity.ENDPOINT_UNREACHABLE, result.reason)

    def test_probe_is_off_unless_enabled(self):
        self.assertFalse(cdp_identity.enabled({}))
        self.assertTrue(cdp_identity.enabled({"MKT_CDP_IDENTITY": "1"}))


# --------------------------------------------------------------------------- #
# 假 vendor（真实 subprocess，不 mock 发送路径）
# --------------------------------------------------------------------------- #

_VENDOR_BODY = r'''
import json, pathlib, sys, time
base = pathlib.Path(__file__).resolve().parent
with (base / "calls.jsonl").open("a", encoding="utf-8") as fh:
    fh.write(json.dumps(sys.argv[1:]) + "\n")
if "--dry-run" in sys.argv[1:]:
    sys.exit(0)
logs = base / "logs"
logs.mkdir(exist_ok=True)
args = sys.argv[1:]
target = args[args.index("--tweet-url") + 1] if "--tweet-url" in args else ""
(logs / ("reply-%d.json" % int(time.time() * 1000000))).write_text(
    json.dumps({"record": {"posted": True, "url": target}}), encoding="utf-8")
sys.exit(0)
'''


def install_fake_vendor(root):
    vendor = Path(root) / "vendor"
    vendor.mkdir(parents=True, exist_ok=True)
    for script in ("reply_x.py", "publish_x.py", "follow_x.py"):
        (vendor / script).write_text(_VENDOR_BODY, encoding="utf-8")
    return vendor


def vendor_calls(vendor):
    path = Path(vendor) / "calls.jsonl"
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def real_calls(vendor):
    """真正发送（非 --dry-run）的次数。"""
    return [call for call in vendor_calls(vendor) if "--dry-run" not in call]


def _queue_file(tmp, *, account="", target=X_REPLY_TARGET):
    items = [{"id": "q1-0", "target": target,
              "body": "针对该楼的具体回复，没有链接。",
              "scheduled_for": "2026-11-15"}]
    path = Path(tmp) / "queues" / "q1.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(
        {"campaign": "q1", "channel": "x", "action": "reply",
         "account": account, "items": items}, ensure_ascii=False),
        encoding="utf-8")
    return path


def _x_args(*, go=True, account="acct-a", campaign="c1",
            target=X_REPLY_TARGET, extra=None):
    return SimpleNamespace(
        action="reply",
        extra=list(extra if extra is not None else
                   ["--tweet-url", target, "--text", "针对该楼的具体回复，没有链接。"]),
        file=None, thread=False, go=go, adhoc=True, campaign=campaign,
        account=account, handle="", record=False)


class _SendCase(unittest.TestCase):
    """共用：临时目录 + 假 vendor + 登录探测离线桩。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name) / "data"
        self.dir.mkdir()
        self.vendor = install_fake_vendor(Path(self.tmp.name))
        bypass_x_compliance(self)
        account_identity.reset_reader()
        self.addCleanup(account_identity.reset_reader)
        login = mock.patch("core.session_gate.check_login",
                           return_value={"status": "logged_in"})
        login.start()
        self.addCleanup(login.stop)

    def _vendor_patch(self):
        patcher = mock.patch.object(adapter, "VENDOR", self.vendor)
        patcher.start()
        self.addCleanup(patcher.stop)


# --------------------------------------------------------------------------- #
# 第 2 块：旧授权迁移（安全核心）
# --------------------------------------------------------------------------- #

class LegacyAuthorizationMigrationTests(_SendCase):
    def _legacy(self):
        authorize(self.dir, by="owner", actions=["x:reply"], days=30)

    def _bound(self):
        authorize(self.dir, by="owner", actions=["x:reply"], days=30,
                  accounts=["acct-a"], identities=["acct-a=x:uid-a"],
                  require_identity=True)

    def test_legacy_policy_queue_real_send_denied_with_zero_vendor_calls(self):
        self._legacy()
        self._vendor_patch()
        with self.assertRaises(QueueRunError) as ctx:
            run_queue(_queue_file(self.dir), data_dir=self.dir, go=True,
                      routine=True, log=lambda *a, **k: None)
        message = str(ctx.exception)
        self.assertIn("AUTH_REQUIRED", message)
        self.assertIn("mkt policy migrate-identity", message)
        self.assertEqual([], real_calls(self.vendor))
        self.assertEqual([], vendor_calls(self.vendor))
        self.assertFalse((self.dir / "ledger.jsonl").exists())

    def test_legacy_policy_cli_send_denied_with_zero_vendor_calls(self):
        self._legacy()
        self._vendor_patch()
        out = io.StringIO()
        with mock.patch.object(mkt, "DATA", self.dir), contextlib.redirect_stdout(out):
            rc = mkt.cmd_x(_x_args(account=""))
        self.assertEqual(1, rc)
        self.assertIn("AUTH_REQUIRED", out.getvalue())
        self.assertIn("migrate-identity", out.getvalue())
        self.assertEqual([], vendor_calls(self.vendor))

    def test_legacy_policy_mcp_send_denied_with_zero_vendor_calls(self):
        self._legacy()
        self._vendor_patch()
        with mock.patch.object(mcp_server, "DATA", self.dir):
            response = mcp_server.call_tool("mkt_action", {
                "channel": "x", "action": "reply", "target": X_REPLY_TARGET,
                "text": "针对该楼的具体回复，没有链接。", "go": True,
                "routine": True, "account": ""})
        self.assertTrue(response.get("isError"))
        text = response["content"][0]["text"]
        self.assertIn("AUTH_REQUIRED", text)
        self.assertIn("migrate-identity", text)
        self.assertEqual([], vendor_calls(self.vendor))

    def test_legacy_dry_run_only_reports_blocker(self):
        self._legacy()
        self._vendor_patch()
        logs = []
        rows = run_queue(_queue_file(self.dir), data_dir=self.dir, go=False,
                         routine=True, log=logs.append)
        self.assertEqual(["dry-run"], [row["result"] for row in rows])
        joined = "\n".join(str(line) for line in logs)
        self.assertIn("阻塞项", joined)
        self.assertIn("AUTH_REQUIRED", joined)
        self.assertIn("mkt policy migrate-identity", joined)
        self.assertEqual([], real_calls(self.vendor))
        dry = vendor_calls(self.vendor)
        self.assertEqual(1, len(dry), "dry-run 可以走 vendor 的 --dry-run，但绝不真发")
        self.assertIn("--dry-run", dry[0])
        self.assertFalse((self.dir / "ledger.jsonl").exists())

    def test_identity_blockers_are_machine_readable(self):
        self._legacy()
        blockers = account_identity.identity_blockers(self.dir)
        self.assertEqual(1, len(blockers))
        self.assertEqual("AUTH_REQUIRED", blockers[0]["code"])
        self.assertEqual("authorization_not_identity_bound", blockers[0]["reason"])
        self.assertIn("migrate-identity", blockers[0]["next"])

    def test_migrate_identity_dry_run_lists_missing_bindings(self):
        authorize(self.dir, by="owner", actions=["x:reply", "x:publish"], days=30,
                  accounts=["acct-a"])
        _queue_file(self.dir, account="acct-a")
        report = self._cli("policy", "migrate-identity")
        self.assertTrue(report["dry_run"])
        self.assertFalse(report["identity_bound"])
        self.assertEqual(["x:publish", "x:reply"], report["allow_actions"])
        self.assertEqual(1, len(report["accounts"]))
        account = report["accounts"][0]
        self.assertEqual("acct-a", account["account"])
        self.assertTrue(account["blocked"])
        self.assertEqual("name_only", account["binding"])
        self.assertIn("q1", json.dumps(account["queues"], ensure_ascii=False))
        self.assertIn("--identity acct-a=x:", account["next"])
        # dry-run 不写 policy
        self.assertFalse(get_policy(self.dir).get("require_identity"))
        self.assertFalse(get_policy(self.dir).get("account_identities"))

    def test_migrate_identity_apply_requires_signature_and_identity(self):
        self._legacy()
        result = self._cli("policy", "migrate-identity", "--apply", expected=2)
        self.assertIn("--by", result["error"])
        result = self._cli("policy", "migrate-identity", "--apply", "--by", "owner",
                           expected=2)
        self.assertIn("缺身份绑定", result["error"])

    def test_migrate_identity_apply_binds_and_unblocks_real_send(self):
        self._legacy()
        result = self._cli("policy", "migrate-identity", "--apply", "--by", "owner",
                           "--identity", "x:uid-a")
        self.assertTrue(result["applied"])
        self.assertTrue(result["policy"]["require_identity"])
        self.assertTrue(result["policy"]["account_identities"][""])
        self.assertTrue((self.dir / "identity" / "migrations.jsonl").is_file())

        self._vendor_patch()
        account_identity.set_reader(lambda channel: _fake_identity_proof("uid-a"))
        rows = run_queue(_queue_file(self.dir), data_dir=self.dir, go=True,
                         routine=True, log=lambda *a, **k: None)
        self.assertEqual("committed", rows[0]["result"])
        self.assertEqual("uid-a", rows[0]["identity"]["observed_platform_identity"])
        self.assertEqual(1, len(real_calls(self.vendor)))

    def test_probe_command_reports_closed_reader(self):
        env = {key: value for key, value in os.environ.items()
               if not key.startswith("MKT_")}
        result = subprocess.run(
            [sys.executable, str(REPO / "mkt.py"), "--workspace", str(self.dir),
             "policy", "identity"],
            cwd=str(REPO), text=True, capture_output=True, env=env)
        self.assertEqual(0, result.returncode, result.stderr)
        payload = json.loads(result.stdout)
        self.assertFalse(payload["enabled"])
        self.assertIsNone(payload["observed"])
        self.assertEqual("disabled", payload["probe"]["reason"])
        self.assertIn("MKT_CDP_IDENTITY", payload["hint"])

    def test_doctor_reports_legacy_policy_blocker(self):
        self._legacy()
        env = {key: value for key, value in os.environ.items()
               if not key.startswith("MKT_")}
        out = io.StringIO()
        with mock.patch.dict(os.environ, env, clear=True), \
                contextlib.redirect_stdout(out):
            from core import doctor
            rc = doctor.run_doctor(REPO, self.dir)
        self.assertEqual(0, rc)
        text = out.getvalue()
        self.assertIn("账号身份门禁", text)
        self.assertIn("mkt policy migrate-identity", text)

    def _cli(self, *args, expected=0):
        env = {key: value for key, value in os.environ.items()
               if not key.startswith("MKT_")}
        result = subprocess.run(
            [sys.executable, str(REPO / "mkt.py"), "--workspace", str(self.dir),
             *args], cwd=str(REPO), text=True, capture_output=True, env=env)
        self.assertEqual(expected, result.returncode, result.stderr)
        if expected != 0:
            return {"error": result.stderr, "stdout": result.stdout}
        return json.loads(result.stdout)


# --------------------------------------------------------------------------- #
# 第 3 块：三条真实写入口一致
# --------------------------------------------------------------------------- #

class ThreeEntryChainTests(_SendCase):
    def _bound(self):
        authorize(self.dir, by="owner", actions=["x:reply"], days=30,
                  accounts=["acct-a"], identities=["acct-a=x:uid-a"],
                  require_identity=True)

    def _queue(self, target=X_REPLY_TARGET):
        return _queue_file(self.dir, account="acct-a", target=target)

    def _run_queue(self, target=X_REPLY_TARGET):
        return run_queue(self._queue(target), data_dir=self.dir, go=True,
                         routine=True, log=lambda *a, **k: None)

    def _run_cli(self, target, *, campaign="cli-1"):
        out = io.StringIO()
        with mock.patch.object(mkt, "DATA", self.dir), \
                contextlib.redirect_stdout(out):
            rc = mkt.cmd_x(_x_args(target=target, campaign=campaign))
        return rc, out.getvalue()

    def _run_mcp(self, target, *, campaign="mcp-1"):
        with mock.patch.object(mcp_server, "DATA", self.dir):
            return mcp_server.call_tool("mkt_action", {
                "channel": "x", "action": "reply", "target": target,
                "text": "针对该楼的具体回复，没有链接。", "go": True,
                "routine": True, "account": "acct-a", "campaign": campaign})

    def test_three_entries_match_identity_and_receipts_carry_proof(self):
        self._bound()
        self._vendor_patch()
        account_identity.set_reader(lambda channel: _fake_identity_proof("uid-a"))

        queue_rows = self._run_queue("https://x.com/example/status/101")
        self.assertEqual("committed", queue_rows[0]["result"])
        self.assertEqual("acct-a", queue_rows[0]["identity"]["authorized_account"])

        rc, out = self._run_cli("https://x.com/example/status/102")
        self.assertEqual(0, rc, out)

        response = self._run_mcp("https://x.com/example/status/103")
        self.assertFalse(response.get("isError"), response)
        action = json.loads(response["content"][0]["text"])
        self.assertEqual("committed", action["result"])

        self.assertEqual(3, len(real_calls(self.vendor)))
        from core.ledger import Ledger
        rows = Ledger(self.dir / "ledger.jsonl").results(channel="x", action="reply")
        self.assertEqual(3, len(rows))
        for row in rows:
            self.assertEqual("acct-a", row.get("authorized_account"), row)
            self.assertEqual("uid-a", row.get("observed_platform_identity"), row)
            self.assertEqual("test:reader", row.get("identity_proof_ref"), row)
            self.assertEqual("uid-a", row["identity"]["observed_platform_identity"])

    def test_three_entries_deny_when_probe_returns_none(self):
        self._bound()
        self._vendor_patch()
        account_identity.set_reader(lambda channel: None)

        with self.assertRaises(QueueRunError) as ctx:
            self._run_queue()
        self.assertIn("identity_unknown", str(ctx.exception))

        rc, out = self._run_cli("https://x.com/example/status/102")
        self.assertEqual(1, rc)
        self.assertIn("identity_unknown", out)

        response = self._run_mcp("https://x.com/example/status/103")
        self.assertTrue(response.get("isError"))
        self.assertIn("identity_unknown", response["content"][0]["text"])
        self.assertEqual([], vendor_calls(self.vendor))

    def test_three_entries_deny_identity_mismatch(self):
        self._bound()
        self._vendor_patch()
        account_identity.set_reader(lambda channel: _fake_identity_proof("uid-b"))

        with self.assertRaises(QueueRunError) as ctx:
            self._run_queue()
        self.assertIn("identity_mismatch", str(ctx.exception))

        rc, out = self._run_cli("https://x.com/example/status/102")
        self.assertEqual(1, rc)
        self.assertIn("identity_mismatch", out)

        response = self._run_mcp("https://x.com/example/status/103")
        self.assertTrue(response.get("isError"))
        self.assertIn("identity_mismatch", response["content"][0]["text"])
        self.assertEqual([], vendor_calls(self.vendor))

    def test_stale_proof_is_rejected_on_all_three_entries(self):
        self._bound()
        self._vendor_patch()
        account_identity.set_reader(
            lambda channel: _fake_identity_proof("uid-a", age_seconds=3600))
        with self.assertRaises(QueueRunError) as ctx:
            self._run_queue()
        self.assertIn("identity_stale", str(ctx.exception))
        rc, out = self._run_cli("https://x.com/example/status/102")
        self.assertEqual(1, rc)
        self.assertIn("identity_stale", out)
        self.assertEqual([], vendor_calls(self.vendor))

    def test_require_identity_auto_path_actually_commits(self):
        """require_identity 授权下真实发送必须跑通（含适配器嵌套审批复核）。"""
        self._bound()
        self._vendor_patch()
        account_identity.set_reader(lambda channel: _fake_identity_proof("uid-a"))
        rows = self._run_queue("https://x.com/example/status/201")
        self.assertEqual("committed", rows[0]["result"])
        self.assertEqual("routine-policy", rows[0].get("approval"))
        self.assertEqual(1, len(real_calls(self.vendor)))

    def test_unbound_channel_is_still_governed_by_approval(self):
        """没有 policy 的 adhoc CLI 不因本门禁改变（明确的人工通道）。"""
        self._vendor_patch()
        probed = []

        def must_not_probe(channel):
            probed.append(channel)
            return _fake_identity_proof("uid-a")

        account_identity.set_reader(must_not_probe)
        rc, out = self._run_cli("https://x.com/example/status/301")
        self.assertEqual(0, rc, out)
        self.assertEqual([], probed, "无 policy 时不应探测身份")
        self.assertEqual(1, len(real_calls(self.vendor)))


if __name__ == "__main__":
    unittest.main()
