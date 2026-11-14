"""账号/帖子契合度：分词、评分、聚合、排序；纯离线。"""
import tempfile
import unittest
from pathlib import Path

from core import grow_fit as fit

FACTS = {
    "product": {"name": "Verovo",
                "what": "AI 账期分摊与发票对账，交付可签署的关账包"},
    "icp": [{"hypothesis": "中型公司 FinOps 负责人月底要分摊 AI 支出并对上发票",
             "tags": ["b2b-buyers"]}],
    "goals": [{"name": "付费客户"}],
    "value_events": [{"name": "免费样本交付"}],
}


def _post(text, **over):
    row = {"text": text, "age_hours": 2, "engagement": {"replies": 2, "likes": 10}}
    row.update(over)
    return row


class TermTests(unittest.TestCase):
    def test_terms_drop_stopwords_and_keep_domain_words(self):
        words = fit.terms("How do you reconcile the AI invoice? 账期分摊怎么做")
        self.assertIn("reconcile", words)
        self.assertIn("invoice", words)
        self.assertIn("账期分摊", words)
        self.assertNotIn("how", words)

    def test_product_profile(self):
        profile = fit.product_profile(FACTS)
        self.assertIn("发票对账", profile["keywords"])
        self.assertIn("账期分摊", profile["keywords"])
        self.assertIn("finops", profile["audience"])
        self.assertEqual("Verovo", profile["name"])


class PostScoreTests(unittest.TestCase):
    def test_relevant_post_beats_irrelevant(self):
        profile = fit.product_profile(FACTS)
        good = fit.score_post(_post(
            "Our AI invoice doesn't match usage. How do you reconcile cost per team?"),
            profile)
        bad = fit.score_post(_post("Beautiful sunset today, love this city"), profile)
        self.assertGreater(good["score"], bad["score"])
        self.assertEqual("A", good["tier"])
        self.assertTrue(good["reasons"])

    def test_promo_and_old_and_huge_account_reduce(self):
        profile = fit.product_profile(FACTS)
        base = _post("How do you reconcile AI invoice cost per team?")
        plain = fit.score_post(base, profile)
        promo = fit.score_post(dict(base, text=base["text"] + " DM me for free trial"),
                               profile)
        old = fit.score_post(dict(base, age_hours=200), profile)
        huge = fit.score_post(base, profile, account={"followers": 900_000})
        self.assertLess(promo["score"], plain["score"])
        self.assertLess(old["score"], plain["score"])
        self.assertLess(huge["score"], plain["score"])

    def test_small_account_is_discouraged_but_not_excluded(self):
        profile = fit.product_profile(FACTS)
        row = fit.score_post(_post("How do you reconcile AI invoice cost per team?"),
                             profile, account={"followers": 30})
        self.assertLessEqual(row["score"], 100)
        self.assertTrue(any("粉丝很少" in risk for risk in row["risks"]))


