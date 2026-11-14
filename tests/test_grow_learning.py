"""学习回路：指标解析、采集、按公式归因、样本门槛与提案；纯离线。"""
import json
import tempfile
import unittest
from pathlib import Path

from core import grow_learning as gl


class ParseTests(unittest.TestCase):
    def test_parse_labels_and_views(self):
        metrics = gl.parse_post_metrics(
            ["12 回复。回复", "3 次转帖。转帖", "45 喜欢次数。喜欢"],
            "Someone · 1,234 次查看")
        self.assertEqual({"views": 1234, "likes": 45, "replies": 12,
                          "reposts": 3}, metrics)

    def test_missing_is_none_not_zero(self):
        metrics = gl.parse_post_metrics([], "")
        self.assertIsNone(metrics["views"])
        self.assertIsNone(metrics["likes"])


class CollectTests(unittest.TestCase):
    def test_collect_dedupes_and_appends(self):
        with tempfile.TemporaryDirectory() as tmp:
            rows = [{"target": "https://x.com/a/status/1"},
                    {"target": "https://x.com/a/status/2"},
                    {"target": "https://x.com/a/status/1"}]
            first = gl.collect(tmp, "g1", rows=rows, reader=lambda url: {
                "views": 100, "likes": 5, "replies": 2})
            self.assertEqual(2, first["written"])
            second = gl.collect(tmp, "g1", rows=rows, reader=lambda url: {})
            self.assertEqual(0, second["written"])
            self.assertEqual(2, len(gl.load_metrics(tmp, "g1")))


