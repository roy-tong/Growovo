"""G-09 验收冻结：DNS 校验 + 连接固定（先于实现编写）。

G-06 的 ``core/watch_safety.py`` 先判定地址、再由传输层按 **URL** 抓取，两次
解析各自独立，存在 DNS rebinding 的 TOCTOU。本文件冻结 G-09 的新契约：解析
一次 → 每个地址都判定 → 选一个已通过判定的地址 → **连接该 IP** → Host 头 /
SNI 用原域名 → 再校验响应。

冻结的六组语义：

1. 固定连接：连接目标是已校验 IP（``target["ip"]``），解析只发生一次；HTTP 的
   ``Host`` 头保留原域名（含非默认端口、IPv6 方括号）。
2. 全部公网才放行：公网 + 私网混合解析 → 拒绝且连接数为 0；字面私网地址 →
   拒绝且不连接。
3. rebinding：校验后若「再解析」被改成私网，仍然直连已校验 IP（不重解析）；
   连接层报告的落地地址与已校验 IP 不符时拒绝读取响应。
4. 重定向：每一跳重新解析 → 判定 → 固定连接；跳到 127.0.0.1 一律拒绝，私网
   地址从未被连接；跳数上限、成环、https→http 降级都明确失败。
5. HTTPS 证书校验仍针对**原域名**：自签证书握手失败（而不是放过）；同一信任
   锚下，域名 SAN 证书通过、IP SAN 证书失败（说明校验的是域名不是 IP）；SNI
   是原域名；禁止使用关闭了证书校验的 ``SSLContext``。
6. 响应边界：超字节上限、超总时长预算 → 明确失败，返回码带 C-01 家族。

全部离线：注入 fake resolver / fake connector；需要真实 socket 的用例只连
127.0.0.1 上的临时端口（HTTP 假服务器 / TLS 假服务器 / 静默服务器）。
"""
import socket
import ssl
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from core import watch_safety as safety

try:  # 红阶段：core/safe_fetch.py 还不存在，每条用例各自变红
    from core import safe_fetch as sf
except Exception as exc:  # noqa: BLE001
    sf = None
    _SF_IMPORT_ERROR = exc
else:
    _SF_IMPORT_ERROR = None

PUBLIC = "93.184.216.34"
PUBLIC_ALT = "20.205.243.166"
PUBLIC_V6 = "2606:4700:4700::1111"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "tls"
HTML_BODY = ("<html><body><p>Pro plan costs 49 dollars per month</p></body></html>")
URL_HTTPS = "https://rival.example/pricing"
URL_HTTP = "http://rival.example/pricing"


# ---------------------------------------------------------------------------
# 注入工具（零网络）：解析器、连接层、响应
# ---------------------------------------------------------------------------

def html(text):
    return f"<html><body><p>{text}</p></body></html>"


def page(body, *, status=200, ctype="text/html; charset=utf-8", headers=None):
    merged = {"Content-Type": ctype} if ctype else {}
    merged.update(headers or {})
    return {"status": status, "headers": merged, "body": body}


def redirect(location, *, status=302):
    return {"status": status, "headers": {"Location": location}, "body": ""}


def resolver(addresses):
    """每次解析都返回同一组地址，并记录调用（用来断言「只解析一次」）。"""
    calls = []

    def fn(host, port):
        calls.append((host, int(port)))
        return list(addresses)

    fn.calls = calls
    return fn


def resolver_map(mapping):
    calls = []

    def fn(host, port):
        calls.append((host, int(port)))
        return list(mapping[host])

    fn.calls = calls
    return fn


def rebound_resolver(first, later):
    """第一次返回已校验地址，之后返回私网：模拟「校验后、连接前」被改写。"""
    calls = []

    def fn(host, port):
        calls.append((host, int(port)))
        return list(first if len(calls) == 1 else later)

    fn.calls = calls
    return fn