class KocTests(unittest.TestCase):
    def test_account_tier_boundaries(self):
        self.assertEqual("nano", fit.account_tier(50))
        self.assertEqual("micro", fit.account_tier(500))
        self.assertEqual("koc", fit.account_tier(5_000))
        self.assertEqual("koc", fit.account_tier(50_000))
        self.assertEqual("mid", fit.account_tier(80_000))
        self.assertEqual("macro", fit.account_tier(500_000))
        self.assertEqual("unknown", fit.account_tier(None))

    def test_koc_beats_macro_and_promo_is_penalized(self):
        profile = fit.product_profile(FACTS)
        koc = fit.koc_score({"followers": 8_000,
                             "bio": "FinOps lead, invoice reconciliation"},
                            profile)
        macro = fit.koc_score({"followers": 800_000,
                               "bio": "FinOps lead, invoice reconciliation"},
                              profile)
        promo = fit.koc_score({"followers": 8_000,
                               "bio": "FinOps | link in bio | DM me"},
                              profile)
        self.assertEqual("koc", koc["tier"])
        self.assertTrue(koc["is_koc"])
        self.assertGreater(koc["score"], macro["score"])
        self.assertLess(promo["score"], koc["score"])

    def test_koc_only_filter_excludes_unknown_followers(self):
        profile = fit.product_profile(FACTS)
        rows = [{"handle": "a", "text": "AI invoice reconciliation",
                 "url": "https://x.com/a/status/1", "age_hours": 1,
                 "engagement": {"replies": 1, "likes": 3}},
                {"handle": "b", "text": "AI invoice reconciliation",
                 "url": "https://x.com/b/status/2", "age_hours": 1,
                 "engagement": {"replies": 1, "likes": 3}}]
        accounts = {"a": {"followers": 5_000, "bio": "FinOps"},
                    "b": {"followers": 800_000, "bio": "FinOps"}}
        all_rows = fit.rank_prospects(rows, profile, accounts)
        koc_rows = fit.rank_prospects(rows, profile, accounts, koc_only=True)
        self.assertEqual(2, len(all_rows))
        self.assertEqual(["a"], [r["handle"] for r in koc_rows])
        self.assertTrue(koc_rows[0]["is_koc"])


class AutoQueryAndWatchlistTests(unittest.TestCase):
    def test_auto_queries_prefers_domain_phrases(self):
        profile = fit.product_profile(FACTS)
        queries = fit.auto_queries(profile)
        self.assertTrue(queries)
        self.assertTrue(any("invoice" in q for q in queries))
        self.assertFalse(any("verovo" in q for q in queries))

    def test_preselect_handles_by_best_post(self):
        profile = fit.product_profile(FACTS)
        rows = [
            {"handle": "good", "text": "How do you reconcile AI invoice cost?",
             "url": "https://x.com/good/status/1", "age_hours": 1,
             "engagement": {"replies": 1, "likes": 5}},
            {"handle": "bad", "text": "nice sunset", "url": "https://x.com/bad/status/2",
             "age_hours": 1, "engagement": {"replies": 0, "likes": 0}},
        ]
        handles = fit.preselect_handles(rows, profile, limit=1)
        self.assertEqual(["good"], handles)

    def test_rank_excludes_engaged_handles(self):
        profile = fit.product_profile(FACTS)
        rows = [{"handle": "a", "text": "invoice reconciliation",
                 "url": "https://x.com/a/status/1", "age_hours": 1,
                 "engagement": {"replies": 1, "likes": 2}}]
        ranked = fit.rank_prospects(rows, profile, {"a": {"followers": 5000}},
                                    exclude_handles=["A"])
        self.assertEqual([], ranked)

    def test_mark_engaged_increments_touches(self):
        with tempfile.TemporaryDirectory() as tmp:
            fit.mark_engaged(tmp, "g1", "@alice", on="2026-11-14",
                             meta={"followers": 5000})
            first = fit.load_watchlist(tmp, "g1")["alice"]
            self.assertEqual(1, first["touches"])
            fit.mark_engaged(tmp, "g1", "alice", on="2026-11-15")
            second = fit.load_watchlist(tmp, "g1")["alice"]
            self.assertEqual(2, second["touches"])
            self.assertEqual("2026-11-15", second["last_engaged_at"])

    def test_watchlist_update_and_render(self):
        with tempfile.TemporaryDirectory() as tmp:
            rows = [{"handle": "alice", "tier": "A", "score": 80,
                     "koc_tier": "koc", "is_koc": True,
                     "account": {"followers": 5000, "bio": "FinOps"},
                     "best_post": {"url": "https://x.com/alice/status/1"}}]
            path = fit.update_watchlist(tmp, "g1", rows, on="2026-11-14",
                                        engaged=["alice"])
            self.assertTrue(Path(path).is_file())
            accounts = fit.load_watchlist(tmp, "g1")
            self.assertIn("alice", accounts)
            self.assertEqual("2026-11-14", accounts["alice"]["last_engaged_at"])
            text = fit.render_watchlist(accounts)
            self.assertIn("@alice", text)
            self.assertIn("已触达", text)


