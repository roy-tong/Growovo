"""G-09 实现侧冻结：``core/watch.py`` 的生产抓取路径切到受控抓取器。

G-06 冻结的 ``test_grow_watch_g06.py`` / ``test_grow_watch_io.py`` 保持不变；
本文件只加 G-09 的新契约：

1. 生产默认路径（不注入 transport/fetcher）走 ``core.safe_fetch``：解析一次、
   连接固定到已校验 IP；
2. 抓取记录（entry 的 ``last_fetch`` 与事件里的 ``fetch``）带
   ``resolved_ips`` / ``pinned_ip`` / ``redirect_chain`` / ``bytes`` /
   ``allowed`` / ``reason`` / ``mode`` / ``connector``，便于事后复核；
3. 超字节上限、超时、重定向到私网：明确失败，**不产生** ``page_content_changed``
   事件、不改基线；
4. 旧注入路径（``transport`` / ``fetcher`` / 显式本地 ``file://``）仍然可用，
   但记录里诚实标成 ``injected_*`` / ``local_file``，绝不冒充「已固定连接」。

全部离线：注入 fake resolver + fake connector，零网络。
"""
import tempfile
import unittest
from pathlib import Path

try:  # 红阶段：core/safe_fetch.py 还不存在，每条用例各自变红
    from core import safe_fetch as sf
except Exception as exc:  # noqa: BLE001
    sf = None
    _SF_IMPORT_ERROR = exc
else:
    _SF_IMPORT_ERROR = None

from core.watch import WatchError, WatchList

PUBLIC = "93.184.216.34"
PUBLIC_ALT = "20.205.243.166"
URL = "https://rival.example/pricing"


def no_log(*args, **kwargs):
    pass


def html(text):
    return f"<html><body><p>{text}</p></body></html>"


def page(text, *, status=200, ctype="text/html; charset=utf-8", headers=None):
    merged = {"Content-Type": ctype} if ctype else {}
    merged.update(headers or {})
    return {"status": status, "headers": merged, "body": text}


def redirect(location, *, status=302):
    return {"status": status, "headers": {"Location": location}, "body": ""}


def resolver(addresses):
    calls = []

    def fn(host, port):
        calls.append((host, int(port)))
        return list(addresses)

    fn.calls = calls
    return fn


def rebound_resolver(first, later):
    calls = []

    def fn(host, port):
        calls.append((host, int(port)))
        return list(first if len(calls) == 1 else later)

    fn.calls = calls
    return fn


def fake_connector(pages, *, peer_ip=None):
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


def transport(pages):
    """G-06 的旧注入点：按 URL 返回响应，不参与地址判定。"""
    calls = []

    def fn(url):
        calls.append(url)
        hit = pages[url]
        return dict(hit() if callable(hit) else hit)

    fn.calls = calls
    return fn


class TmpWatch(unittest.TestCase):
    def setUp(self):
        if sf is None:
            self.assertIsNotNone(
                sf, f"G-09 未实现：core/safe_fetch.py 不存在（{_SF_IMPORT_ERROR}）")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.path = self.dir / "watch" / "rivals.json"
        WatchList.start(self.path, "rivals")
        self.watch = WatchList(self.path)

    def add(self, **kwargs):
        kwargs.setdefault("resolver", resolver([PUBLIC]))
        kwargs.setdefault("connector", fake_connector(
            {URL: page(html("Pro plan costs 49 dollars per month"))}))
        return self.watch.add(URL, "price", "Rival", **kwargs)

    def entry(self):
        return self.watch.load()["entries"][0]


