"""非常规增长战术：注册表不变量 + 执行记录生命周期 + CLI 契约。

三种写法对齐仓库既有测试：
- 注册表不变量（仿 test_channels）
- 临时目录直调 core 对象（仿 test_core 的 TmpDir）
- CLI 子进程端到端（仿 test_cli_e2e，MKT_HOME 隔离，零真实发送）
"""
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
from core.gates import GateError
from core.queue import lint_queue
from core.tactic_run import TacticRuleError, TacticRun, check_candidate


def _facts() -> dict:
    return {
        "product": {"name": "X", "what": "test", "url": "https://example.invalid",
                    "stage": "launched", "product_type": "devtool"},
    }


class RegistryTests(unittest.TestCase):
    def test_lint_passes(self):
        self.assertEqual([], tactics_reg.lint())

    def test_ids_unique_and_fields_complete(self):
        ids = list(tactics_reg.TACTICS)
        self.assertEqual(len(ids), len(set(ids)))
        for tid, t in tactics_reg.TACTICS.items():
            self.assertIn(t["mechanism"], tactics_reg.MECHANISMS, tid)
            self.assertIn(t["risk"], tactics_reg.RISK_ORDER, tid)
            self.assertIn(t["status"], ("usable", "proposed"), tid)
            self.assertGreater(len(t["steps"]), 0, tid)
            self.assertGreater(len(t["evidence"]), 0, tid)

    def test_x_reply_guy_is_usable_and_replayable(self):
        spec = tactics_reg.spec("x-reply-guy")
        q = spec["quota"]
        self.assertEqual("usable", spec["status"])
        self.assertEqual("reply", spec["queue_action"])
        self.assertLessEqual(q["daily_min"], q["daily_cap"])
        self.assertEqual(80, q["min_sample"])
        self.assertGreater(len(spec["formulas"]), 4)

    def test_spec_unknown_returns_none(self):
        self.assertIsNone(tactics_reg.spec("no-such-tactic"))

    def test_suggest_filters(self):
        only_x = tactics_reg.suggest(channel="x")
        self.assertTrue(only_x)
        self.assertTrue(all(t["channel"] == "x" for t in only_x))
        white_only = tactics_reg.suggest(risk_max="white")
        self.assertTrue(all(t["risk"] == "white" for t in white_only))
        with self.assertRaises(ValueError):
            tactics_reg.suggest(risk_max="black")

    def test_stale_flags_expired_evidence(self):
        # 用战术自己的 evidence_date + revalidate_days 构造过期日与未过期日
        tid = next(iter(tactics_reg.TACTICS))
        t = tactics_reg.TACTICS[tid]
        from datetime import date, timedelta
        fresh = (date.fromisoformat(t["evidence_date"])
                 + timedelta(days=t["revalidate_days"] - 1)).isoformat()
        old = (date.fromisoformat(t["evidence_date"])
               + timedelta(days=t["revalidate_days"] + 1)).isoformat()
        self.assertEqual([], [x for x in tactics_reg.stale(fresh) if x["id"] == tid])
        self.assertIn(tid, [x["id"] for x in tactics_reg.stale(old)])

    def test_forbidden_has_reasons(self):
        self.assertGreaterEqual(len(tactics_reg.FORBIDDEN), 5)
        for f in tactics_reg.FORBIDDEN:
            self.assertTrue(f["why"], f["id"])
            self.assertTrue(f["evidence"], f["id"])

    def test_render_index_lists_every_tactic(self):
        text = tactics_reg.render_index()
        for tid in tactics_reg.TACTICS:
            self.assertIn(f"`{tid}`", text)
        self.assertIn("已证伪", text)


class TmpDir(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)