class AccountRankTests(unittest.TestCase):
    def test_group_and_rank(self):
        profile = fit.product_profile(FACTS)
        rows = [
            {"handle": "alice", "text": "How do you reconcile AI invoice cost?",
             "url": "https://x.com/alice/status/1", "age_hours": 1,
             "engagement": {"replies": 2, "likes": 10}},
            {"handle": "alice", "text": "AI spend allocation is painful",
             "url": "https://x.com/alice/status/2", "age_hours": 3,
             "engagement": {"replies": 1, "likes": 4}},
            {"handle": "bob", "text": "lovely weather",
             "url": "https://x.com/bob/status/3", "age_hours": 1,
             "engagement": {"replies": 0, "likes": 1}},
        ]
        grouped = fit.group_by_handle(rows)
        self.assertEqual(2, len(grouped["alice"]))
        ranked = fit.rank_prospects(rows, profile,
                                    {"alice": {"followers": 800}})
        self.assertEqual("alice", ranked[0]["handle"])
        self.assertGreater(ranked[0]["score"], ranked[1]["score"])
        self.assertEqual(2, ranked[0]["post_count"])

    def test_parse_account_text(self):
        parsed = fit.parse_account_text(
            "Roy\n@roy\nBuilding AI invoice reconciliation for FinOps teams\n"
            "194 正在关注 | 33 关注者\n")
        self.assertEqual(33, parsed["followers"])
        self.assertIn("invoice", parsed["bio"].lower())

    def test_save_prospects_and_render(self):
        with tempfile.TemporaryDirectory() as tmp:
            rows = [{"handle": "alice", "score": 80, "tier": "A",
                     "reasons": ["命中关键词"], "risks": [], "post_count": 1,
                     "account": {"bio": "FinOps", "followers": 800},
                     "best_post": {"text": "how to reconcile", "url": "https://x/1"}}]
            path = fit.save_prospects(tmp, "g1", rows, on="2026-11-14")
            self.assertTrue(Path(path).is_file())
            text = fit.render(rows)
            self.assertIn("@alice", text)
            self.assertIn("80 分", text)
            self.assertIn("没有候选账号", fit.render([]))


if __name__ == "__main__":
    unittest.main()

class ProspectMixRegressionTests(unittest.TestCase):
    def test_share_changes_actual_shortlist(self):
        rows = ([{"handle": f"k{i}", "is_koc": True} for i in range(10)] +
                [{"handle": f"m{i}", "is_koc": False} for i in range(10)])
        for share, expected in [(0.3, 3), (0.7, 7)]:
            result = fit.select_prospect_mix(rows, limit=10, koc_share=share)
            self.assertEqual(10, len(result))
            self.assertEqual(expected, sum(r["is_koc"] for r in result))
        self.assertTrue(all(r['is_koc'] for r in fit.select_prospect_mix(rows, koc_only=True)))

    def test_scarce_group_backfills_without_duplicates(self):
        rows = [{"handle": "k", "is_koc": True}] + [
            {"handle": f"m{i}", "is_koc": False} for i in range(5)]
        result = fit.select_prospect_mix(rows, limit=5, koc_share=0.8)
        self.assertEqual(5, len(result))
        self.assertEqual(5, len({r['handle'] for r in result}))

class VerticalFitRegressions(unittest.TestCase):
    def test_followers_alone_do_not_make_relevant_koc(self):
        profile=fit.product_profile(FACTS)
        self.assertFalse(fit.koc_score({'followers':18000,'bio':'Country over corporations',
            'recent_posts':['Congress budget reconciliation debate','Senate legislative agenda']},profile)['is_koc'])
        self.assertFalse(fit.koc_score({'followers':1641,'bio':'Web3 Ambassador',
            'recent_posts':['Download a torrent','Binance promotional giveaway']},profile)['is_koc'])

    def test_ai_does_not_match_inside_other_words(self):
        self.assertEqual([],fit._keyword_hits('daily paid campaign',{'ai'}))