def fake_connector(pages, *, peer_ip=None):
    """模拟连接层：记录收到的 target，返回预置响应。

    默认把 ``peer_ip`` 报成 ``target["ip"]``（即「连到了被要求连的地址」）；
    传 ``peer_ip=`` 可以伪造一次「落地地址与已校验 IP 不符」。
    """
    calls = []

    def fn(target):
        calls.append(dict(target))
        hit = pages.get(target["url"])
        if hit is None:
            raise AssertionError(f"connection not expected: {target['url']}")
        response = dict(hit() if callable(hit) else hit)
        if peer_ip is not None:
            response["peer_ip"] = peer_ip
        else:
            response.setdefault("peer_ip", target["ip"])
        return response

    fn.calls = calls
    return fn


def loopback_connector(port):
    """把一个已固定到公网 IP 的 target 改写到本机假服务器。

    假服务器在 127.0.0.1，所以丢掉 ``peer_ip``（否则会与已校验公网 IP 不符）；
    连接固定本身由 ``target["ip"]`` 断言，真实 peer 校验由 peer 用例覆盖。
    """
    calls = []

    def fn(target):
        calls.append(dict(target))
        local = dict(target, ip="127.0.0.1", port=int(port))
        response = dict(sf.default_connector(local))
        response.pop("peer_ip", None)
        return response

    fn.calls = calls
    return fn


def _parse_request(raw):
    # 假服务器把手写请求行和 headers 拼成 str，TLS 假服务器拿到 raw bytes；
    # 两种输入都接受（测试脚手架，不涉及被冻结的断言）。
    if isinstance(raw, (bytes, bytearray)):
        text = raw.decode("iso-8859-1", errors="replace")
    else:
        text = str(raw)
    lines = text.split("\r\n")
    first = lines[0].split(" ") if lines else [""]
    headers = {}
    for line in lines[1:]:
        if ":" in line:
            key, value = line.split(":", 1)
            headers[key.strip().lower()] = value.strip()
    return {"line": lines[0], "path": first[1] if len(first) > 1 else "",
            "headers": headers, "raw": text}


class HttpServer:
    """只监听 127.0.0.1 临时端口的 HTTP 假服务器（离线）。"""

    def __init__(self, routes):
        self.requests = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_GET(self):  # noqa: N802 标准库接口名
                outer.requests.append(_parse_request(
                    f"GET {self.path} HTTP/1.1\r\n"
                    + "".join(f"{k}: {v}\r\n" for k, v in self.headers.items())
                    + "\r\n"))
                hit = routes.get(self.path)
                if hit is None:
                    payload = b"not found"
                    self.send_response(404)
                    self.send_header("Content-Type", "text/plain")
                    self.send_header("Content-Length", str(len(payload)))
                    self.send_header("Connection", "close")
                    self.end_headers()
                    self.wfile.write(payload)
                    return
                status, headers, body = hit
                payload = body.encode("utf-8") if isinstance(body, str) else body
                self.send_response(status)
                for key, value in headers.items():
                    self.send_header(key, value)
                self.send_header("Content-Length", str(len(payload)))
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *args):  # 测试不打印访问日志
                pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(5)


class SilentServer:
    """接受 TCP 连接但永不响应：用来触发读超时（离线）。"""

    def __init__(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(1)
        self.sock.settimeout(5)
        self.port = self.sock.getsockname()[1]
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self):
        try:
            raw, _ = self.sock.accept()
        except OSError:
            return
        try:
            self.stop.wait(2)
        finally:
            raw.close()

    def close(self):
        self.stop.set()
        try:
            self.sock.close()
        except OSError:
            pass
        self.thread.join(5)


