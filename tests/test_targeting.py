"""信任节点目标库：KOC 评分、阶段机、战术联动与衰减过滤。零网络零账号。"""
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
from core.tactic_run import TacticRun
from core.targeting import TargetList, TargetingError, score_candidate, tier_of


class TmpDir(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)


class ScoreTests(TmpDir):
    def test_koc_window_beats_mega_on_trust_price(self):
        koc = score_candidate({"followers": 8_000, "engagement_rate": 0.035,
                               "sponsored_recent": 0, "niche_match": True})
        mega = score_candidate({"followers": 900_000, "engagement_rate": 0.004,
                                "sponsored_recent": 8, "niche_match": True})
        self.assertGreater(koc["score"], mega["score"] * 2)
        self.assertEqual("koc_window", koc["tier"])
        self.assertEqual("mega_low_trust", mega["tier"])
        self.assertTrue(koc["in_koc_window"])
        self.assertFalse(mega["in_koc_window"])

    def test_sponsored_count_discounts(self):
        fresh = score_candidate({"followers": 20_000, "sponsored_recent": 0})
        burned = score_candidate({"followers": 20_000, "sponsored_recent": 5})
        self.assertGreater(fresh["score"], burned["score"])
        self.assertTrue(any("窗口期" in r for r in fresh["reasons"]))
        self.assertTrue(any("折价" in r for r in burned["reasons"]))

    def test_niche_mismatch_halves(self):
        match = score_candidate({"followers": 10_000, "niche_match": True})
        miss = score_candidate({"followers": 10_000, "niche_match": False})
        self.assertAlmostEqual(match["score"], miss["score"] * 2, delta=2)

    def test_tier_boundaries(self):
        self.assertEqual("tiny", tier_of(500))
        self.assertEqual("koc_window", tier_of(1_000))
        self.assertEqual("trusted_mid", tier_of(50_000))
        self.assertEqual("mega_low_trust", tier_of(500_000))

    def test_invalid_inputs_raise(self):
        with self.assertRaises(TargetingError):
            score_candidate({"followers": 0})
        with self.assertRaises(TargetingError):
            score_candidate({"followers": 100, "engagement_rate": 1.5})
        with self.assertRaises(TargetingError):
            score_candidate({"followers": 100, "sponsored_recent": -1})


class TargetListTests(TmpDir):
    def _list(self, list_id="dream"):
        path = self.dir / "targets" / f"{list_id}.json"
        TargetList.start(path, list_id, niche="AI 独立开发者")
        return TargetList(path)

    def test_add_scores_and_dedupes(self):
        tl = self._list()
        t1 = tl.add("x", "@devtool_koc", 8_000, engagement_rate=0.04,
                    sponsored_recent=0, niche_match=True)
        self.assertEqual("candidate", t1["stage"])
        with self.assertRaises(TargetingError):
            tl.add("x", "devtool_koc", 9_000)     # 同账号去重（@ 前缀归一）

    def test_stage_machine_forward_only(self):
        tl = self._list()
        tl.add("x", "@a", 10_000)
        with self.assertRaises(TargetingError):    # 不能跳级
            tl.advance("a", "engaged", note="manual observation")
        tl.advance("a", "contacted", note="manual observation")
        tl.advance("a", "engaged", note="manual observation")
        with self.assertRaises(TargetingError):    # 不能后退
            tl.advance("a", "contacted", note="manual observation")
        self.assertEqual("engaged", tl.load()["targets"][0]["stage"])

    def test_next_batch_ranks_by_trust_price(self):
        tl = self._list()
        tl.add("x", "@mega", 800_000, niche_match=True)
        tl.add("x", "@koc", 12_000, engagement_rate=0.04, niche_match=True)
        tl.add("x", "@koc2", 15_000, engagement_rate=0.03, niche_match=True)
        rows = tl.next_batch(2)
        self.assertEqual("koc", rows[0]["handle"])
        self.assertNotIn("mega", [r["handle"] for r in rows])

    def test_summary_feeds_koc_verdict(self):
        tl = self._list()
        tl.add("x", "@a", 10_000, niche_match=True)
        tl.add("x", "@b", 20_000, niche_match=True)
        tl.add("x", "@c", 30_000, niche_match=True)
        tl.advance("a", "contacted", on="2026-11-13", note="manual observation")
        tl.advance("a", "engaged", on="2026-11-14", note="manual observation")
        tl.advance("b", "contacted", on="2026-11-14", note="manual observation")
        tl.advance("b", "engaged", on="2026-11-14", note="manual observation")
        tl.advance("b", "amplified", on="2026-11-14", note="manual observation")
        s = tl.summary("2026-11-14")
        self.assertEqual(3, s["total"])
        self.assertEqual(1, s["by_stage"]["engaged"])
        self.assertEqual(1, s["by_stage"]["amplified"])
        self.assertEqual(2, s["engaged_last_7d"])   # a + b 在近 7 天进入 engaged
        self.assertEqual(1, s["amplified_total"])
        text = tl.render_summary("2026-11-14")
        self.assertIn("KOC 甜区 3", text)
        self.assertIn("近 7 天互动 2", text)