class PinnedWatchPathTests(TmpWatch):
    def test_production_default_goes_through_the_pinned_fetcher(self):
        calls = []

        def spy(url, **kwargs):
            calls.append((url, kwargs))
            record = {"mode": "pinned", "connector": "default", "url": url,
                      "final_url": url, "allowed": True, "reason": "",
                      "resolved_ips": [PUBLIC], "pinned_ip": PUBLIC,
                      "redirect_chain": [], "bytes": 42, "status": 200,
                      "host": "rival.example"}
            return {"url": url, "final_url": url, "status": 200,
                    "headers": {"content-type": "text/html"},
                    "text": html("Pro plan costs 49 dollars per month"),
                    "bytes": 42, "redirects": [], "resolved_ips": [PUBLIC],
                    "pinned_ip": PUBLIC, "allowed": True, "reason": "",
                    "mode": "pinned", "redirect_chain": [], "hops": [],
                    "record": record}

        original = sf.fetch
        sf.fetch = spy
        try:
            self.add()
        finally:
            sf.fetch = original
        self.assertEqual([URL], [url for url, _kwargs in calls])
        record = self.entry()["last_fetch"]
        self.assertEqual("pinned", record["mode"])
        self.assertEqual(PUBLIC, record["pinned_ip"])

    def test_add_records_the_pinned_fetch_audit(self):
        self.add()
        record = self.entry()["last_fetch"]
        self.assertEqual("pinned", record["mode"])
        self.assertEqual("injected", record["connector"])
        self.assertEqual([PUBLIC], record["resolved_ips"])
        self.assertEqual(PUBLIC, record["pinned_ip"])
        self.assertTrue(record["allowed"])
        self.assertEqual("", record["reason"])
        self.assertEqual([], record["redirect_chain"])
        self.assertEqual(200, record["status"])
        self.assertGreater(record["bytes"], 0)
        self.assertTrue(record["ts"])

    def test_check_does_not_resolve_twice_for_one_hop(self):
        r = rebound_resolver([PUBLIC], ["10.0.0.5"])
        fake = fake_connector({URL: page(html("Pro plan costs 49 dollars per month"))})
        self.watch.add(URL, "price", "Rival", resolver=r, connector=fake)
        self.assertEqual(1, len(r.calls), "watch 不能先解析一遍再交给安全抓取器")
        self.assertEqual(PUBLIC, fake.calls[0]["ip"])
        self.assertEqual(PUBLIC, self.entry()["last_fetch"]["pinned_ip"])

    def test_change_event_carries_the_fetch_record(self):
        self.add()
        changed = fake_connector({URL: page(html("Pro plan costs 99 dollars per month"))})
        result = self.watch.check(resolver=resolver([PUBLIC]),
                                  connector=changed, log=no_log)
        self.assertEqual(1, len(result["events"]))
        record = result["events"][0]["fetch"]
        self.assertEqual("pinned", record["mode"])
        self.assertEqual([PUBLIC], record["resolved_ips"])
        self.assertEqual(PUBLIC, record["pinned_ip"])
        self.assertTrue(record["allowed"])
        self.assertEqual("", record["reason"])
        self.assertGreater(record["bytes"], 0)
        self.assertEqual([], record["redirect_chain"])

    def test_redirected_change_records_the_hop_chain(self):
        second = "https://rival.example/new-price"
        self.add()
        fake = fake_connector({
            URL: redirect(second),
            second: page(html("Pro plan costs 99 dollars per month")),
        })
        result = self.watch.check(resolver=resolver([PUBLIC]), connector=fake,
                                  log=no_log)
        event = result["events"][0]
        self.assertEqual(second, event["final_url"])
        self.assertEqual([second], event["redirects"])
        chain = event["fetch"]["redirect_chain"]
        self.assertEqual(1, len(chain))
        self.assertEqual(second, chain[0]["location"])
        self.assertEqual(PUBLIC, chain[0]["pinned_ip"])