class TlsServer:
    """只监听 127.0.0.1 临时端口的 TLS 假服务器（离线），可记录 SNI。"""

    def __init__(self, certfile, keyfile):
        self.sni = []
        self.requests = []
        self.error = None
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(1)
        self.sock.settimeout(5)
        self.port = self.sock.getsockname()[1]
        self.context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self.context.load_cert_chain(str(FIXTURES / certfile),
                                     str(FIXTURES / keyfile))
        self.context.sni_callback = self._remember_sni
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _remember_sni(self, sslobj, server_name, context):
        self.sni.append(server_name)

    def _run(self):
        raw = None
        try:
            try:
                raw, _ = self.sock.accept()
            except OSError:
                return
            raw.settimeout(5)
            try:
                tls = self.context.wrap_socket(raw, server_side=True)
            except (ssl.SSLError, OSError) as exc:
                self.error = f"{type(exc).__name__}: {exc}"
                return
            try:
                data = tls.recv(65536)
                self.requests.append(_parse_request(data))
                payload = HTML_BODY.encode("utf-8")
                tls.sendall(
                    b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\n"
                    b"Content-Length: " + str(len(payload)).encode() + b"\r\n"
                    b"Connection: close\r\n\r\n" + payload)
            except (ssl.SSLError, OSError):
                self.error = "connection dropped before the request completed"
            finally:
                try:
                    tls.close()
                except OSError:
                    pass
        finally:
            if raw is not None:
                try:
                    raw.close()
                except OSError:
                    pass
            self.sock.close()

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass
        self.thread.join(5)


class SafeFetchCase(unittest.TestCase):
    def setUp(self):
        if sf is None:
            self.assertIsNotNone(
                sf, f"G-09 未实现：core/safe_fetch.py 不存在（{_SF_IMPORT_ERROR}）")


# ---------------------------------------------------------------------------
# 1. 固定连接：连的是已校验 IP，Host 头用原域名
# ---------------------------------------------------------------------------

class PinnedConnectionTests(SafeFetchCase):
    def test_connects_to_the_validated_ip_and_resolves_only_once(self):
        r = resolver([PUBLIC])
        fake = fake_connector({URL_HTTPS: page(HTML_BODY)})
        result = sf.fetch(URL_HTTPS, resolver=r, connector=fake)
        self.assertEqual(1, len(fake.calls), "每次抓取应当只建立一次连接")
        target = fake.calls[0]
        self.assertEqual(PUBLIC, target["ip"], "必须连接已校验的公网 IP")
        self.assertEqual("rival.example", target["host"])
        self.assertEqual(443, target["port"])
        self.assertEqual("https", target["scheme"])
        self.assertEqual("/pricing", target["path"])
        self.assertEqual([("rival.example", 443)], r.calls,
                         "只解析一次；连接不能靠第二次解析")
        self.assertEqual([PUBLIC], result["resolved_ips"])
        self.assertEqual(PUBLIC, result["pinned_ip"])
        self.assertEqual("pinned", result["mode"])
        self.assertTrue(result["allowed"])
        self.assertEqual("", result["reason"])
        self.assertIn("Pro plan", result["text"])
        self.assertEqual(200, result["status"])
        self.assertGreater(result["bytes"], 0)

    def test_http_keeps_the_original_domain_in_the_host_header(self):
        fake = fake_connector({URL_HTTP: page(HTML_BODY)})
        sf.fetch(URL_HTTP, resolver=resolver([PUBLIC]), connector=fake)
        target = fake.calls[0]
        self.assertEqual(80, target["port"])
        self.assertEqual("rival.example", target["headers"]["Host"])

    def test_non_default_port_stays_in_the_host_header(self):
        url = "http://rival.example:8080/pricing?a=1"
        fake = fake_connector({url: page(HTML_BODY)})
        result = sf.fetch(url, resolver=resolver([PUBLIC]), connector=fake)
        target = fake.calls[0]
        self.assertEqual(8080, target["port"])
        self.assertEqual("rival.example:8080", target["headers"]["Host"])
        self.assertEqual("/pricing?a=1", target["path"])
        self.assertEqual(url, result["final_url"])

    def test_ipv6_literal_is_bracketed_in_the_host_header(self):
        url = f"http://[{PUBLIC_V6}]/pricing"
        r = resolver([])
        fake = fake_connector({url: page(HTML_BODY)})
        sf.fetch(url, resolver=r, connector=fake)
        self.assertEqual([], r.calls, "字面 IP 不需要 DNS")
        self.assertEqual(PUBLIC_V6, fake.calls[0]["ip"])
        self.assertEqual(f"[{PUBLIC_V6}]", fake.calls[0]["headers"]["Host"])

    def test_literal_private_ip_is_refused_without_connecting(self):
        for url in ("http://127.0.0.1/price", "http://10.0.0.5/price",
                    "http://[::1]/price", "http://2130706433/price"):
            with self.subTest(url=url):
                fake = fake_connector({})
                with self.assertRaises(sf.SafeFetchError) as ctx:
                    sf.fetch(url, resolver=resolver([PUBLIC]), connector=fake)
                self.assertIn(ctx.exception.code,
                              ("BLOCKED_HOST", "BLOCKED_ADDRESS"))
                self.assertEqual([], fake.calls)


