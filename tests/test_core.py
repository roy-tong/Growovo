#!/usr/bin/env python3
"""core 离线单测 —— 不碰网络、不碰浏览器、不碰真实 data/。

跑法: python3 -m unittest discover tests -v   （在仓库根目录）
覆盖：账本幂等/不重试名单、门禁五关、节流上限/日预算、写路径事务管道
（含 449 事故的核心回归：回读不一致绝不 SEND）。
"""
import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.evidence import Evidence
from core.gates import (Gate, GateError, check_content, check_target,
                        load_contact_suppressions, load_suppress, load_suppressions)
from core.ledger import Ledger, LedgerCorruptError
from core.pipeline import WritePipeline, standard_pipeline
from core import throttle
from core.throttle import Throttle, ThrottleError


class TmpDir(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)


class TestLedger(TmpDir):
    def test_append_and_query(self):
        led = Ledger(self.dir / "ledger.jsonl")
        led.append(channel="email", action="message", target="a@b.c",
                   result="committed", campaign="w1")
        led.append(channel="email", action="message", target="a@b.c",
                   result="failed", campaign="w1")
        self.assertEqual(len(led.all()), 2)
        self.assertEqual(len(led.results(channel="email", campaign="w1")), 2)
        self.assertEqual(len(led.results(channel="x")), 0)

    def test_already_idempotency(self):
        led = Ledger(self.dir / "ledger.jsonl")
        led.append(channel="email", action="message", target="a@b.c",
                   result="committed", campaign="w1")
        self.assertTrue(led.already(channel="email", target="a@b.c", campaign="w1"))
        self.assertFalse(led.already(channel="email", target="a@b.c", campaign="w2"))
        # dry-run 永远不算已发送
        led.append(channel="email", action="message", target="x@y.z",
                   result="dry-run", campaign="w1")
        self.assertFalse(led.already(channel="email", target="x@y.z", campaign="w1"))

    def test_no_retry_set(self):
        led = Ledger(self.dir / "ledger.jsonl")
        led.append(channel="x", action="message", target="@someone", result="no-dm")
        led.append(channel="email", action="message", target="a@b.c", result="bounced")
        led.append(channel="x", action="message", target="@ok", result="committed")
        self.assertEqual(led.no_retry("x"), {"@someone"})
        self.assertEqual(led.no_retry("email"), {"a@b.c"})

    def test_corrupt_line_fails_loud(self):
        """G-3（review 2026-11-04）：坏行静默跳过=把已发送当未发送，改为显式报错。"""
        p = self.dir / "ledger.jsonl"
        p.write_text("not-json\n" + json.dumps(
            {"channel": "x", "action": "search", "target": "q", "result": "dry-run"}
        ) + "\n", encoding="utf-8")
        with self.assertRaises(LedgerCorruptError):
            Ledger(p).all()

    def test_summary(self):
        led = Ledger(self.dir / "ledger.jsonl")
        led.append(channel="x", action="publish", target="tweet:1",
                   result="committed", campaign="c1")
        led.append(channel="x", action="publish", target="tweet:2",
                   result="committed", campaign="c1")
        s = led.summary()
        self.assertEqual(s["total"], 2)
        self.assertEqual(s["breakdown"][0]["count"], 2)