class TacticRunTests(TmpDir):
    def _run(self, run_id="r1", tactic="x-reply-guy", started="2026-11-01"):
        path = self.dir / "tactics" / f"{run_id}.json"
        TacticRun.start(path, run_id, tactic, started=started)
        return TacticRun(path)

    def test_start_unknown_tactic_fails_loud(self):
        with self.assertRaises(ValueError):
            TacticRun.start(self.dir / "tactics" / "x.json", "x", "no-such")

    def test_start_snapshots_quota(self):
        run = self._run()
        snap = run.load()["tactic"]
        self.assertEqual(snap["id"], "x-reply-guy")
        self.assertEqual(snap["quota"]["daily_cap"], 20)
        # 改注册表不影响已开的 run
        original = tactics_reg.TACTICS["x-reply-guy"]["quota"]["daily_cap"]
        try:
            tactics_reg.TACTICS["x-reply-guy"]["quota"]["daily_cap"] = 99
            self.assertEqual(run.load()["tactic"]["quota"]["daily_cap"], 20)
        finally:
            tactics_reg.TACTICS["x-reply-guy"]["quota"]["daily_cap"] = original

    def test_draft_link_is_rejected(self):
        run = self._run()
        with self.assertRaises(GateError):
            run.add_draft("tweet:https://x.com/a/1", "详见 https://example.com/x")

    def test_draft_placeholder_and_dash_rejected_by_shared_gates(self):
        run = self._run()
        with self.assertRaises(GateError):  # 占位符残留
            run.add_draft("tweet:https://x.com/a/1", "你好 {name} 这里有用")
        with self.assertRaises(GateError):  # em dash 是既有禁字符
            run.add_draft("tweet:https://x.com/a/1", "这个数据很关键\u2014\u2014补一个数字")

    def test_draft_duplicate_body_rejected(self):
        run = self._run()
        body = "我们实测 300 个小账号，84% 靠回帖涨到一万粉。"
        run.add_draft("tweet:https://x.com/a/1", body)
        with self.assertRaises(TacticRuleError):
            run.add_draft("tweet:https://x.com/a/2", body + "  ")

    def test_per_target_daily_cap(self):
        run = self._run()
        tgt = "tweet:https://x.com/a/1"
        run.add_draft(tgt, "第一条：补一个具体数字，回复独立成立值得点开主页")
        run.add_draft(tgt, "第二条：换个角度说经验，这条也独立成立")
        with self.assertRaises(TacticRuleError):
            run.add_draft(tgt, "第三条：同目标当天刷屏会被屏蔽")

    def test_daily_cap_and_formula_warn(self):
        run = self._run()
        for i in range(20):
            run.add_draft(f"tweet:https://x.com/a/{i}",
                          f"第 {i} 条：每条按帖子定制，有数字有细节，独立成立。",
                          formula="数据型" if i < 5 else "经验型", hour=9)
        with self.assertRaises(TacticRuleError):  # 第 21 条超日上限 20
            run.add_draft("tweet:https://x.com/a/overflow", "超上限的一条")
        data = run.load()
        # 数据型用了 5 次 > same_formula_max_streak 4 → 告警在案
        self.assertTrue(any("数据型" in v["detail"] for v in data["violations"]))

    def test_late_reply_records_warning_not_rejection(self):
        run = self._run()
        run.add_draft("tweet:https://x.com/a/1", "补一个实测数字，前排位置才有意义。",
                      post_age_min=45)
        self.assertTrue(any("回帖超窗" in v["detail"] for v in run.load()["violations"]))

    def test_check_candidate_pure_function(self):
        spec = tactics_reg.spec("x-reply-guy")
        warns = check_candidate(spec, target="tweet:https://x.com/a/1",
                                body="可行的做法：先回帖攒信任，再发原创做转化。",
                                formula="洞见型", post_age_min=3)
        self.assertEqual([], warns)
        with self.assertRaises(TacticRuleError):
            check_candidate(spec, target="tweet:https://x.com/a/1", body="可行做法",
                            formula="不存在的公式")

    def test_queue_export_lints_clean_and_no_double_export(self):
        run = self._run()
        run.add_draft("tweet:https://x.com/a/1", "第一条候选：内容独立成立，不带链接。")
        run.add_draft("tweet:https://x.com/a/2", "第二条候选：换个公式，还是不带链接。")
        queue = run.as_queue_items("t1")
        self.assertEqual([], lint_queue(queue))
        self.assertEqual(2, len(queue["items"]))
        self.assertEqual("x", queue["channel"])
        with self.assertRaises(TacticRuleError):  # 已导出，不能重复导
            run.as_queue_items("t1")
        self.assertEqual("t1", run.load()["campaign"])

    def test_log_over_cap_is_recorded_not_hidden(self):
        run = self._run()
        items = [{"target": f"tweet:https://x.com/b/{i}", "body": "",
                  "formula": "", "hour": None} for i in range(25)]
        run.log(sent=25, items=items, on="2026-11-02")
        self.assertTrue(any("超限" in v["detail"] for v in run.load()["violations"]))

    def test_summary_and_verdict_lifecycle(self):
        run = self._run()
        for day in range(4):
            run.log(sent=20, replied=3, moved=1, on=f"2026-11-0{day + 2}",
                    metrics={"profile_visits": 200 + day, "new_followers": 30 + day}
                    if day == 3 else None)
        s = run.summary("2026-11-28")
        self.assertEqual(80, s["sent"])
        self.assertEqual(4, s["week"])
        self.assertEqual(0.0, s["deleted_rate"])
        v = run.verdict("2026-11-28")
        self.assertEqual("观察到互动", v["verdict"])
        self.assertEqual({"on": "2026-11-05", "profile_visits": 203, "new_followers": 33},
                         v["last_metrics"])

    def test_verdict_insufficient_sample(self):
        run = self._run()
        run.log(sent=10, replied=5, on="2026-11-02")
        self.assertEqual("样本不足", run.verdict("2026-11-28")["verdict"])

    def test_verdict_safety_anomaly(self):
        run = self._run()
        for i in range(4):
            run.log(sent=20, deleted=6, replied=1, on=f"2026-11-0{i + 2}")
        self.assertEqual("安全异常", run.verdict("2026-11-28")["verdict"])

    def test_sync_from_ledger_counts_committed_only(self):
        from core.ledger import Ledger
        run = self._run()
        run.add_draft("tweet:https://x.com/a/1", "候选一条：内容独立成立。")
        run.as_queue_items("t1")
        ledger = Ledger(self.dir / "ledger.jsonl")
        ledger.append(channel="x", action="reply", target="tweet:https://x.com/a/1",
                      result="committed", campaign="t1")
        ledger.append(channel="x", action="reply", target="tweet:https://x.com/a/9",
                      result="dry-run", campaign="t1")
        ledger.append(channel="x", action="reply", target="tweet:https://x.com/a/8",
                      result="committed", campaign="other")
        summary = run.sync_from_ledger(self.dir)
        self.assertEqual(1, summary["committed_total"])

    def test_sync_requires_campaign(self):
        run = self._run()
        with self.assertRaises(TacticRuleError):
            run.sync_from_ledger(self.dir)


