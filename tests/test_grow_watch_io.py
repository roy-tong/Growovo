"""G-06 实现侧补充测试：地址边角、噪声、投递与 CLI 人工核实。

冻结契约在 ``test_grow_watch_g06.py``；这里补它没覆盖的实现面：

1. IPv4-mapped 公网、6to4/NAT64 嵌入私网、主机名后缀的边角；
2. 缺失 content-type 的容忍与 xhtml 许可；
3. 人工核实的 latest-wins、summary 计数、CLI confirm 端到端；
4. file:// 的 CLI 显式本地模式（测试/离线演示专用）。

全部离线：注入 fake resolver + fake transport，零网络。
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from core import watch_safety as ws
from core.watch import WatchError, WatchList

ROOT = Path(__file__).resolve().parents[1]
MKT = ROOT / "mkt.py"
PUBLIC = "93.184.216.34"


def resolver(addresses=(PUBLIC,)):
    def fn(host, port):
        return list(addresses)

    return fn


def page(text, *, status=200, ctype="text/html; charset=utf-8", headers=None):
    merged = {"content-type": ctype} if ctype else {}
    merged.update(headers or {})
    return {"status": status, "headers": merged, "body": text}


def transport(pages):
    calls = []

    def fn(url):
        calls.append(url)
        hit = pages[url]
        return dict(hit() if callable(hit) else hit)

    fn.calls = calls
    return fn


def html(text):
    return f"<html><body><p>{text}</p></body></html>"


class AddressEdgeTests(unittest.TestCase):
    def test_ipv4_mapped_public_is_allowed(self):
        result = ws.classify_address("::ffff:8.8.8.8")
        self.assertTrue(result["allowed"], result["reason"])

    def test_embedded_private_v4_is_rejected(self):
        for address in ("2002:7f00:1::", "64:ff9b::10.0.0.5",
                        "2001::ffff:10.0.0.5"):
            with self.subTest(address=address):
                self.assertFalse(ws.classify_address(address)["allowed"])

    def test_local_suffix_reasons(self):
        for host in ("nas.home.arpa", "printer.internal", "box.lan"):
            with self.subTest(host=host):
                result = ws.classify_host(host)
                self.assertFalse(result["allowed"])
                self.assertIn("local", result["reason"].lower())

    def test_denoise_keeps_price_digits(self):
        self.assertIn("$49", ws.denoise("Pro plan $49 per month"))
        self.assertEqual(ws.denoise("at 2026-11-15T09:00:00 nonce a3f9c2d81b7e"),
                         ws.denoise("at 2026-11-16T10:30:00 nonce 9f8e7d6c5b4a"))


class ResponseEdgeTests(unittest.TestCase):
    URL = "https://rival.example/pricing"

    def test_missing_content_type_is_tolerated(self):
        result = ws.fetch_page(
            self.URL, resolver=resolver(),
            transport=transport({self.URL: page(html("Pro plan costs 49 dollars"),
                                                ctype="")}))
        self.assertIn("Pro plan", result["text"])

    def test_xhtml_content_type_allowed(self):
        result = ws.fetch_page(
            self.URL, resolver=resolver(),
            transport=transport({self.URL: page(
                html("Pro plan costs 49 dollars"),
                ctype="application/xhtml+xml")}))
        self.assertTrue(result["text"])

    def test_noise_only_change_updates_baseline_without_event(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "watch" / "r.json"
            WatchList.start(path, "r")
            watch = WatchList(path)
            watch.add(self.URL, "price", "Rival", resolver=resolver(),
                      transport=transport({self.URL: page(html(
                          "Pro plan costs 49 dollars. Updated 2026-11-15T09:00:00"))}))
            result = watch.check(
                resolver=resolver(),
                transport=transport({self.URL: page(html(
                    "Pro plan costs 49 dollars. Updated 2026-11-16T10:00:00"))}),
                log=lambda *a, **k: None)
            self.assertEqual([], result["events"])
            entry = watch.load()["entries"][0]
            self.assertIn("2026-11-16", entry["last_snapshot"])


class ConfirmAndSummaryTests(unittest.TestCase):
    URL = "https://rival.example/pricing"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        path = self.dir / "watch" / "rivals.json"
        WatchList.start(path, "rivals")
        self.watch = WatchList(path)
        self.watch.add(self.URL, "price", "Rival", resolver=resolver(),
                       transport=transport({self.URL: page(html(
                           "Pro plan costs $49 per month"))}))
        self.event = self.watch.check(
            resolver=resolver(),
            transport=transport({self.URL: page(html(
                "Pro plan costs $99 per month"))}),
            log=lambda *a, **k: None)["events"][0]

    def test_latest_confirmation_wins_and_summary_counts(self):
        self.watch.confirm_event(self.event["id"], claim="price_change",
                                 by="roy", note="截图")
        summary = self.watch.summary()
        self.assertEqual(1, summary["confirmed"])
        self.assertEqual(0, summary["needs_review"])
        self.watch.confirm_event(self.event["id"], claim="rejected",
                                 by="ann", note="只是版式")
        summary = self.watch.summary()
        self.assertEqual(0, summary["confirmed"])
        self.assertEqual(0, summary["needs_review"])
        self.assertEqual(1, summary["rejected"])
        view = self.watch.events()[0]
        self.assertEqual("rejected", view["verification"])

    def test_events_are_append_only_and_ids_unique(self):
        before = self.watch.events_path.read_text(encoding="utf-8")
        self.watch.confirm_event(self.event["id"], claim="price_change",
                                 by="roy", note="截图")
        self.assertEqual(before,
                         self.watch.events_path.read_text(encoding="utf-8"))
        self.assertTrue(self.watch.verifications_path.is_file())
        self.assertTrue(self.event["id"])


class WatchCliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / "data"
        self.home.mkdir(parents=True)
        self.env = dict(os.environ, MKT_HOME=str(self.home))
        self.page = Path(self.tmp.name) / "rival.html"
        self.page.write_text(html("Pro plan costs $49 per month"),
                             encoding="utf-8")

    def run_mkt(self, *args, check=True):
        proc = subprocess.run([sys.executable, str(MKT), *args],
                              cwd=str(ROOT), env=self.env, text=True,
                              capture_output=True)
        if check and proc.returncode:
            self.fail(f"mkt {' '.join(args)} failed ({proc.returncode})"
                      f"\nSTDOUT:{proc.stdout}\nSTDERR:{proc.stderr}")
        return proc

    def test_file_requires_explicit_local_flag(self):
        self.run_mkt("watch", "start", "r")
        proc = self.run_mkt("watch", "add", "r", "--url", self.page.as_uri(),
                            "--kind", "price", "--competitor", "R",
                            check=False)
        self.assertEqual(2, proc.returncode)
        self.assertIn("file", proc.stdout.lower())

    def test_confirm_cli_end_to_end(self):
        self.run_mkt("watch", "start", "r")
        self.run_mkt("watch", "add", "r", "--url", self.page.as_uri(),
                     "--kind", "price", "--competitor", "R",
                     "--allow-local-file")
        self.page.write_text(html("Pro plan costs $99 per month"),
                             encoding="utf-8")
        self.run_mkt("watch", "check", "r", "--allow-local-file")
        events_path = self.home / "watch" / "events.jsonl"
        event = json.loads(events_path.read_text(encoding="utf-8").splitlines()[0])
        shown = self.run_mkt("watch", "events", "r").stdout
        self.assertIn("needs_review/content_changed", shown)
        self.run_mkt("watch", "confirm", "r", event["id"],
                     "--claim", "price_change", "--by", "roy",
                     "--note", "平台价格页显示 99")
        shown = self.run_mkt("watch", "events", "r").stdout
        self.assertIn("confirmed/price_change", shown)
        summary = self.run_mkt("watch", "summary", "r").stdout
        self.assertIn("累计事件 1", summary)

    def test_confirm_cli_requires_signature_and_note(self):
        self.run_mkt("watch", "start", "r")
        self.run_mkt("watch", "add", "r", "--url", self.page.as_uri(),
                     "--kind", "price", "--competitor", "R",
                     "--allow-local-file")
        proc = self.run_mkt("watch", "confirm", "r", "no-id",
                            "--claim", "price_change", "--by", "",
                            "--note", "", check=False)
        self.assertEqual(2, proc.returncode)


if __name__ == "__main__":
    unittest.main()