class TestGates(TmpDir):
    def test_content_rules(self):
        ok = "Hi Sam, loved your MCP billing post. One question: how do you meter retries?"
        check_content(ok)  # 不抛
        with self.assertRaises(GateError):
            check_content("bad \u2014 dash")          # em dash
        with self.assertRaises(GateError):
            check_content("hi {{name}}")              # 占位符
        with self.assertRaises(GateError):
            check_content("")                          # 空正文
        with self.assertRaises(GateError):
            check_content("x" * 300, max_len=280)     # 超长

    def test_target_formats(self):
        self.assertEqual(check_target("email", "a@b.co"), "a@b.co")
        with self.assertRaises(GateError):
            check_target("email", "not-an-email")
        with self.assertRaises(GateError):
            check_target("x", "someone")               # 缺 @
        with self.assertRaises(GateError):
            check_target("linkedin", "https://vk.com/x")

    def test_gate_suppress_and_dedupe(self):
        led = Ledger(self.dir / "ledger.jsonl")
        led.append(channel="email", action="message", target="a@b.c",
                   result="committed", campaign="w1")
        gate = Gate(led, suppress={"bad@x.io"})
        gate.check(channel="email", action="message", target="c@d.e",
                   campaign="w2", content="hello there friend")
        with self.assertRaises(GateError):   # 抑制名单
            gate.check(channel="email", action="message", target="bad@x.io", campaign="w9")
        with self.assertRaises(GateError):   # 同 campaign 防重发
            gate.check(channel="email", action="message", target="a@b.c",
                       campaign="w1", content="hello")
        # 不同 campaign 可再发（换波次跟进是正常运营）

    def test_gate_no_retry(self):
        led = Ledger(self.dir / "ledger.jsonl")
        led.append(channel="x", action="message", target="@gone", result="no-dm")
        gate = Gate(led)
        with self.assertRaises(GateError):
            gate.check(channel="x", action="message", target="@gone", campaign="w9")

    def test_load_suppress(self):
        p = self.dir / "suppress.txt"
        p.write_text("# comment\n\nbad@x.io\n  spaced@y.io  \n", encoding="utf-8")
        self.assertEqual(load_suppress(p), {"bad@x.io", "spaced@y.io"})
        self.assertEqual(load_suppress(self.dir / "nope.txt"), set())

    def test_contacts_dnc_is_a_real_suppression_source(self):
        (self.dir / "contacts.jsonl").write_text(json.dumps({
            "id": "c1", "platforms": {"email": "dnc@example.com", "x": "@blocked"},
            "state": {"dnc": True},
        }) + "\n" + json.dumps({
            "id": "c2", "platforms": {"email": "ok@example.com"},
            "state": {"dnc": False},
        }) + "\n", encoding="utf-8")
        (self.dir / "suppress.txt").write_text("file@example.com\n", encoding="utf-8")
        self.assertEqual(load_contact_suppressions(self.dir), {"dnc@example.com", "@blocked"})
        self.assertEqual(load_suppressions(self.dir),
                         {"dnc@example.com", "@blocked", "file@example.com"})
        gate = Gate(suppress=load_suppressions(self.dir))
        with self.assertRaises(GateError):
            gate.check(channel="email", action="message", target="dnc@example.com",
                       campaign="c1", content="hello")
        gate.check(channel="email", action="message", target="ok@example.com",
                   campaign="c1", content="hello")

    def test_indeterminate_blocks_auto_retry_until_resolved(self):
        led = Ledger(self.dir / "ledger.jsonl")
        led.append(channel="email", action="message", target="a@b.c",
                   result="indeterminate", campaign="w1", ref="r1")
        gate = Gate(led)
        with self.assertRaises(GateError):
            gate.check(channel="email", action="message", target="a@b.c",
                       campaign="w1", content="hello")
        led.resolve("r1", result="failed", by="roy", note="后台确认未发送")
        gate.check(channel="email", action="message", target="a@b.c",
                   campaign="w1", content="hello")

    def test_hn_submit_target_is_valid_for_publish_pipeline(self):
        self.assertEqual(check_target("hn", "hn:submit:https://example.com/p"),
                         "hn:submit:https://example.com/p")