class TacticLinkTests(TmpDir):
    def test_run_summary_includes_target_progress(self):
        from core.execution_policy import authorize
        authorize(self.dir, by="测试", actions=["x:reply", "x:message"], days=30)
        tpath = self.dir / "tactics" / "k1.json"
        TacticRun.start(tpath, "k1", "koc-first", started="2026-11-01",
                        target_list="dream")
        tl_path = self.dir / "targets" / "dream.json"
        TargetList.start(tl_path, "dream")
        tl = TargetList(tl_path)
        tl.add("x", "@koc_a", 10_000, niche_match=True)
        tl.advance("koc_a", "contacted", on="2026-11-14", note="manual observation")
        tl.advance("koc_a", "engaged", on="2026-11-14", note="manual observation")
        run = TacticRun(tpath)
        s = run.summary("2026-11-14")
        self.assertIn("targets", s)
        self.assertEqual(1, s["targets"]["engaged_last_7d"])
        v = run.verdict("2026-11-14")
        self.assertEqual(1, v["koc_engaged_last_7d"])

    def test_suggest_excludes_stale_by_default(self):
        fresh = tactics_reg.suggest(mechanism="trust_nodes", today="2026-11-14")
        self.assertTrue(fresh)
        later = tactics_reg.suggest(mechanism="trust_nodes", today="2027-06-01")
        self.assertEqual([], later)                        # 全部过复核期
        with_stale = tactics_reg.suggest(mechanism="trust_nodes",
                                         include_stale=True, today="2027-06-01")
        self.assertEqual(len(fresh), len(with_stale))      # 放开后等量


class TargetCliTests(unittest.TestCase):
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

    def test_target_chain_and_stale_list(self):
        self.run_mkt("target", "start", "dream", "--niche", "AI 独立开发者")
        out = self.run_mkt("target", "score", "--followers", "900000",
                           "--engagement-rate", "0.004").stdout
        self.assertIn("mega_low_trust", out)

        self.run_mkt("target", "add", "dream", "--platform", "x",
                     "--handle", "koc_dev", "--followers", "12000",
                     "--engagement-rate", "0.04", "--sponsored-recent", "0",
                     "--niche-match", "--on", "2026-11-13")
        # 同账号去重
        proc = self.run_mkt("target", "add", "dream", "--platform", "x",
                            "--handle", "koc_dev", "--followers", "13000", check=False)
        self.assertEqual(2, proc.returncode)

        out = self.run_mkt("target", "next", "dream").stdout
        self.assertIn("koc_dev", out)
        self.run_mkt("target", "advance", "dream", "--handle", "koc_dev",
                     "--stage", "contacted", "--note", "sent receipt", "--on", "2026-11-14")
        self.run_mkt("target", "advance", "dream", "--handle", "koc_dev",
                     "--stage", "engaged", "--note", "reply receipt", "--on", "2026-11-14")
        out = self.run_mkt("target", "summary", "dream", "--on", "2026-11-14").stdout
        self.assertIn("近 7 天互动 1", out)

        # 战术执行记录关联清单
        self.run_mkt("tactic", "start", "k1", "--tactic", "koc-first",
                     "--on", "2026-11-01", "--target-list", "dream")
        out = self.run_mkt("tactic", "show-run", "k1", "--on", "2026-11-14").stdout
        self.assertIn("目标清单 dream", out)
        self.assertIn("累计再传播 0", out)

        # 衰减过滤：证据过期默认不进建议
        out = self.run_mkt("tactic", "list", "--mechanism", "trust_nodes").stdout
        self.assertIn("koc-first", out)
        out = self.run_mkt("tactic", "list", "--mechanism", "trust_nodes",
                           "--include-stale").stdout
        self.assertIn("koc-first", out)


if __name__ == "__main__":
    unittest.main()