# ---------------------------------------------------------------------------
# 2. 全部公网才放行；3. rebinding 不能把连接改到私网
# ---------------------------------------------------------------------------

class ResolutionPinTests(SafeFetchCase):
    def test_mixed_public_and_private_resolution_is_refused(self):
        for mixed in ([PUBLIC, "127.0.0.1"], [PUBLIC, "10.0.0.5"],
                      ["169.254.169.254", PUBLIC], [PUBLIC, "::1"],
                      [PUBLIC, "::ffff:127.0.0.1"]):
            with self.subTest(addresses=mixed):
                fake = fake_connector({URL_HTTPS: page(HTML_BODY)})
                with self.assertRaises(sf.SafeFetchError) as ctx:
                    sf.fetch(URL_HTTPS, resolver=resolver(mixed), connector=fake)
                self.assertEqual("BLOCKED_ADDRESS", ctx.exception.code)
                self.assertEqual("POLICY_DENIED", ctx.exception.c01)
                self.assertEqual([], fake.calls, "判定没过就不能建连接")

    def test_resolution_failure_or_empty_result_is_refused(self):
        def broken(host, port):
            raise OSError("dns down")

        for fn in (broken, resolver([])):
            with self.subTest(fn=fn):
                fake = fake_connector({})
                with self.assertRaises(sf.SafeFetchError) as ctx:
                    sf.fetch(URL_HTTPS, resolver=fn, connector=fake)
                self.assertEqual("RESOLUTION_FAILED", ctx.exception.code)
                self.assertEqual([], fake.calls)

    def test_rebinding_after_validation_still_connects_the_validated_ip(self):
        r = rebound_resolver([PUBLIC], ["10.0.0.5"])
        fake = fake_connector({URL_HTTPS: page(HTML_BODY)})
        result = sf.fetch(URL_HTTPS, resolver=r, connector=fake)
        self.assertEqual(1, len(r.calls), "连接阶段不得二次解析")
        self.assertEqual(PUBLIC, fake.calls[0]["ip"], "必须直连已校验的公网 IP")
        self.assertEqual(PUBLIC, result["pinned_ip"])
        self.assertNotIn("10.0.0.5", [t["ip"] for t in fake.calls])

    def test_connection_landing_on_another_address_is_refused(self):
        for peer in ("10.0.0.5", "127.0.0.1", "169.254.169.254", "::1"):
            with self.subTest(peer=peer):
                fake = fake_connector({URL_HTTPS: page(HTML_BODY)}, peer_ip=peer)
                with self.assertRaises(sf.SafeFetchError) as ctx:
                    sf.fetch(URL_HTTPS, resolver=resolver([PUBLIC]), connector=fake)
                self.assertEqual("BLOCKED_PINNED_ADDRESS", ctx.exception.code)
                self.assertEqual("POLICY_DENIED", ctx.exception.c01)

    def test_ipv4_mapped_peer_of_the_same_address_is_accepted(self):
        fake = fake_connector({URL_HTTPS: page(HTML_BODY)},
                              peer_ip=f"::ffff:{PUBLIC}")
        result = sf.fetch(URL_HTTPS, resolver=resolver([PUBLIC]), connector=fake)
        self.assertIn("Pro plan", result["text"])
        self.assertEqual(PUBLIC, result["pinned_ip"])


