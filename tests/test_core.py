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
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.evidence import Evidence
from core.gates import Gate, GateError, check_content, check_target, load_suppress
from core.ledger import Ledger
from core.pipeline import WritePipeline
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

    def test_corrupt_line_tolerated(self):
        p = self.dir / "ledger.jsonl"
        p.write_text("not-json\n" + json.dumps(
            {"channel": "x", "action": "search", "target": "q", "result": "dry-run"}
        ) + "\n", encoding="utf-8")
        self.assertEqual(len(Ledger(p).all()), 1)

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
        self.assertEqual(rec["result"], "failed")
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