class TacticCliTests(unittest.TestCase):
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

    def test_list_show_start_log_verdict_chain(self):
        out = self.run_mkt("tactic", "list")
        self.assertIn("x-reply-guy", out.stdout)
        self.assertIn("借别人的受众", out.stdout)

        out = self.run_mkt("tactic", "show", "x-reply-guy")
        self.assertIn("15", out.stdout)  # daily_min

        self.run_mkt("tactic", "start", "r1", "--tactic", "x-reply-guy", "--on", "2026-11-01")
        run_file = self.home / "tactics" / "r1.json"
        self.assertTrue(run_file.exists())

        # 带链接文本必须被拒（exit 2），且拒绝原因写明禁链
        proc = self.run_mkt("tactic", "check", "--tactic", "x-reply-guy",
                            "--target", "tweet:https://x.com/a/1",
                            "--text", "详见 https://example.com/x", check=False)
        self.assertEqual(2, proc.returncode)
        self.assertIn("禁放外链", proc.stdout + proc.stderr)

        self.run_mkt("tactic", "log", "r1", "--sent", "20", "--replied", "3",
                     "--moved", "1", "--on", "2026-11-02")
        out = self.run_mkt("tactic", "show-run", "r1", "--on", "2026-11-28")
        self.assertIn("触达 20", out.stdout)
        out = self.run_mkt("tactic", "verdict", "r1", "--on", "2026-11-28")
        self.assertIn("样本不足", out.stdout)

    def test_stale_and_forbidden_visible(self):
        out = self.run_mkt("tactic", "stale", "--on", "2027-06-01")
        self.assertIn("x-reply-guy", out.stdout)  # 60 天复核期早已过
        out = self.run_mkt("tactic", "forbidden")
        self.assertIn("买粉买量", out.stdout)


if __name__ == "__main__":
    unittest.main()