# ---------------------------------------------------------------------------
# 4. 重定向：逐跳重新解析 → 判定 → 固定连接
# ---------------------------------------------------------------------------

class RedirectPinTests(SafeFetchCase):
    def test_redirect_to_loopback_is_refused_and_never_connected(self):
        for target in ("http://127.0.0.1/admin", "https://127.0.0.1/admin",
                       "http://[::1]/admin", "http://169.254.169.254/latest"):
            with self.subTest(target=target):
                fake = fake_connector({URL_HTTPS: redirect(target)})
                with self.assertRaises(sf.SafeFetchError) as ctx:
                    sf.fetch(URL_HTTPS, resolver=resolver([PUBLIC]), connector=fake)
                self.assertEqual("REDIRECT_BLOCKED", ctx.exception.code)
                self.assertEqual([URL_HTTPS], [t["url"] for t in fake.calls],
                                 "私网那一跳不能被连接")
                self.assertNotIn("127.0.0.1", [t["ip"] for t in fake.calls])
                self.assertNotIn("169.254.169.254",
                                 [t["ip"] for t in fake.calls])

    def test_each_hop_resolves_and_connects_its_own_validated_ip(self):
        second = "https://cdn.rival.example/pricing"
        third = "https://rival.example/final"
        r = resolver_map({"rival.example": [PUBLIC],
                          "cdn.rival.example": [PUBLIC_ALT]})
        fake = fake_connector({URL_HTTPS: redirect(second, status=301),
                               second: redirect(third, status=307),
                               third: page(HTML_BODY)})
        result = sf.fetch(URL_HTTPS, resolver=r, connector=fake)
        self.assertEqual([PUBLIC, PUBLIC_ALT, PUBLIC],
                         [t["ip"] for t in fake.calls])
        self.assertEqual(["rival.example", "cdn.rival.example", "rival.example"],
                         [host for host, _port in r.calls])
        self.assertEqual(third, result["final_url"])
        self.assertEqual([second, third], result["redirects"])
        self.assertEqual(3, len(result["hops"]))
        self.assertEqual(2, len(result["redirect_chain"]))
        self.assertEqual(second, result["redirect_chain"][0]["location"])
        self.assertEqual(PUBLIC_ALT, result["redirect_chain"][0]["pinned_ip"])
        self.assertEqual([PUBLIC, PUBLIC_ALT], result["resolved_ips"])
        self.assertEqual(PUBLIC, result["pinned_ip"])

    def test_redirect_hop_with_mixed_resolution_is_refused(self):
        second = "https://cdn.rival.example/pricing"
        r = resolver_map({"rival.example": [PUBLIC],
                          "cdn.rival.example": [PUBLIC_ALT, "10.0.0.5"]})
        fake = fake_connector({URL_HTTPS: redirect(second)})
        with self.assertRaises(sf.SafeFetchError) as ctx:
            sf.fetch(URL_HTTPS, resolver=r, connector=fake)
        self.assertEqual("REDIRECT_BLOCKED", ctx.exception.code)
        self.assertEqual([URL_HTTPS], [t["url"] for t in fake.calls])

    def test_redirect_limit_loop_and_bad_targets(self):
        chain = {}
        for index in range(1, sf.MAX_REDIRECTS + 2):
            chain[f"https://rival.example/{index}"] = redirect(
                f"https://rival.example/{index + 1}")
        fake = fake_connector(chain)
        with self.assertRaises(sf.SafeFetchError) as ctx:
            sf.fetch("https://rival.example/1", resolver=resolver([PUBLIC]),
                     connector=fake)
        self.assertEqual("TOO_MANY_REDIRECTS", ctx.exception.code)
        self.assertEqual("RATE_LIMITED", ctx.exception.c01)
        self.assertEqual(sf.MAX_REDIRECTS + 1, len(fake.calls))

        loop = fake_connector({URL_HTTPS: redirect(URL_HTTPS)})
        with self.assertRaises(sf.SafeFetchError) as ctx:
            sf.fetch(URL_HTTPS, resolver=resolver([PUBLIC]), connector=loop)
        self.assertEqual("REDIRECT_LOOP", ctx.exception.code)

        for location in ("", "file:///etc/passwd", "javascript:alert(1)"):
            with self.subTest(location=location):
                bad = fake_connector({URL_HTTPS: redirect(location)})
                with self.assertRaises(sf.SafeFetchError) as ctx:
                    sf.fetch(URL_HTTPS, resolver=resolver([PUBLIC]),
                             connector=bad)
                self.assertEqual("REDIRECT_BLOCKED", ctx.exception.code)

    def test_https_to_http_downgrade_is_refused(self):
        fake = fake_connector({URL_HTTPS: redirect("http://rival.example/plain")})
        with self.assertRaises(sf.SafeFetchError) as ctx:
            sf.fetch(URL_HTTPS, resolver=resolver([PUBLIC]), connector=fake)
        self.assertEqual("REDIRECT_DOWNGRADE", ctx.exception.code)
        self.assertEqual([URL_HTTPS], [t["url"] for t in fake.calls])

    def test_http_to_https_upgrade_is_allowed(self):
        final = "https://rival.example/pricing"
        fake = fake_connector({URL_HTTP: redirect(final), final: page(HTML_BODY)})
        result = sf.fetch(URL_HTTP, resolver=resolver([PUBLIC]), connector=fake)
        self.assertEqual(final, result["final_url"])
        self.assertEqual([PUBLIC, PUBLIC], [t["ip"] for t in fake.calls])