class TestThrottle(TmpDir):
    def test_intervals_in_range(self):
        th = Throttle(self.dir)
        for _ in range(50):
            w = th.interval("x", "publish")
            self.assertTrue(45 <= w <= 90)
        self.assertEqual(th.interval("x", "publish", override=7), 7)
        # 未知渠道动作走保守缺省
        lo, hi, _, _ = th.policy("sms", "message")
        self.assertEqual((lo, hi), (60, 120))

    def test_run_cap(self):
        th = Throttle(self.dir)
        with self.assertRaises(ThrottleError):
            th.check_run("x", "engage", 11)   # follow/like 单次 ≤10
        th.check_run("x", "engage", 10)       # 边界不抛

    def test_daily_budget(self):
        th = Throttle(self.dir)
        for _ in range(3):
            th.daily_charge("reddit", "publish")
        self.assertEqual(th.daily_used("reddit", "publish"), 3)
        self.assertFalse(th.daily_allows("reddit", "publish", 1))   # 日 ≤3 帖
        self.assertTrue(th.daily_allows("x", "search", 999))         # 只读无上限

    def test_slice_batches_keeps_business_running(self):
        th = Throttle(self.dir)
        self.assertEqual(th.slice_batches("email", "message", 120),
                         [range(0, 50), range(50, 100), range(100, 120)])
        self.assertEqual(th.slice_batches("x", "search", 999), [range(0, 999)])
        self.assertEqual(th.slice_batches("hn", "publish", 1), [range(0, 1)])

    def test_advice_gives_split_and_reset_hint(self):
        th = Throttle(self.dir)
        th.daily_charge("email", "message", 20)
        hint = th.advice("email", "message", 120)
        self.assertIn("拆成 3 批", hint)
        self.assertIn("今日剩余 80 条", hint)
        self.assertIn("UTC 0 点", hint)
        self.assertIn("风控", hint)
        self.assertIn("45-90s", th.advice("x", "publish", 3))


class TestBudgetDayBoundary(TmpDir):
    """夜间窗口 19:00→次日 07:00 跨本地 0 点：预算日界不能把一晚切成两段。"""

    def _at(self, *args):
        when = datetime(*args, tzinfo=timezone.utc)
        return mock.patch.object(throttle, "_now", return_value=when)

    def test_night_window_stays_in_one_budget_day(self):
        th = Throttle(self.dir)
        with self._at(2026, 11, 22, 15, 30):        # 北京 23:30
            th.daily_charge("x", "publish")
            self.assertEqual(th.daily_used("x", "publish"), 1)
        with self._at(2026, 11, 22, 16, 30):        # 北京次日 00:30
            th.daily_charge("x", "publish")
            self.assertEqual(th.daily_used("x", "publish"), 2)

    def test_local_boundary_still_splits_at_midnight(self):
        th = Throttle(self.dir, day_boundary="local")
        with self._at(2026, 11, 22, 15, 30):
            th.daily_charge("x", "publish")
        with self._at(2026, 11, 22, 16, 30):
            self.assertEqual(th.daily_used("x", "publish"), 0)

    def test_old_local_day_file_is_not_counted(self):
        th = Throttle(self.dir)
        now = datetime(2026, 11, 22, 11, 0, tzinfo=timezone.utc)
        local_day = now.astimezone().date().isoformat()
        (self.dir / f"mkt-daily-{local_day}.json").write_text(
            json.dumps({"x/publish": 48}), encoding="utf-8")
        with mock.patch.object(throttle, "_now", return_value=now):
            self.assertEqual(th.daily_used("x", "publish"), 0)

    def test_cap_resets_after_boundary(self):
        th = Throttle(self.dir)
        with self._at(2026, 11, 22, 15, 30):
            for _ in range(48):
                th.reserve("x", "publish")
            with self.assertRaises(ThrottleError):
                th.reserve("x", "publish")
        with self._at(2026, 11, 23, 1, 0):              # 北京次日 09:00，已过 UTC 0 点
            self.assertEqual(th.daily_used("x", "publish"), 0)
            self.assertEqual(th.reserve("x", "publish"), 1)


def make_pipeline(tmpdir, readback_text=None):
    led = Ledger(tmpdir / "ledger.jsonl")
    gate = Gate(led, suppress={"bad@x.io"})
    th = Throttle(tmpdir / "state")
    pipe = WritePipeline(gate=gate, ledger=led, throttle=th)
    return pipe, led, th


def run_fake(pipe, *, expect="hello world", readback=None, dry_run=False,
             target="a@b.c", content=None, channel="email", action="message",
             campaign="w1", send_raises=False, confirm_value=True):
    calls = []

    def cb(name):
        def f(item, *a, **k):
            calls.append(name)
            if name == "readback":
                return readback if readback is not None else expect
            if name == "send" and send_raises:
                raise RuntimeError("smtp 拒绝")
            if name == "confirm":
                return confirm_value
        return f

    rec = pipe.run_one(
        {"id": "t1"}, channel=channel, action=action, campaign=campaign,
        transport="smtp", expect=content if content is not None else expect,
        target=target, compose=cb("compose"), fill=cb("fill"),
        readback=cb("readback"), send=cb("send"), confirm=cb("confirm"),
        shot=cb("shot"), dry_run=dry_run)
    return rec, calls