class FetchFailureIsNotAnEventTests(TmpWatch):
    def setUp(self):
        super().setUp()
        self.add()
        self.before = self.entry()["last_snapshot"]

    def _check(self, connector, **kwargs):
        return self.watch.check(resolver=resolver([PUBLIC]), connector=connector,
                                log=no_log, **kwargs)

    def _assert_untouched(self, result, reason):
        self.assertEqual([], result["events"])
        self.assertEqual(1, result["failed"])
        entry = self.entry()
        self.assertEqual(self.before, entry["last_snapshot"])
        self.assertEqual(0, entry["changes"])
        self.assertFalse(entry["last_fetch"]["allowed"])
        self.assertEqual(reason, entry["last_fetch"]["reason"])

    def test_oversized_response_is_refused_without_an_event(self):
        result = self._check(fake_connector({URL: page("x" * 400)}),
                             fetch_options={"max_bytes": 200})
        self._assert_untouched(result, "TOO_LARGE")
        self.assertEqual("pinned", self.entry()["last_fetch"]["mode"])

    def test_timeout_is_refused_without_an_event(self):
        def timed_out(target):
            raise sf.SafeFetchError("FETCH_TIMEOUT", "读取超时：模拟")

        result = self._check(timed_out)
        self._assert_untouched(result, "FETCH_TIMEOUT")

    def test_redirect_to_private_is_refused_without_connecting_it(self):
        fake = fake_connector({URL: redirect("http://127.0.0.1/admin")})
        result = self._check(fake)
        self._assert_untouched(result, "REDIRECT_BLOCKED")
        self.assertEqual([URL], [target["url"] for target in fake.calls])
        self.assertNotIn("127.0.0.1", [target["ip"] for target in fake.calls])

    def test_connection_landing_elsewhere_is_refused_without_an_event(self):
        fake = fake_connector({URL: page(html("Pro plan costs 99 dollars per month"))},
                              peer_ip="10.0.0.5")
        result = self._check(fake)
        self._assert_untouched(result, "BLOCKED_PINNED_ADDRESS")

    def test_mixed_resolution_is_refused_before_any_connect(self):
        fake = fake_connector({})
        with self.assertRaises(WatchError) as ctx:
            self.watch.add("https://other.example/price", "price", "Other",
                           resolver=resolver([PUBLIC, "10.0.0.5"]),
                           connector=fake)
        self.assertEqual("BLOCKED_ADDRESS", ctx.exception.code)
        self.assertEqual("pinned", ctx.exception.mode)
        self.assertEqual([], fake.calls)
        self.assertEqual(1, len(self.watch.load()["entries"]),
                         "被拒绝的地址不能进清单")


class LegacyInjectionStaysHonestTests(TmpWatch):
    def test_legacy_transport_is_labeled_injected_not_pinned(self):
        legacy = transport({URL: page(html("Pro plan costs 49 dollars per month"))})
        self.watch.add(URL, "price", "Rival", transport=legacy,
                       resolver=resolver([PUBLIC]))
        record = self.entry()["last_fetch"]
        self.assertEqual("injected_transport", record["mode"])
        self.assertIsNone(record["pinned_ip"])
        self.assertEqual([PUBLIC], record["resolved_ips"])

    def test_legacy_fetcher_is_labeled_injected_not_pinned(self):
        self.watch.add("https://other.example/price", "price", "Other",
                       fetcher=lambda url: html("Other plan costs 19 dollars per month"),
                       resolver=resolver([PUBLIC]))
        record = self.watch.load()["entries"][0]["last_fetch"]
        self.assertEqual("injected_fetcher", record["mode"])
        self.assertIsNone(record["pinned_ip"])

    def test_local_file_mode_is_labeled_local_not_pinned(self):
        local = self.dir / "local.html"
        local.write_text(html("Local plan costs 9 dollars per month"),
                         encoding="utf-8")
        self.watch.add(local.as_uri(), "price", "Local", allow_local_file=True,
                       fetcher=lambda url: html("Local plan costs 9 dollars per month"))
        record = self.entry()["last_fetch"]
        self.assertEqual("local_file", record["mode"])
        self.assertIsNone(record["pinned_ip"])
        self.assertTrue(record["allowed"])

    def test_g06_style_transport_flow_still_produces_events(self):
        # 旧注入路径不许绕过静态地址规则，但也不该因为 G-09 失效。
        self.watch.add(URL, "price", "Rival", resolver=resolver([PUBLIC]),
                       transport=transport({URL: page(html(
                           "Pro plan costs 49 dollars per month"))}))
        result = self.watch.check(
            resolver=resolver([PUBLIC]),
            transport=transport({URL: page(html(
                "Pro plan costs 99 dollars per month"))}),
            log=no_log)
        self.assertEqual(1, len(result["events"]))
        self.assertEqual("injected_transport",
                         result["events"][0]["fetch"]["mode"])


if __name__ == "__main__":
    unittest.main()