# ---------------------------------------------------------------------------
# 6. 响应边界：字节上限、总时长预算、状态码、内容类型
# ---------------------------------------------------------------------------

class ResponseBoundTests(SafeFetchCase):
    def test_body_over_the_byte_cap_is_refused(self):
        fake = fake_connector({URL_HTTPS: page("x" * 101)})
        with self.assertRaises(sf.SafeFetchError) as ctx:
            sf.fetch(URL_HTTPS, resolver=resolver([PUBLIC]), connector=fake,
                     max_bytes=100)
        self.assertEqual("TOO_LARGE", ctx.exception.code)
        self.assertEqual("RATE_LIMITED", ctx.exception.c01)

    def test_declared_content_length_over_the_cap_is_refused(self):
        fake = fake_connector({URL_HTTPS: page(
            "hi", headers={"Content-Length": "9999999"})})
        with self.assertRaises(sf.SafeFetchError) as ctx:
            sf.fetch(URL_HTTPS, resolver=resolver([PUBLIC]), connector=fake,
                     max_bytes=100)
        self.assertEqual("TOO_LARGE", ctx.exception.code)

    def test_blocked_statuses_are_refused(self):
        for status in (401, 403, 404, 429, 451, 500, 503):
            with self.subTest(status=status):
                fake = fake_connector({URL_HTTPS: page("denied", status=status)})
                with self.assertRaises(sf.SafeFetchError) as ctx:
                    sf.fetch(URL_HTTPS, resolver=resolver([PUBLIC]),
                             connector=fake)
                self.assertEqual("BLOCKED_STATUS", ctx.exception.code)
                self.assertEqual("STALE_SOURCE", ctx.exception.c01)

    def test_blocked_content_type_is_refused(self):
        fake = fake_connector({URL_HTTPS: page(
            "binary", ctype="application/octet-stream")})
        with self.assertRaises(sf.SafeFetchError) as ctx:
            sf.fetch(URL_HTTPS, resolver=resolver([PUBLIC]), connector=fake)
        self.assertEqual("BLOCKED_CONTENT_TYPE", ctx.exception.code)
        self.assertEqual("POLICY_DENIED", ctx.exception.c01)

    def test_missing_content_type_and_xhtml_are_tolerated(self):
        for ctype in ("", "application/xhtml+xml"):
            with self.subTest(ctype=ctype):
                fake = fake_connector({URL_HTTPS: page(HTML_BODY, ctype=ctype)})
                result = sf.fetch(URL_HTTPS, resolver=resolver([PUBLIC]),
                                  connector=fake)
                self.assertIn("Pro plan", result["text"])

    def test_total_time_budget_is_enforced_before_connecting(self):
        ticks = {"now": 0.0}

        def clock():
            ticks["now"] += 10.0
            return ticks["now"]

        fake = fake_connector({URL_HTTPS: page(HTML_BODY)})
        with self.assertRaises(sf.SafeFetchError) as ctx:
            sf.fetch(URL_HTTPS, resolver=resolver([PUBLIC]), connector=fake,
                     max_seconds=5, clock=clock)
        self.assertEqual("RATE_LIMITED", ctx.exception.code)
        self.assertEqual("RATE_LIMITED", ctx.exception.c01)
        self.assertEqual([], fake.calls)

    def test_every_reason_code_maps_to_a_c01_code(self):
        from core.grow_runbook import C01_ERROR_CODES
        self.assertIn("BLOCKED_ADDRESS", sf.C01_BY_CODE)
        self.assertIn("BLOCKED_PINNED_ADDRESS", sf.C01_BY_CODE)
        self.assertIn("TLS_FAILED", sf.C01_BY_CODE)
        self.assertIn("RATE_LIMITED", sf.C01_BY_CODE)
        unknown = set(sf.C01_BY_CODE.values()) - set(C01_ERROR_CODES)
        self.assertEqual(set(), unknown, "reason code 必须落在 C-01 家族里")

    def test_file_urls_are_refused_on_the_network_path(self):
        with self.assertRaises(sf.SafeFetchError) as ctx:
            sf.fetch("file:///etc/hosts", resolver=resolver([PUBLIC]),
                     connector=fake_connector({}))
        self.assertEqual("FILE_DISABLED", ctx.exception.code)