class AnalyzeTests(unittest.TestCase):
    def _data_dir(self, tmp):
        run_dir = Path(tmp) / "grow" / "g1"
        run_dir.mkdir(parents=True)
        rows = []
        for i in range(3):
            rows.append({"url": f"https://x.com/a/status/a{i}",
                         "views": 100, "likes": 10, "replies": 10})
        for i in range(3):
            rows.append({"url": f"https://x.com/a/status/b{i}",
                         "views": 100, "likes": 1, "replies": 1})
        (run_dir / "post_metrics.jsonl").write_text(
            "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
        index = {}
        for i in range(3):
            index[f"https://x.com/a/status/a{i}"] = {
                "formula": "reply.agree_extend_ask", "window": "20:00-23:00@us-west"}
        for i in range(3):
            index[f"https://x.com/a/status/b{i}"] = {
                "formula": "reply.contrast_boundary", "window": "08:00-10:00@us-west"}
        return tmp, {"content_index": index}

    def test_analyze_with_enough_sample_proposes_boost(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir, run = self._data_dir(tmp)
            result = gl.analyze(data_dir, "g1", run)
            self.assertEqual(6, result["sample"])
            self.assertTrue(result["changes"])
            change = result["changes"][0]
            self.assertEqual("content.mix_weights", change["param"])
            self.assertEqual("reply.agree_extend_ask", change["to"]["boost"])
            self.assertIn("rollback", change)
            buckets = result["buckets"]["formula"]
            self.assertEqual(3, buckets["reply.agree_extend_ask"]["n"])

    def test_koc_bucket_proposes_share_increase(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "grow" / "g1"
            run_dir.mkdir(parents=True)
            rows, index = [], {}
            for i in range(3):
                rows.append({"url": f"https://x.com/k/status/{i}",
                             "views": 100, "likes": 10, "replies": 10})
                index[f"https://x.com/k/status/{i}"] = {
                    "formula": "reply.koc_peer", "author_tier": "koc"}
            for i in range(3):
                rows.append({"url": f"https://x.com/m/status/{i}",
                             "views": 100, "likes": 1, "replies": 1})
                index[f"https://x.com/m/status/{i}"] = {
                    "formula": "reply.koc_peer", "author_tier": "macro"}
            (run_dir / "post_metrics.jsonl").write_text(
                "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
            result = gl.analyze(tmp, "g1", {"content_index": index})
            koc_changes = [c for c in result["changes"]
                           if c["param"] == "target.koc_share"]
            self.assertTrue(koc_changes)
            self.assertEqual(0.7, koc_changes[0]["to"]["koc_share"])

    def test_sample_below_threshold_does_not_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir, run = self._data_dir(tmp)
            rows = gl.load_metrics(data_dir, "g1")[:2]
            path = Path(tmp) / "grow" / "g1" / "post_metrics.jsonl"
            path.write_text("\n".join(json.dumps(r) for r in rows) + "\n",
                            encoding="utf-8")
            result = gl.analyze(data_dir, "g1", run)
            self.assertEqual([], result["changes"])

    def test_no_metrics(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = gl.analyze(tmp, "g1", {})
            self.assertEqual(0, result["sample"])
            self.assertIn("先", result["recommendation"])

    def test_write_latest_and_render(self):
        with tempfile.TemporaryDirectory() as tmp:
            payload = {"sample": 6, "recommendation": "继续观察",
                       "changes": [], "buckets": {"formula": {}}}
            path = gl.write_learning(tmp, "g1", payload, on="2026-11-14")
            self.assertTrue(Path(path).is_file())
            latest = gl.latest_proposal(tmp, "g1")
            self.assertEqual("继续观察", latest["recommendation"])
            text = gl.render(payload)
            self.assertIn("内容学习", text)
            self.assertIn("边界", text)


if __name__ == "__main__":
    unittest.main()

class FeedbackRegressionTests(unittest.TestCase):
    def test_refresh_daily_and_retry_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            rows = [{"target": "tweet:https://x.com/parent/status/1",
                     "published_url": "https://x.com/me/status/2"}]
            bad = lambda url: {"note": "unavailable"}
            good = lambda url: {"views": 100, "replies": 2}
            gl.collect(tmp, "g", rows=rows, on="2026-11-14", reader=bad)
            self.assertEqual(1, gl.collect(tmp, "g", rows=rows, on="2026-11-14", reader=good)["written"])
            self.assertEqual(0, gl.collect(tmp, "g", rows=rows, on="2026-11-14", reader=good)["written"])
            self.assertEqual(1, gl.collect(tmp, "g", rows=rows, on="2026-11-15", reader=good)["written"])
            self.assertEqual(1, len(gl.latest_metrics(gl.load_metrics(tmp, "g"))))

    def test_duplicate_snapshots_never_become_three_posts(self):
        with tempfile.TemporaryDirectory() as tmp:
            gl.append_metrics(tmp, "g", [{"target": "https://x.com/a/status/1", "views": 100,
                                          "replies": 10}] * 4)
            result = gl.analyze(tmp, "g", {"content_index": {
                "tweet:https://x.com/a/status/1": {"formula": "reply.koc_peer"}}})
            self.assertEqual(1, result["sample"])
            self.assertIn("reply.koc_peer", result["buckets"]["formula"])
            self.assertEqual([], result["changes"])

    def test_missing_replies_do_not_satisfy_sample_threshold(self):
        with tempfile.TemporaryDirectory() as tmp:
            rows = [{"url": str(i), "views": 100, "replies": 10 if i == 0 else None}
                    for i in range(3)]
            gl.append_metrics(tmp, "g", rows)
            result = gl.analyze(tmp, "g", {"content_index": {
                str(i): {"formula": "reply.koc_peer"} for i in range(3)}})
            self.assertEqual(1, result["buckets"]["formula"]["reply.koc_peer"]["rate_n"])
            self.assertEqual([], result["changes"])

class OwnPostReaderTests(unittest.TestCase):
    def test_only_requested_article_contributes_metrics(self):
        from unittest.mock import Mock, patch
        parent, own = Mock(), Mock()
        parent.locator.return_value.evaluate_all.return_value = ['/parent/status/1']
        own.locator.return_value.evaluate_all.side_effect = [['/me/status/2'], ['3 replies', '5 likes']]
        own.inner_text.return_value = '100 views'
        browser, page = Mock(), Mock()
        page.locator.return_value.all.return_value = [parent, own]
        with patch('core.cdp.connect', return_value=(browser, page)), patch('core.cdp.safe_goto', return_value=True):
            result = gl.read_post_metrics('https://x.com/me/status/2')
        self.assertEqual(100, result['views'])
        self.assertEqual(3, result['replies'])
        parent.inner_text.assert_not_called()
        page.inner_text.assert_not_called()
        browser.close.assert_called_once()
