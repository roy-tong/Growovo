"""G-06 验收冻结：竞争监控 URL 与事件真实性（P2）。

先于实现编写。冻结五组语义：

1. URL 准入：只收 http/https；file:// 只在显式本地模式并提供注入抓取器时可读，
   生产默认路径一律拒绝。
2. 解析后地址判定：按 DNS 结果判 loopback / 私网 / 链路本地 / 保留 / 组播 /
   IPv6 唯一本地 / IPv4 映射 IPv6 / 0.0.0.0 / 历史十进制八进制十六进制写法；
   localhost 及子域、*.local、单段主机名一律拒绝。判定逻辑纯函数可单测。
3. 重定向：手工逐跳跟随，每一跳重新做地址判定；跳数上限、https→http 降级拒绝、
   相对 Location、非 http(s) 跳转都拒绝；回填 URL 经重定向到内网必须拒绝。
4. 响应边界：大小上限、超时、内容类型、403/验证码/登录墙/空壳页不得改基线。
5. 事件真实性：脚本/模板噪声、随机 token、时间戳不算竞争事件；整页 diff 在
   人工核实前只记 `page_content_changed`（needs_review），价格只能用结构化
   token 做提示，人工确认写入追加式审计，不改原事件。

全部离线：注入 fake resolver + fake transport，零网络、零真实请求。
"""
import json
import tempfile
import unittest
from pathlib import Path

from core import watch_safety as ws
from core.watch import WatchError, WatchList

PUBLIC_V4 = "93.184.216.34"
PUBLIC_V6 = "2606:4700:4700::1111"
PUBLIC_IP = [PUBLIC_V4]


def resolver(addresses=PUBLIC_IP):
    calls = []

    def fn(host, port):
        calls.append((host, int(port)))
        return list(addresses)

    fn.calls = calls
    return fn


def page(text, *, status=200, ctype="text/html; charset=utf-8", headers=None):
    merged = {"content-type": ctype}
    merged.update(headers or {})
    return {"status": status, "headers": merged, "body": text}


def redirect(location, *, status=302, headers=None):
    merged = {"location": location}
    merged.update(headers or {})
    return {"status": status, "headers": merged, "body": ""}


def transport(pages):
    calls = []

    def fn(url):
        calls.append(url)
        hit = pages.get(url)
        if hit is None:
            raise AssertionError(f"unexpected fetch: {url}")
        return dict(hit()) if callable(hit) else dict(hit)

    fn.calls = calls
    return fn


def html(text):
    return f"<html><body><p>{text}</p></body></html>"