# ---------------------------------------------------------------------------
# 真实连接层（只连 127.0.0.1 临时端口）：Host 头、手动重定向、读超时
# ---------------------------------------------------------------------------

class DefaultConnectorTests(SafeFetchCase):
    def test_plain_http_sends_the_original_host_header(self):
        server = HttpServer({"/pricing": (200, {"Content-Type": "text/html"},
                                          HTML_BODY)})
        self.addCleanup(server.close)
        result = sf.fetch(URL_HTTP, resolver=resolver([PUBLIC]),
                          connector=loopback_connector(server.port))
        self.assertIn("Pro plan", result["text"])
        self.assertEqual(1, len(server.requests))
        self.assertEqual("/pricing", server.requests[0]["path"])
        self.assertEqual("rival.example", server.requests[0]["headers"]["host"])
        self.assertEqual("injected", result["record"]["connector"])

    def test_default_connector_does_not_follow_redirects_by_itself(self):
        server = HttpServer({
            "/old": (302, {"Location": "/new"}, ""),
            "/new": (200, {"Content-Type": "text/html"}, HTML_BODY),
        })
        self.addCleanup(server.close)
        result = sf.fetch("http://rival.example/old",
                          resolver=resolver([PUBLIC]),
                          connector=loopback_connector(server.port))
        self.assertEqual("http://rival.example/new", result["final_url"])
        self.assertEqual(["http://rival.example/new"], result["redirects"])
        self.assertEqual(["/old", "/new"],
                         [row["path"] for row in server.requests])
        self.assertEqual(2, len(result["hops"]))

    def test_read_timeout_is_reported_as_fetch_timeout(self):
        server = SilentServer()
        self.addCleanup(server.close)
        with self.assertRaises(sf.SafeFetchError) as ctx:
            sf.fetch(URL_HTTP, resolver=resolver([PUBLIC]),
                     connector=loopback_connector(server.port),
                     read_timeout=0.2)
        self.assertEqual("FETCH_TIMEOUT", ctx.exception.code)
        self.assertEqual("RATE_LIMITED", ctx.exception.c01)

    def test_oversized_real_response_is_refused(self):
        server = HttpServer({"/pricing": (200, {"Content-Type": "text/html"},
                                          "x" * 5000)})
        self.addCleanup(server.close)
        with self.assertRaises(sf.SafeFetchError) as ctx:
            sf.fetch(URL_HTTP, resolver=resolver([PUBLIC]),
                     connector=loopback_connector(server.port), max_bytes=1000)
        self.assertEqual("TOO_LARGE", ctx.exception.code)