class TestPipeline(TmpDir):
    def test_happy_path_committed(self):
        pipe, led, th = make_pipeline(self.dir)
        rec, calls = run_fake(pipe)
        self.assertEqual(rec["result"], "committed")
        self.assertEqual(calls, ["compose", "fill", "readback", "shot", "send", "confirm"])
        self.assertEqual(th.daily_used("email", "message"), 1)  # 计入日预算
        self.assertTrue(led.already(channel="email", target="a@b.c", campaign="w1"))

    def test_dry_run_stops_before_send(self):
        pipe, led, th = make_pipeline(self.dir)
        rec, calls = run_fake(pipe, dry_run=True)
        self.assertEqual(rec["result"], "dry-run")
        self.assertEqual(calls, ["compose", "fill", "readback", "shot"])  # 无 send
        self.assertFalse(led.already(channel="email", target="a@b.c", campaign="w1"))
        self.assertEqual(th.daily_used("email", "message"), 0)

    def test_readback_mismatch_never_sends(self):
        """449 空正文事故回归：回读不一致 → FAILED，绝不进入 SEND。"""
        pipe, led, _ = make_pipeline(self.dir)
        rec, calls = run_fake(pipe, expect="full message", readback="")  # 读回为空
        self.assertEqual(rec["result"], "failed")
        self.assertIn("回读不一致", rec["detail"])
        self.assertNotIn("send", calls)
        self.assertNotIn("shot", calls)

    def test_send_failure_recorded_not_committed(self):
        pipe, led, _ = make_pipeline(self.dir)
        rec, calls = run_fake(pipe, send_raises=True)
        self.assertEqual(rec["result"], "indeterminate")
        self.assertIn("send", calls)
        self.assertFalse(led.already(channel="email", target="a@b.c", campaign="w1"))

    def test_unconfirmed_is_indeterminate(self):
        pipe, led, _ = make_pipeline(self.dir)
        rec, _ = run_fake(pipe, confirm_value=False)
        self.assertEqual(rec["result"], "indeterminate")
        self.assertFalse(led.already(channel="email", target="a@b.c", campaign="w1"))

    def test_gate_rejection_is_normal_record(self):
        pipe, led, _ = make_pipeline(self.dir)
        rec, calls = run_fake(pipe, target="bad@x.io")   # 在抑制名单
        self.assertEqual(rec["result"], "rejected")
        self.assertEqual(calls, [])                       # 一个回调都没碰
        # 换 campaign 也不给已发目标重发
        rec2, _ = run_fake(pipe, campaign="w2", content="x \u2014 y")  # 禁字符
        self.assertEqual(rec2["result"], "rejected")

    def test_whitespace_normalized_in_readback(self):
        pipe, _, _ = make_pipeline(self.dir)
        rec, _ = run_fake(pipe, expect="line one\n\nline two", readback="line one\n\n\nline two")
        self.assertEqual(rec["result"], "committed")   # 段落符归一化后等价

    def test_exhausted_daily_budget_blocks_before_send(self):
        pipe, _, th = make_pipeline(self.dir)
        for _ in range(100):
            th.daily_charge("email", "message")
        rec, calls = run_fake(pipe)
        self.assertEqual(rec["result"], "rejected")
        self.assertIn("日预算门禁", rec["detail"])
        self.assertNotIn("send", calls)
        self.assertEqual(th.daily_used("email", "message"), 100)

    def test_reserve_is_atomic_and_never_exceeds_cap(self):
        th = Throttle(self.dir / "state")
        th.reserve("reddit", "publish", 3)
        with self.assertRaises(ThrottleError):
            th.reserve("reddit", "publish", 1)
        self.assertEqual(th.daily_used("reddit", "publish"), 3)


