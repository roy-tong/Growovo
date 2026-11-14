"""产品能力层：战术组合推荐、对手事件监控、MCP 知识库访问。零真实网络（file:// 除外）。"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MKT = ROOT / "mkt.py"

from core import tactics as tactics_reg
from core.watch import WatchList, WatchError, diff_excerpt, normalize


def _facts():
    return {
        "product": {"name": "Verovo", "what": "AI 账期分摊", "url": "https://example.invalid",
                    "stage": "launched", "product_type": "devtool"},
        "icp": [{"hypothesis": "开发者", "tags": ["developers"],
                 "evidence_label": "hypothesis", "evidence": "待验证"}],
        "baseline": {"followers": 120},
        "value_events": [{"name": "激活", "source": "产品事件", "is_approximation": False}],
        "constraints": {"red_lines": ["不做群发"]},
        "owned_assets": {"platforms": ["x", "email"]},
    }


class TmpDir(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)


class RecommendTests(TmpDir):
    def test_goal_family_dominates_selection(self):
        facts = _facts()
        awareness = tactics_reg.recommend(facts, "awareness.followers", risk_max="white")
        commercial = tactics_reg.recommend(facts, "commercial.first_paid", risk_max="white")
        a_ids = {p["id"] for p in awareness["picks"]}
        self.assertIn("koc-first", a_ids)
        self.assertIn("x-reply-guy", a_ids)
        # commercial 组合必须含商业机制面战术（对手事件/付费放大/价值交换任一）
        c_mechs = {p["mechanism"] for p in commercial["picks"]}
        self.assertTrue(c_mechs & {"competitor_events", "paid_amplify", "value_exchange"})

    def test_channel_scope_excludes_off_platform_tactics(self):
        rec = tactics_reg.recommend(_facts(), risk_max="white")
        for p in rec["picks"]:
            self.assertTrue(p["channel"] in tactics_reg.GENERIC_CHANNELS
                            or p["channel"] in ("x", "email"))

    def test_mechanism_diversity_and_reasons(self):
        rec = tactics_reg.recommend(_facts(), "awareness.followers", risk_max="white")
        mechs = [p["mechanism"] for p in rec["picks"]]
        self.assertLessEqual(max(mechs.count(m) for m in set(mechs)), 2)
        for p in rec["picks"]:
            self.assertTrue(p["why"], p["id"])            # 每条入选都有理由
            self.assertTrue(p["steps"] and p["caution"])  # 全文参数随选随用

    def test_risk_ceiling_and_stale_filter(self):
        rec = tactics_reg.recommend(_facts(), risk_max="white")
        self.assertTrue(all(p["risk"] == "white" for p in rec["picks"]))
        # 最长复核期 365 天：2028 年初全部过期 → 默认空，显式放开才有
        later = tactics_reg.recommend(_facts(), today="2028-01-01")
        self.assertEqual([], later["picks"])
        reopened = tactics_reg.recommend(_facts(), today="2028-01-01", include_stale=True)
        self.assertTrue(reopened["picks"])


class WatchTests(TmpDir):
    def _list(self):
        path = self.dir / "watch" / "rivals.json"
        WatchList.start(path, "rivals")
        return WatchList(path)

    @staticmethod
    def _page(text):
        return f"<html><body>{text}</body></html>"

    def test_diff_flow_emits_event_with_tactic_hints(self):
        wl = self._list()
        url = "https://rival.example/pricing"
        wl.add(url, "price", "Rival",
               fetcher=lambda u: self._page("Pro plan costs 49 dollars per month, billed yearly"))

        # 无变化：不产事件
        result = wl.check(fetcher=lambda u: self._page(
            "Pro plan costs 49 dollars per month, billed yearly"))
        self.assertEqual([], result["events"])

        # 涨价：事件 + 承接战术联动
        result = wl.check(fetcher=lambda u: self._page(
            "Pro plan costs 99 dollars per month, billed yearly"))
        self.assertEqual(1, len(result["events"]))
        event = result["events"][0]
        self.assertEqual("price", event["kind"])
        self.assertIn("price-hike-harvest", event["tactics"])
        self.assertIn("49", event["diff"])
        self.assertIn("99", event["diff"])
        rows = wl.events()                                # 事件落盘且可回读
        self.assertEqual(1, len(rows))
        self.assertEqual(event["ts"], rows[0]["ts"])

    def test_fetch_failure_is_skipped_not_event(self):
        wl = self._list()
        url = "https://rival.example/status"
        wl.add(url, "status", "Rival", fetcher=lambda u: "ok " * 20)

        def flaky(u):
            raise TimeoutError("boom")

        result = wl.check(fetcher=flaky, log=lambda *a, **k: None)
        self.assertEqual([], result["events"])            # 网络失败≠页面变化
        result = wl.check(fetcher=lambda u: "ok " * 20)   # 恢复后正常
        self.assertEqual([], result["events"])

    def test_add_validates_kind_url_and_content(self):
        wl = self._list()
        with self.assertRaises(WatchError):
            wl.add("https://x.com", "weird", "R", fetcher=lambda u: "ok" * 20)
        with self.assertRaises(WatchError):
            wl.add("ftp://x", "price", "R", fetcher=lambda u: "ok" * 20)
        with self.assertRaises(WatchError):
            wl.add("https://x.com", "price", "R", fetcher=lambda u: "thin")
        wl.add("https://rival.example/a", "price", "R", fetcher=lambda u: "ok " * 20)
        with self.assertRaises(WatchError):               # 去重
            wl.add("https://rival.example/a", "price", "R", fetcher=lambda u: "ok " * 20)

    def test_normalize_and_excerpt(self):
        self.assertEqual("a b c", normalize("<p>a  <b>b</b></p>\nc  "))
        excerpt = diff_excerpt("price is 49 dollars", "price is 99 dollars")
        self.assertIn("49", excerpt)
        self.assertIn("99", excerpt)


class McpTacticsTests(unittest.TestCase):
    """直接调 _call_tool（与 tests/test_channels.py 的 MCP 测试同式）。"""

    def _call(self, args):
        from mcp_server import _call_tool
        res = _call_tool("mkt_tactics", args)
        if res.get("isError"):
            return res["content"][0]["text"]
        return json.loads(res["content"][0]["text"])

    def test_list_show_recommend_forbidden(self):
        rows = self._call({"action": "list", "channel": "x", "risk_max": "white"})
        self.assertTrue(any(r["id"] == "x-reply-guy" for r in rows))
        spec = self._call({"action": "show", "id": "koc-first"})
        self.assertEqual("usable", spec["status"])
        self.assertTrue(spec["steps"])
        rec = self._call({"action": "recommend", "facts": _facts(),
                          "goal_id": "awareness.followers"})
        self.assertTrue(rec["picks"])
        forbidden = self._call({"action": "forbidden"})
        self.assertTrue(any(f["id"] == "llms-txt-aeo" for f in forbidden))
        err = self._call({"action": "show", "id": "nope"})
        self.assertIn("未知战术", str(err))

    def test_bad_action_rejected(self):
        err = self._call({"action": "delete-everything"})
        self.assertIn("未知 action", str(err))


class CapabilityCliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / "data"
        self.home.mkdir(parents=True, exist_ok=True)
        self.env = dict(os.environ, MKT_HOME=str(self.home))

    def run_mkt(self, *args, check=True):
        proc = subprocess.run([sys.executable, str(MKT), *args], cwd=ROOT, env=self.env,
                              text=True, capture_output=True)
        if check and proc.returncode:
            self.fail(f"mkt {' '.join(args)} failed ({proc.returncode})"
                      f"\nSTDOUT:{proc.stdout}\nSTDERR:{proc.stderr}")
        return proc

    def test_recommend_and_watch_chain(self):
        facts = self.home / "facts.json"
        facts.write_text(json.dumps(_facts(), ensure_ascii=False), encoding="utf-8")
        out = self.run_mkt("tactic", "recommend", "--facts", str(facts),
                           "--goal", "awareness.followers").stdout
        self.assertIn("战术组合", out)
        self.assertIn("koc-first", out)
        self.assertIn("入选理由", out)

        # watch：file:// 本地页面，零外网
        page = self.home / "rival_price.html"
        page.write_text("<html><body>Pro plan 49 dollars per month for teams</body></html>",
                        encoding="utf-8")
        self.run_mkt("watch", "start", "rivals")
        out = self.run_mkt("watch", "add", "rivals", "--url", page.as_uri(),
                           "--kind", "price", "--competitor", "Rival").stdout
        self.assertIn("基线快照", out)
        self.assertIn("price-hike-harvest", out)

        out = self.run_mkt("watch", "check", "rivals").stdout
        self.assertIn("无变化", out)
        page.write_text("<html><body>Pro plan 99 dollars per month for teams</body></html>",
                        encoding="utf-8")
        out = self.run_mkt("watch", "check", "rivals").stdout
        self.assertIn("Rival", out)
        self.assertIn("price-hike-harvest", out)
        out = self.run_mkt("watch", "events", "rivals").stdout
        self.assertIn("Rival", out)
        out = self.run_mkt("watch", "summary", "rivals").stdout
        self.assertIn("累计事件 1", out)


if __name__ == "__main__":
    unittest.main()