# ---------------------------------------------------------------------------
# 5. HTTPS：证书与 SNI 仍针对原域名
# ---------------------------------------------------------------------------

class TlsPinTests(SafeFetchCase):
    def trusting(self, cafile):
        """信任锚换成测试自签证书；证书校验与主机名校验都保持开启。"""
        def factory():
            context = ssl.create_default_context()
            context.load_verify_locations(cafile=str(FIXTURES / cafile))
            return context

        return factory

    def test_certificate_is_checked_against_the_original_domain(self):
        server = TlsServer("domain_san.crt", "domain_san.key")
        self.addCleanup(server.close)
        result = sf.fetch(URL_HTTPS, resolver=resolver([PUBLIC]),
                          connector=loopback_connector(server.port),
                          tls_context=self.trusting("domain_san.crt"))
        self.assertIn("Pro plan", result["text"])
        self.assertEqual("rival.example", server.requests[0]["headers"]["host"])
        self.assertEqual(["rival.example"], server.sni, "SNI 必须是原域名")

    def test_ip_san_certificate_is_not_accepted_for_the_domain(self):
        # 同一个信任锚下：域名 SAN 能过、IP SAN 过不了 → 校验的是域名，不是 IP。
        server = TlsServer("ip_san.crt", "ip_san.key")
        self.addCleanup(server.close)
        with self.assertRaises(sf.SafeFetchError) as ctx:
            sf.fetch(URL_HTTPS, resolver=resolver([PUBLIC]),
                     connector=loopback_connector(server.port),
                     tls_context=self.trusting("ip_san.crt"))
        self.assertEqual("TLS_FAILED", ctx.exception.code)
        self.assertIsInstance(ctx.exception.__cause__, ssl.SSLError)
        self.assertEqual([], server.requests, "握手失败就不能发出 HTTP 请求")

    def test_untrusted_self_signed_certificate_fails_the_handshake(self):
        server = TlsServer("domain_san.crt", "domain_san.key")
        self.addCleanup(server.close)
        with self.assertRaises(sf.SafeFetchError) as ctx:
            sf.fetch(URL_HTTPS, resolver=resolver([PUBLIC]),
                     connector=loopback_connector(server.port))
        self.assertEqual("TLS_FAILED", ctx.exception.code)
        self.assertIsInstance(ctx.exception.__cause__, ssl.SSLError)
        self.assertEqual([], server.requests)

    def test_context_with_verification_disabled_is_refused(self):
        def disabled():
            context = ssl.create_default_context()
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
            return context

        server = TlsServer("domain_san.crt", "domain_san.key")
        self.addCleanup(server.close)
        with self.assertRaises(sf.SafeFetchError) as ctx:
            sf.fetch(URL_HTTPS, resolver=resolver([PUBLIC]),
                     connector=loopback_connector(server.port),
                     tls_context=disabled)
        self.assertEqual("TLS_FAILED", ctx.exception.code)
        self.assertIn("证书", str(ctx.exception))
        self.assertEqual([], server.requests)


if __name__ == "__main__":
    unittest.main()