class TestPipelineApproval(TmpDir):
    """实跑人审门禁进管道（此前只有 email 接线且查错目录，其余渠道全绕过）。"""

    def _plan(self, approved):
        plans = self.dir / "plans"
        plans.mkdir(parents=True, exist_ok=True)
        (plans / "p1.json").write_text(json.dumps({
            "plan_id": "p1", "goal": "验证渠道", "created": "2026-10-31",
            "hypothesis": "目标用户会回复", "icp": {"beachhead": "开发者"},
            "audience": {"spec": "测试目标"}, "waves": [{
                "wave": 1, "channel": "email", "action": "message", "size": 1,
                "content_policy": "千人千面", "queue": "data/queues/w1.json"}],
            "success_criteria": {"metric": "reply_rate", "target": ">=5%", "stop": "退信>5%"},
            "review": {"human_approved": approved},
        }, ensure_ascii=False), encoding="utf-8")

    def test_real_run_rejects_unapproved_plan_before_any_callback(self):
        self._plan(False)
        led = Ledger(self.dir / "ledger.jsonl")
        pipe = standard_pipeline(self.dir, led)
        rec, calls = run_fake(pipe, campaign="w1")     # 实跑（dry_run=False）
        self.assertEqual(rec["result"], "rejected")
        self.assertIn("人审门禁", rec["detail"])
        self.assertEqual(calls, [])                     # 审批在 gate 之前，回调全未触碰

    def test_real_run_rejects_no_plan_by_default_adhoc_passes_with_flag(self):
        led = Ledger(self.dir / "ledger.jsonl")
        pipe = standard_pipeline(self.dir, led)         # 无 plans/ 目录
        rec, calls = run_fake(pipe, campaign="w1")
        self.assertEqual(rec["result"], "rejected")
        self.assertIn("人审门禁", rec["detail"])
        self.assertEqual(calls, [])
        pipe2 = standard_pipeline(self.dir, led, adhoc=True)
        rec2, _ = run_fake(pipe2, campaign="w1")
        self.assertEqual(rec2["result"], "committed")
        self.assertEqual(rec2.get("approval"), "adhoc")
        # 账本留痕：detail 带 adhoc=true
        rows = led.results(campaign="w1")
        self.assertTrue(any(r.get("result") == "committed" and "adhoc=true" in (r.get("detail") or "")
                            for r in rows))

    def test_approved_plan_real_run_committed_with_plan_mode(self):
        self._plan(True)
        led = Ledger(self.dir / "ledger.jsonl")
        pipe = standard_pipeline(self.dir, led)
        rec, _ = run_fake(pipe, campaign="w1")
        self.assertEqual(rec["result"], "committed")
        self.assertEqual(rec.get("approval"), "plan")

    def test_invalid_legacy_plan_cannot_bypass_with_boolean(self):
        plans = self.dir / "plans"
        plans.mkdir(parents=True, exist_ok=True)
        (plans / "bad.json").write_text(json.dumps({
            "plan_id": "p1", "waves": [{"wave": 1, "queue": "data/queues/w1.json"}],
            "review": {"human_approved": True},
        }), encoding="utf-8")
        led = Ledger(self.dir / "ledger.jsonl")
        rec, calls = run_fake(standard_pipeline(self.dir, led), campaign="w1")
        self.assertEqual(rec["result"], "rejected")
        self.assertIn("计划校验未过", rec["detail"])
        self.assertEqual(calls, [])

    def test_dry_run_not_gated_by_approval(self):
        """dry-run 无外部效果，不要求人审——审批只约束实跑。"""
        led = Ledger(self.dir / "ledger.jsonl")
        pipe = standard_pipeline(self.dir, led)
        rec, _ = run_fake(pipe, campaign="w1", dry_run=True)
        self.assertEqual(rec["result"], "dry-run")

    def test_pause_blocks_real_and_dry_runs_via_standard_pipeline(self):
        led = Ledger(self.dir / "ledger.jsonl")
        (self.dir / "state").mkdir(parents=True, exist_ok=True)
        (self.dir / "state" / "PAUSED").write_text("test\n", encoding="utf-8")
        pipe = standard_pipeline(self.dir, led)
        rec, calls = run_fake(pipe, campaign="w1")
        self.assertEqual(rec["result"], "rejected")
        self.assertIn("全局暂停", rec["detail"])
        self.assertEqual(calls, [])