class TmpDir(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.path = self.dir / "watch" / "rivals.json"
        WatchList.start(self.path, "rivals")
        self.watch = WatchList(self.path)


class AddressMatrixTests(unittest.TestCase):
    """解析后地址矩阵：是 IP 就按 IP 判，是名字就按名字判，不猜。"""

    def test_forbidden_ipv4_matrix(self):
        cases = {
            "127.0.0.1": "loopback",
            "127.255.255.254": "loopback",
            "10.0.0.5": "private",
            "172.16.0.1": "private",
            "172.31.255.255": "private",
            "192.168.1.1": "private",
            "169.254.169.254": "link",
            "0.0.0.0": "unspecified",
            "224.0.0.1": "multicast",
            "240.0.0.1": "reserved",
            "255.255.255.255": "reserved",
            "100.64.0.1": "not_global",
            "198.18.0.1": "not_global",
            "192.0.2.10": "not_global",
        }
        for address, reason in cases.items():
            with self.subTest(address=address):
                result = ws.classify_address(address)
                self.assertFalse(result["allowed"], address)
                self.assertIn(reason, result["reason"].lower(), result["reason"])

    def test_public_ipv4_allowed(self):
        for address in ("93.184.216.34", "8.8.8.8", "1.1.1.1", "20.205.243.166"):
            with self.subTest(address=address):
                self.assertTrue(ws.classify_address(address)["allowed"])

    def test_forbidden_ipv6_matrix(self):
        cases = {
            "::1": "loopback",
            "fe80::1": "link",
            "fc00::1": "private",
            "fd12:3456::1": "private",
            "ff02::1": "multicast",
            "::": "unspecified",
            "2001:db8::1": "not_global",
            "::ffff:127.0.0.1": "mapped",
            "::ffff:10.0.0.5": "mapped",
        }
        for address, reason in cases.items():
            with self.subTest(address=address):
                result = ws.classify_address(address)
                self.assertFalse(result["allowed"], address)
                self.assertIn(reason, result["reason"].lower(), result["reason"])

    def test_public_ipv6_allowed(self):
        self.assertTrue(ws.classify_address(PUBLIC_V6)["allowed"])

    def test_legacy_ipv4_forms_are_rejected(self):
        # 历史 shorthand：十进制、八进制、十六进制、少段、0 都要按 127/0 处理。
        for address in ("2130706433", "017700000001", "0177.0.0.1",
                        "0x7f.0.0.1", "0x7f000001", "127.1", "0",
                        "127.0.1"):
            with self.subTest(address=address):
                result = ws.classify_address(address)
                self.assertFalse(result["allowed"], address)

    def test_hostname_rules(self):
        forbidden = ("localhost", "api.localhost", "LOCALHOST.",
                     "printer.local", "nas.local.", "intranet", "router",
                     "metadata.google.internal", "box.home.arpa")
        for host in forbidden:
            with self.subTest(host=host):
                result = ws.classify_host(host)
                self.assertFalse(result["allowed"], host)
        for host in ("rival.example", "example.com", "status.rival.io"):
            with self.subTest(host=host):
                self.assertTrue(ws.classify_host(host)["allowed"])

    def test_resolved_addresses_all_must_be_public(self):
        with self.assertRaises(ws.UrlPolicyError) as ctx:
            ws.check_url("https://rival.example/price",
                         resolver=resolver(["93.184.216.34", "127.0.0.1"]))
        self.assertEqual("BLOCKED_ADDRESS", ctx.exception.code)
        with self.assertRaises(ws.UrlPolicyError):
            ws.check_url("https://rival.example/price",
                         resolver=resolver(["10.0.0.5"]))
        ok = ws.check_url("https://rival.example/price",
                          resolver=resolver([PUBLIC_V4]))
        self.assertTrue(ok["allowed"])
        self.assertEqual([PUBLIC_V4], ok["addresses"])

    def test_resolution_failure_or_empty_is_rejected(self):
        def broken(host, port):
            raise OSError("dns down")

        for fn in (broken, resolver([])):
            with self.subTest(fn=fn):
                with self.assertRaises(ws.UrlPolicyError) as ctx:
                    ws.check_url("https://rival.example/price", resolver=fn)
                self.assertIn(ctx.exception.code,
                              ("RESOLUTION_FAILED", "BLOCKED_ADDRESS"))

    def test_scheme_matrix(self):
        for url in ("ftp://example.com/a", "file:///etc/hosts",
                    "javascript:alert(1)", "data:text/html,hi",
                    "gopher://example.com/"):
            with self.subTest(url=url):
                with self.assertRaises(ws.UrlPolicyError) as ctx:
                    ws.check_url(url, resolver=resolver())
                self.assertIn(ctx.exception.code,
                              ("UNSUPPORTED_SCHEME", "FILE_DISABLED"))

    def test_bad_urls_rejected(self):
        for url in ("https://user:pass@example.com/", "https:///path",
                    "https://example.com:0/", "https://exa mple.com/",
                    "http://[::1]/"):
            with self.subTest(url=url):
                with self.assertRaises(ws.UrlPolicyError):
                    ws.check_url(url, resolver=resolver())


class FetchPolicyTests(TmpDir):
    """响应边界：每一跳都判定，403/验证页/超限都不改基线。"""

    URL = "https://rival.example/pricing"

    def _add(self, transport_fn, *, resolver_fn=None):
        return self.watch.add(
            self.URL, "price", "Rival", transport=transport_fn,
            resolver=resolver_fn or resolver())

    def test_public_page_fetch_ok(self):
        result = ws.fetch_page(
            self.URL, transport=transport({self.URL: page(html("Pro plan costs 49 dollars per month"))}),
            resolver=resolver())
        self.assertIn("Pro plan", result["text"])
        self.assertEqual(self.URL, result["final_url"])
        self.assertEqual([], result["redirects"])

    def test_size_limit_rejects(self):
        big = "x" * (ws.MAX_BYTES + 10)
        with self.assertRaises(ws.UrlPolicyError) as ctx:
            ws.fetch_page(self.URL, transport=transport({self.URL: page(big)}),
                          resolver=resolver())
        self.assertEqual("TOO_LARGE", ctx.exception.code)

    def test_content_type_rejected(self):
        with self.assertRaises(ws.UrlPolicyError) as ctx:
            ws.fetch_page(
                self.URL, resolver=resolver(),
                transport=transport({self.URL: page(
                    "binary", ctype="application/octet-stream")}))
        self.assertEqual("BLOCKED_CONTENT_TYPE", ctx.exception.code)

    def test_403_keeps_baseline_and_is_not_event(self):
        self._add(transport({self.URL: page(html("Pro plan costs 49 dollars per month"))}))
        before = self.watch.load()["entries"][0]["last_snapshot"]
        result = self.watch.check(
            transport=transport({self.URL: page("denied", status=403)}),
            resolver=resolver(), log=lambda *a, **k: None)
        self.assertEqual([], result["events"])
        self.assertEqual(1, result["failed"])
        entry = self.watch.load()["entries"][0]
        self.assertEqual(before, entry["last_snapshot"])
        self.assertEqual(0, entry["changes"])

    def test_challenge_and_login_wall_keep_baseline(self):
        self._add(transport({self.URL: page(html("Pro plan costs 49 dollars per month"))}))
        before = self.watch.load()["entries"][0]["last_snapshot"]
        for body in ("Just a moment... checking your browser",
                     "Verify you are human — captcha",
                     "Sign in to continue reading this page",
                     "Please log in to view pricing"):
            with self.subTest(body=body):
                result = self.watch.check(
                    transport=transport({self.URL: page(body)}),
                    resolver=resolver(), log=lambda *a, **k: None)
                self.assertEqual([], result["events"])
                self.assertEqual(1, result["failed"])
                entry = self.watch.load()["entries"][0]
                self.assertEqual(before, entry["last_snapshot"])
                self.assertEqual(0, entry["changes"])

    def test_empty_shell_keeps_baseline(self):
        self._add(transport({self.URL: page(html("Pro plan costs 49 dollars per month"))}))
        before = self.watch.load()["entries"][0]["last_snapshot"]
        result = self.watch.check(
            transport=transport({self.URL: page(
                '<html><body><div id="app"></div><script>1</script></body></html>')}),
            resolver=resolver(), log=lambda *a, **k: None)
        self.assertEqual([], result["events"])
        self.assertEqual(before, self.watch.load()["entries"][0]["last_snapshot"])

    def test_redirect_to_private_is_rejected(self):
        backfill = "http://127.0.0.1/admin"
        fake = transport({self.URL: redirect(backfill)})
        with self.assertRaises(ws.UrlPolicyError) as ctx:
            ws.fetch_page(self.URL, transport=fake, resolver=resolver())
        self.assertEqual("REDIRECT_BLOCKED", ctx.exception.code)
        self.assertEqual([self.URL], fake.calls)  # 内网那跳没有被抓取

    def test_each_redirect_hop_is_revalidated(self):
        second = "https://cdn.rival.example/pricing"
        third = "https://rival.example/final"
        fake = transport({
            self.URL: redirect(second, status=301),
            second: redirect(third, status=307),
            third: page(html("Pro plan costs 49 dollars per month")),
        })
        calls = []

        def multi_resolver(host, port):
            calls.append(host)
            return [PUBLIC_V4]

        result = ws.fetch_page(self.URL, transport=fake,
                               resolver=multi_resolver)
        self.assertEqual(third, result["final_url"])
        self.assertEqual(2, len(result["redirects"]))
        self.assertEqual(["rival.example", "cdn.rival.example",
                          "rival.example"], calls)

    def test_https_to_http_downgrade_rejected(self):
        fake = transport({self.URL: redirect("http://rival.example/plain")})
        with self.assertRaises(ws.UrlPolicyError) as ctx:
            ws.fetch_page(self.URL, transport=fake, resolver=resolver())
        self.assertEqual("REDIRECT_DOWNGRADE", ctx.exception.code)

    def test_http_to_https_upgrade_allowed(self):
        start = "http://rival.example/pricing"
        final = "https://rival.example/pricing"
        result = ws.fetch_page(
            start, resolver=resolver(),
            transport=transport({start: redirect(final),
                                 final: page(html("Pro plan costs 49 dollars"))}))
        self.assertEqual(final, result["final_url"])

    def test_redirect_limit_and_loop(self):
        url = "https://rival.example/1"
        chain = {}
        for i in range(1, ws.MAX_REDIRECTS + 2):
            chain[f"https://rival.example/{i}"] = redirect(
                f"https://rival.example/{i + 1}")
        with self.assertRaises(ws.UrlPolicyError) as ctx:
            ws.fetch_page(url, transport=transport(chain), resolver=resolver())
        self.assertEqual("TOO_MANY_REDIRECTS", ctx.exception.code)
        loop = transport({url: redirect(url)})
        with self.assertRaises(ws.UrlPolicyError) as ctx:
            ws.fetch_page(url, transport=loop, resolver=resolver())
        self.assertEqual("REDIRECT_LOOP", ctx.exception.code)

    def test_redirect_without_location_or_bad_scheme(self):
        for location in ("", "file:///etc/passwd", "javascript:alert(1)"):
            with self.subTest(location=location):
                fake = transport({self.URL: redirect(location)})
                with self.assertRaises(ws.UrlPolicyError):
                    ws.fetch_page(self.URL, transport=fake, resolver=resolver())

    def test_5xx_is_transient_and_keeps_baseline(self):
        self._add(transport({self.URL: page(html("Pro plan costs 49 dollars per month"))}))
        before = self.watch.load()["entries"][0]["last_snapshot"]
        result = self.watch.check(
            transport=transport({self.URL: page("oops", status=503)}),
            resolver=resolver(), log=lambda *a, **k: None)
        self.assertEqual([], result["events"])
        self.assertEqual(before, self.watch.load()["entries"][0]["last_snapshot"])

    def test_injected_transport_without_resolver_is_soft_but_literal_blocks(self):
        # 注入传输层时静态检查仍然生效：字面私网地址不会因为「有 fake」就放行。
        with self.assertRaises(ws.UrlPolicyError):
            ws.check_url("http://127.0.0.1/", resolver=None)
        with self.assertRaises(ws.UrlPolicyError):
            ws.check_url("http://localhost/", resolver=None)
        ok = ws.check_url("https://rival.example/price", resolver=None)
        self.assertTrue(ok["allowed"])


class EventAuthenticityTests(TmpDir):
    URL = "https://rival.example/pricing"

    def _add(self, text, *, kind="price"):
        self.watch.add(self.URL, kind, "Rival", resolver=resolver(),
                       transport=transport({self.URL: page(html(text))}))

    def _check(self, text):
        return self.watch.check(
            resolver=resolver(),
            transport=transport({self.URL: page(html(text))}),
            log=lambda *a, **k: None)

    def test_script_and_style_only_changes_are_not_events(self):
        self._add("Pro plan costs 49 dollars per month")
        result = self.watch.check(
            resolver=resolver(),
            transport=transport({self.URL: page(
                "<html><head><script>var n=2</script>"
                "<style>.a{color:red}</style></head><body>"
                "<p>Pro plan costs 49 dollars per month</p></body></html>")}),
            log=lambda *a, **k: None)
        self.assertEqual([], result["events"])

    def test_random_tokens_and_timestamps_are_not_events(self):
        base = "Pro plan costs 49 dollars per month. Updated 2026-11-15T09:00:00. " \
               "Request 550e8400-e29b-41d4-a716-446655440000 nonce a3f9c2d81b7e " \
               "build 123456789012."
        self._add(base)
        noisy = "Pro plan costs 49 dollars per month. Updated 2026-11-16T10:30:00. " \
                "Request 123e4567-e89b-12d3-a456-426614174000 nonce 9f8e7d6c5b4a " \
                "build 987654321098."
        result = self._check(noisy)
        self.assertEqual([], result["events"])

    def test_real_text_change_is_page_content_changed_needs_review(self):
        self._add("Pro plan costs 49 dollars per month")
        result = self._check("Pro plan now costs 49 dollars per month, with SSO")
        self.assertEqual(1, len(result["events"]))
        event = result["events"][0]
        self.assertEqual("page_content_changed", event["event_type"])
        self.assertEqual("needs_review", event["verification"])
        self.assertNotIn("price_change", json.dumps(event, ensure_ascii=False))
        self.assertNotIn("涨价", json.dumps(event, ensure_ascii=False))
        self.assertEqual("candidate_after_review", event["tactics_status"])
        self.assertIn("price-hike-harvest", event["tactics"])

    def test_structured_price_tokens_are_hints_not_claims(self):
        self._add("Pro plan costs $49 per month")
        result = self._check("Pro plan costs $99 per month")
        event = result["events"][0]
        self.assertEqual("needs_review", event["verification"])
        structured = event["structured"]
        self.assertEqual(["$49"], structured["price_tokens_before"])
        self.assertEqual(["$99"], structured["price_tokens_after"])
        self.assertEqual("price_tokens_changed", structured["hint"])
        self.assertNotIn("claim", structured)
        self.assertEqual("content_changed", event["claim"])
        self.assertEqual("unverified", event["claim_status"])

    def test_human_confirmation_is_append_only_audit(self):
        self._add("Pro plan costs $49 per month")
        event = self._check("Pro plan costs $99 per month")["events"][0]
        raw_before = self.watch.events_path.read_text(encoding="utf-8")
        with self.assertRaises(WatchError):
            self.watch.confirm_event(event["id"], claim="price_change",
                                     by="", note="")
        with self.assertRaises(WatchError):
            self.watch.confirm_event("no-such-event", claim="price_change",
                                     by="roy", note="x")
        with self.assertRaises(WatchError):
            self.watch.confirm_event(event["id"], claim="made_up",
                                     by="roy", note="x")
        record = self.watch.confirm_event(
            event["id"], claim="price_change", by="roy",
            note="平台价格页显示 99，已截图")
        self.assertEqual("roy", record["by"])
        self.assertEqual(raw_before,
                         self.watch.events_path.read_text(encoding="utf-8"))
        view = self.watch.events()[0]
        self.assertEqual("confirmed", view["verification"])
        self.assertEqual("price_change", view["claim"])
        self.assertEqual("roy", view["verified_by"])
        # 人工判为误报：只追加，不删原事件
        self.watch.confirm_event(event["id"], claim="rejected", by="ann",
                                 note="只是页面区块重排")
        latest = self.watch.events()[0]
        self.assertEqual("rejected", latest["verification"])
        self.assertEqual(raw_before,
                         self.watch.events_path.read_text(encoding="utf-8"))

    def test_noise_filter_does_not_hide_real_price_change(self):
        self.assertTrue(ws.is_significant_change(
            "Pro plan costs $49 per month", "Pro plan costs $99 per month"))
        self.assertFalse(ws.is_significant_change(
            "Updated 2026-11-15T09:00:00 req 550e8400-e29b-41d4-a716-446655440000",
            "Updated 2026-11-16T10:30:00 req 123e4567-e89b-12d3-a456-426614174000"))
        self.assertEqual(["$49", "$99"],
                         ws.extract_price_tokens("$49 → $99"))


class UrlModeTests(TmpDir):
    URL = "https://rival.example/price"

    def test_file_is_rejected_on_production_path(self):
        page_path = self.dir / "local.html"
        page_path.write_text(html("Pro plan costs 49 dollars per month"),
                             encoding="utf-8")
        with self.assertRaises(WatchError) as ctx:
            self.watch.add(page_path.as_uri(), "price", "Rival")
        self.assertIn("file", str(ctx.exception).lower())

    def test_file_allowed_only_with_explicit_local_mode(self):
        page_path = self.dir / "local.html"
        page_path.write_text(html("Pro plan costs 49 dollars per month"),
                             encoding="utf-8")
        with self.assertRaises(WatchError):
            self.watch.add(page_path.as_uri(), "price", "Rival",
                           allow_local_file=True,
                           fetcher=lambda u: html("Pro plan costs 49 dollars per month"))
        # 显式本地模式 + 注入抓取器：只在离线演示/测试使用
        entry = self.watch.add(
            page_path.as_uri(), "price", "Rival", allow_local_file=True,
            fetcher=lambda u: html("Pro plan costs 49 dollars per month"))
        self.assertEqual(1, entry["baseline_len"] > 0)

    def test_private_literal_rejected_before_fetch(self):
        calls = []

        def fetcher(url):
            calls.append(url)
            return html("Pro plan costs 49 dollars per month")

        for url in ("http://127.0.0.1/price", "http://localhost/price",
                    "http://10.0.0.5/price", "http://192.168.1.1/price",
                    "http://169.254.169.254/latest",
                    "http://[::1]/price", "http://2130706433/price"):
            with self.subTest(url=url):
                with self.assertRaises(WatchError):
                    self.watch.add(url, "price", "Rival", fetcher=fetcher)
        self.assertEqual([], calls)

    def test_public_page_is_monitorable_end_to_end(self):
        fake = transport({self.URL: page(html("Pro plan costs 49 dollars per month"))})
        self.watch.add(self.URL, "price", "Rival", transport=fake,
                       resolver=resolver())
        changed = transport({self.URL: page(html("Pro plan costs 99 dollars per month"))})
        result = self.watch.check(transport=changed, resolver=resolver(),
                                  log=lambda *a, **k: None)
        self.assertEqual(1, len(result["events"]))
        self.assertEqual(1, len(self.watch.events()))

    def test_redirect_reaching_private_is_refused_for_add(self):
        fake = transport({self.URL: redirect("http://192.168.1.10/price")})
        with self.assertRaises(WatchError):
            self.watch.add(self.URL, "price", "Rival", transport=fake,
                           resolver=resolver())
        self.assertEqual([], self.watch.load()["entries"])

    def test_existing_entry_redirecting_private_keeps_baseline(self):
        self.watch.add(self.URL, "price", "Rival",
                       transport=transport({self.URL: page(html("Pro plan costs 49 dollars per month"))}),
                       resolver=resolver())
        before = self.watch.load()["entries"][0]["last_snapshot"]
        fake = transport({self.URL: redirect("http://127.0.0.1/admin")})
        result = self.watch.check(transport=fake, resolver=resolver(),
                                  log=lambda *a, **k: None)
        self.assertEqual([], result["events"])
        self.assertEqual(1, result["failed"])
        entry = self.watch.load()["entries"][0]
        self.assertEqual(before, entry["last_snapshot"])
        self.assertEqual(0, entry["changes"])

    def test_final_public_url_is_recorded_for_audit(self):
        second = "https://rival.example/new-price"
        fake = transport({self.URL: redirect(second),
                          second: page(html("Pro plan costs 49 dollars per month"))})
        self.watch.add(self.URL, "price", "Rival", transport=fake,
                       resolver=resolver())
        entry = self.watch.load()["entries"][0]
        self.assertEqual(second, entry["last_final_url"])
        self.assertEqual(self.URL, entry["url"])


if __name__ == "__main__":
    unittest.main()
