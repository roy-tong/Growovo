"""转化：阶段、资格、下一个动作、异议、报价、漏斗与 CLI/MCP。"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from core import conversion


ROOT = Path(__file__).resolve().parents[1]
MKT = ROOT / "mkt.py"


class QualificationTests(unittest.TestCase):
    def test_three_of_five_is_qualified(self):
        out = conversion.qualify({"trigger": True, "contactable": True, "data": True})
        self.assertTrue(out["qualified"])
        self.assertEqual(out["score"], 3)

    def test_two_of_five_is_not(self):
        out = conversion.qualify({"trigger": True, "contactable": True})
        self.assertFalse(out["qualified"])
        self.assertEqual(len(out["missing"]), 3)


class NextActionTests(unittest.TestCase):
    def test_each_stage_has_one_action(self):
        for stage in conversion.STAGES:
            out = conversion.next_action(stage, days_since_update=10)
            self.assertTrue(out["action"], stage)
            self.assertIn("why", out)

    def test_contacted_waits_before_day_three(self):
        out = conversion.next_action("contacted", days_since_update=1)
        self.assertEqual(out["action"], "等")
        out = conversion.next_action("contacted", days_since_update=3)
        self.assertIn("跟进", out["action"])

    def test_lost_stops(self):
        self.assertTrue(conversion.next_action("lost")["stop"])


class ObjectionTests(unittest.TestCase):
    def test_known_objection_has_walk_away(self):
        row = conversion.objection("too_expensive")
        self.assertIn("缩范围", row["response"])
        self.assertTrue(row["walk_away"])

    def test_unknown_objection_rejected(self):
        with self.assertRaises(conversion.ConversionError):
            conversion.objection("nope")


class QuoteTests(unittest.TestCase):
    def test_quote_has_scope_price_and_exclusions(self):
        q = conversion.quote({"id": "deal-1", "name": "A", "campaign": "c",
                              "stage": "readback"}, "diagnostic")
        self.assertEqual(q["offer"]["price"], 1500)
        self.assertIn("1 个供应商", q["offer"]["scope"])
        self.assertTrue(q["offer"]["exclusions"])
        md = conversion.render_quote(q)
        self.assertIn("一页 SOW", md)
        self.assertIn("1,500", md)


class DealsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.deals = conversion.Deals(Path(self.tmp.name) / "deals.jsonl")

    def test_add_and_advance(self):
        d = self.deals.add("c1", name="A", campaign="x")
        self.assertEqual(d["stage"], "sourced")
        d = self.deals.advance(d["id"], "contacted", "roy", evidence="comment")
        self.assertEqual(d["stage"], "contacted")
        d = self.deals.advance(d["id"], "engaged", "roy", evidence="reply")
        self.assertEqual(d["stage"], "engaged")

    def test_invalid_transition_rejected(self):
        d = self.deals.add("c1", name="A", campaign="x")
        with self.assertRaises(conversion.ConversionError):
            self.deals.advance(d["id"], "paid", "roy", evidence="x")

    def test_quote_requires_evidence(self):
        d = self.deals.add("c1", name="A", campaign="x")
        self.deals.qualify(d["id"], {"trigger": True, "contactable": True, "data": True})
        for stage, ev in (("contacted", "c"), ("engaged", "r"),
                          ("qualified", "q"), ("sample_offered", "s"),
                          ("sample_received", "d"), ("sample_delivered", "s"),
                          ("readback", "r")):
            d = self.deals.advance(d["id"], stage, "roy", evidence=ev)
        with self.assertRaises(conversion.ConversionError):
            self.deals.advance(d["id"], "quoted", "roy", evidence="")

    def test_funnel_and_stuck(self):
        d = self.deals.add("c1", name="A", campaign="x", on="2026-10-01")
        self.deals.add("c2", name="B", campaign="x", on="2026-10-01")
        self.deals.advance(d["id"], "contacted", "roy", evidence="c", on="2026-10-02")
        f = self.deals.funnel(on="2026-11-04")
        self.assertEqual(f["counts"]["contacted"], 1)
        self.assertEqual(f["counts"]["sourced"], 1)
        self.assertTrue(f["stuck"])

    def test_next_skips_waits(self):
        d = self.deals.add("c1", name="A", campaign="x")
        self.deals.advance(d["id"], "contacted", "roy", evidence="c")
        # 刚触达，未到第 3 天 → next_action 是“等”
        self.assertEqual(conversion.next_action("contacted", 0)["action"], "等")


class DealCliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / "data"
        self.home.mkdir(parents=True)
        self.env = dict(os.environ, MKT_HOME=str(self.home))

    def run_mkt(self, *args):
        proc = subprocess.run([sys.executable, str(MKT), *args], cwd=ROOT,
                              env=self.env, text=True, capture_output=True)
        if proc.returncode:
            self.fail(f"mkt {' '.join(args)} failed\n{proc.stdout}\n{proc.stderr}")
        return proc.stdout

    def test_full_cli_flow(self):
        self.run_mkt("deal", "add", "--contact", "c1", "--name", "A",
                     "--campaign", "x")
        self.run_mkt("deal", "advance", "--id", "deal-0001", "--stage", "contacted",
                     "--by", "roy", "--evidence", "comment")
        out = self.run_mkt("deal", "qualify", "--id", "deal-0001", "--trigger",
                           "--contactable", "--data", "--json")
        payload = json.loads(out)
        self.assertTrue(payload["result"]["qualified"])
        funnel = json.loads(self.run_mkt("deal", "funnel", "--json"))
        self.assertEqual(funnel["counts"]["contacted"], 1)
        for stage in ("engaged", "qualified", "sample_offered", "sample_received",
                      "sample_delivered", "readback"):
            self.run_mkt("deal", "advance", "--id", "deal-0001", "--stage", stage,
                         "--by", "roy", "--evidence", f"evidence-{stage}")
        quote = self.run_mkt("deal", "quote", "--id", "deal-0001",
                             "--offer", "diagnostic")
        self.assertIn("1,500", quote)

    def test_invalid_transition_exits_nonzero(self):
        self.run_mkt("deal", "add", "--contact", "c1", "--name", "A",
                     "--campaign", "x")
        proc = subprocess.run(
            [sys.executable, str(MKT), "deal", "advance", "--id", "deal-0001",
             "--stage", "paid", "--by", "roy", "--evidence", "x"],
            cwd=ROOT, env=self.env, text=True, capture_output=True)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("不能直接到", proc.stderr)


class DealMcpTests(unittest.TestCase):
    def test_readonly_deals(self):
        from mcp_server import _call_tool
        res = _call_tool("mkt_deals", {})
        self.assertFalse(res.get("isError"))
        payload = json.loads(res["content"][0]["text"])
        self.assertIn("funnel", payload)
        res = _call_tool("mkt_deal_next", {})
        self.assertFalse(res.get("isError"))


if __name__ == "__main__":
    unittest.main()