class TestEvidence(TmpDir):
    def test_shot_and_json(self):
        ev = Evidence(self.dir / "shots" / "c1")
        p = ev.shot_path("a.png")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"png")
        self.assertTrue(p.exists())
        out = ev.write_json("run", {"ok": True})
        self.assertTrue(out.exists())
        self.assertEqual(json.loads(out.read_text())["ok"], True)


class TestHNChannel(TmpDir):
    """HN 渠道离线单测：提交物校验/目标格式/稀缺预算/注册表口径（不碰网络与浏览器）。"""

    def test_submission_validation(self):
        from channels.hn import _validate_submission
        _validate_submission("Show HN: Thing - demo", url="https://example.com")
        _validate_submission("Ask HN: how do you x", text="body text")
        with self.assertRaises(ValueError):   # url 与 text 必须二选一
            _validate_submission("t", url="https://a.b", text="x")
        with self.assertRaises(ValueError):   # 两者皆空
            _validate_submission("t")
        with self.assertRaises(ValueError):   # 标题为空
            _validate_submission("", url="https://a.b")
        with self.assertRaises(ValueError):   # 标题超 80（HN 指引）
            _validate_submission("x" * 81, url="https://a.b")
        with self.assertRaises(ValueError):   # url 不合法
            _validate_submission("t", url="example.com")

    def test_target_format(self):
        self.assertEqual(check_target("hn", "item:12345"), "item:12345")
        self.assertTrue(check_target(
            "hn", "https://news.ycombinator.com/item?id=123").startswith("https://"))
        with self.assertRaises(GateError):
            check_target("hn", "@handle")

    def test_publish_is_scarce(self):
        th = Throttle(self.dir)
        th.check_run("hn", "publish", 1)                      # 单次 1 条可跑
        with self.assertRaises(ThrottleError):
            th.check_run("hn", "publish", 2)                  # 单次上限 1：批量提交拒跑
        th.daily_charge("hn", "publish")
        self.assertFalse(th.daily_allows("hn", "publish", 1))  # 日 ≤1：当天第二帖拒跑

    def test_registry_entry(self):
        from channels import REGISTRY
        info = REGISTRY["hn"]
        self.assertEqual(info["status"], "UNVERIFIED")
        self.assertEqual(info["transports"], {"cdp", "api"})
        self.assertTrue(info["actions"] <= {"publish", "reply", "message",
                                            "engage", "search", "inbox"})
        self.assertNotIn("engage", info["actions"])           # 投票操纵红线：不实现
        self.assertNotIn("message", info["actions"])          # HN 无 DM


if __name__ == "__main__":
    unittest.main()


class TestSafeGoto(unittest.TestCase):
    """导航重试纪律：只重试可恢复中断，超时不再赔一遍等待。"""

    class _Page:
        def __init__(self, failures):
            self.failures = list(failures)
            self.calls = []

        def goto(self, url, **kwargs):
            self.calls.append((url, kwargs))
            if self.failures:
                raise RuntimeError(self.failures.pop(0))
            return None

    def test_timeout_is_not_retried(self):
        from core import cdp
        page = self._Page(["Timeout 20000ms exceeded."])
        with mock.patch.object(cdp.time, "sleep") as sleep:
            self.assertFalse(cdp.safe_goto(page, "https://x.com/home"))
            sleep.assert_not_called()
        self.assertEqual(len(page.calls), 1)
        self.assertEqual(page.calls[0][1]["timeout"], cdp.GOTO_TIMEOUT_MS)

    def test_aborted_navigation_is_retried(self):
        from core import cdp
        page = self._Page(["net::ERR_ABORTED", "net::ERR_ABORTED"])
        with mock.patch.object(cdp.time, "sleep"):
            self.assertTrue(cdp.safe_goto(page, "https://x.com/home"))
        self.assertEqual(len(page.calls), 3)

    def test_explicit_timeout_overrides_default(self):
        from core import cdp
        page = self._Page([])
        self.assertTrue(cdp.safe_goto(page, "https://x.com/home", timeout_ms=1500))
        self.assertEqual(page.calls[0][1]["timeout"], 1500)
