#!/usr/bin/env python3
"""单次运行详情页（G-P0-01～16）验收：只读投影 + 自包含 HTML。

全部在临时目录构造 fixture，不碰真实 data/，不发送、不审批、不裁决。
"""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from core import run_detail

NOW = "2026-11-19T10:00:00"
ON = "2026-11-19"


def _run_payload(run_id="demo"):
    return {
        "schema": "growovo.grow-run/2",
        "id": run_id,
        "status": "ready_to_arm",
        "go": False,
        "owner": "owner@example.com",
        "started": "2026-11-19",
        "channels": ["x"],
        "goal": {
            "id": "awareness.followers",
            "family": "awareness",
            "name": "粉丝增长",
            "metric": "followers",
            "metric_label": "粉丝",
            "target": 100,
            "target_kind": "absolute",
            "target_text": "30 天 +100 粉丝",
            "window_days": 30,
            "starts": "2026-11-19",
            "ends": "2026-12-19",
            "baseline": 0,
            "proves": "账号粉丝净增",
            "does_not_prove": "不证明付费转化",
            "leading": ["主页访问"],
            "counter": ["取关"],
        },
        "strategy": {
            "primary": {"channel": "x", "platform": "x",
                        "actions": ["reply"], "why": "目标读者在 X",
                        "tier": "auto", "residency_days": 7},
            "support": [],
            "not_doing": ["不买粉"],
            "tactics": [{"id": "reply-to-thread", "why": "先给价值"}],
            "compiled_at": "2026-11-19",
            "fingerprint": "strategy-fp",
        },
        "quota": {"actions_per_week": 3, "initial_actions_per_week": 3,
                  "max_per_run": 1, "interval_seconds": 60,
                  "daily_preview": 1, "action_weights": {"reply": 1}},
        "plan": {"actions_per_week": 3, "initial_actions_per_week": 3},
        "measurement": {"readers": [], "business_campaign": run_id,
                        "verification": "UNVERIFIED",
                        "calibration": {"due": "2026-11-26",
                                        "checks": [], "streak": 0}},
        "supply": {"briefs": []},
        "supply_state": {"staged": 0, "upcoming": 0, "coverage_days": 0,
                         "gap": 0, "daily_quota": 1, "horizon_days": 1,
                         "by_day": {}, "note": "暂无供给"},
        "content": {"mix": {}, "formula_weights": {}, "layers": {}},
        "content_index": {},
        "learning": {"sample": 0, "buckets": {}, "changes": [],
                     "recommendation": "继续采集：有效样本不足",
                     "min_sample": 3, "on": "2026-11-19",
                     "collection": {"pending": 0, "written": 0}},
        "history": [{"at": "2026-11-19T09:00:00", "kind": "launch",
                     "ran": 0, "published": 0}],
        "heartbeat": {"last_tick": "2026-11-19T09:00:00+00:00",
                      "interval_minutes": 30, "ok": True},
    }


def _digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class RunDetailPageTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.data = Path(self._tmp.name)
        (self.data / "grow").mkdir()
        policies = self.data / "policies"
        policies.mkdir()
        (policies / "routine-marketing.json").write_text(json.dumps({
            "allow_actions": ["x:reply"],
            "allow_accounts": [],
            "expires_at": "2026-12-31T00:00:00",
        }, ensure_ascii=False), encoding="utf-8")

    def tearDown(self):
        self._tmp.cleanup()

    def write_run(self, payload=None, run_id="demo"):
        path = self.data / "grow" / f"{run_id}.json"
        path.write_text(json.dumps(payload or _run_payload(run_id),
                                   ensure_ascii=False, indent=1),
                        encoding="utf-8")
        return path

    def build(self, run_id="demo"):
        return run_detail.build_run_detail(
            self.data, run_id, on=ON, now=NOW)

    def assert_self_contained(self, html):
        self.assertTrue(html.startswith("<!DOCTYPE html>"))
        for needle in ("<script", "<link", "<img", "<iframe",
                       '<a href="http', "src=\"http"):
            self.assertEqual(0, html.count(needle), f"页面含外部依赖 {needle}")

    # --- 空态 -------------------------------------------------------------
    def test_missing_run_renders_empty_without_pass_or_zero(self):
        detail = self.build("nope")
        self.assertEqual("empty", detail["state"])
        self.assertEqual(["EMPTY"], [s["code"] for s in detail["states"]])
        html = run_detail.render_html(detail)
        self.assert_self_contained(html)
        self.assertIn("运行文件不存在", html)
        self.assertNotIn(">PASS<", html)
        self.assertNotIn(">无风险<", html)
        # 页面唯一允许出现"0 次发送"的地方是明确否定它的那句提示
        self.assertIn("不是 0 次发送成功的结论", html)
        self.assertEqual(
            1, html.count("0 次发送"),
            "0 次发送只能出现在否定提示里，不能被当成读数渲染")

    # --- 待开闸 -----------------------------------------------------------
    def test_ready_to_arm_needs_human_and_never_claims_sent(self):
        self.write_run()
        detail = self.build()
        self.assertEqual("NEEDS_INPUT", detail["object"]["ux_state"])
        self.assertEqual("否（需人工开闸）",
                         run_detail._effective_go(detail))
        html = run_detail.render_html(detail)
        self.assertIn("需人工开闸", html)
        self.assertIn("人工确认授权范围与配额后开闸", html)
        self.assertNotIn("已发送", html.replace('不是"已发送"', ""))
        self.assertNotIn("已发布", html)
        self.assert_self_contained(html)

    # --- 内容与授权 -------------------------------------------------------
    def test_unverified_measurement_is_shown_as_unverified(self):
        self.write_run()
        html = run_detail.render_html(self.build())
        self.assertIn("UNVERIFIED", html)
        self.assertIn("UNVERIFIED", run_detail.render_summary(self.build()))

    def test_fingerprint_mismatch_blocks_old_approval(self):
        self.write_run()
        plans = self.data / "plans"
        plans.mkdir()
        (plans / "demo-w1.json").write_text(json.dumps({
            "plan_id": "demo-w1",
            "campaign": "demo-20261119",
            "review": {"human_approved": True,
                       "approved_by": "roy",
                       "approved_at": "2026-11-18T10:00:00",
                       "plan_sha256": "deadbeef"},
            "waves": [{"campaign": "demo-20261119", "items": []}],
        }, ensure_ascii=False), encoding="utf-8")
        detail = self.build()
        auth = detail["authorization"]
        self.assertTrue(auth["fingerprint_mismatch"])
        self.assertFalse(auth["human_approved"])
        self.assertIn("执行指纹不一致", json.dumps(auth, ensure_ascii=False))
        html = run_detail.render_html(detail)
        self.assertIn("执行指纹不一致", html)
        self.assertIn("PARTIAL", [s["code"] for s in detail["states"]])

    # --- 时间线 / 未知 -----------------------------------------------------
    def write_ledger(self, extra_lines=()):
        rows = [{
            "ts": "2026-11-19T09:10:00",
            "campaign": "demo-20261119",
            "channel": "x",
            "transport": "cdp",
            "action": "reply",
            "target": "tweet:https://x.com/a/status/1",
            "result": "indeterminate",
            "evidence": "",
            "ref": "x:demo-20261119:reply:aaaa::tweet:https://x.com/a/status/1",
            "detail": "实跑但无 vendor 结果记录",
        }]
        rows.extend(extra_lines)
        path = self.data / "ledger.jsonl"
        path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n"
                                for r in rows), encoding="utf-8")
        return path

    def test_indeterminate_is_pinned_and_never_retryable(self):
        self.write_run()
        self.write_ledger()
        detail = self.build()
        self.assertEqual("INDETERMINATE_EXTERNAL_ACTION",
                         detail["object"]["blocked_reason_code"])
        self.assertEqual("BLOCKED", detail["object"]["ux_state"])
        self.assertIn("需要先处理的不确定回执",
                      run_detail.render_html(detail))
        html = run_detail.render_html(detail)
        self.assertIn("禁止自动重试", html)
        self.assertIn("--after-reconciliation", html)
        self.assertGreaterEqual(
            html.count("禁止自动重试"), 2)
        self.assertIn("INDETERMINATE_EXTERNAL_ACTION",
                      run_detail.render_summary(detail))

    def test_resolved_indeterminate_shows_operator_not_retry_banner(self):
        self.write_run()
        self.write_ledger()
        (self.data / "ledger_resolutions.jsonl").write_text(
            json.dumps({"ref": "x:demo-20261119:reply:aaaa::"
                               "tweet:https://x.com/a/status/1",
                        "result": "committed", "by": "roy",
                        "note": "平台后台确认已发出",
                        "at": "2026-11-19T09:30:00"},
                       ensure_ascii=False) + "\n", encoding="utf-8")
        detail = self.build()
        row = detail["timeline"]["rows"][0]
        self.assertEqual("roy", row["resolved_by"])
        self.assertEqual("committed", row["result"])
        self.assertFalse(row["no_retry"])
        html = run_detail.render_html(detail)
        self.assertIn("人工裁决", html)
        self.assertIn("roy", html)

    def test_l3_unknown_is_not_zero(self):
        self.write_run()
        detail = self.build()
        self.assertEqual("UNKNOWN",
                         (detail["layers"].get("L3") or {}).get("state"))
        html = run_detail.render_html(detail)
        self.assertIn("这是未知，不是 0", html)
        self.assertIn("PARTIAL", [s["code"] for s in detail["states"]])

    # --- 目标退出与旅程 ---------------------------------------------------
    def test_journey_empty_without_contracts(self):
        self.write_run()
        detail = self.build()
        self.assertEqual("empty", detail["journey"]["state"])
        html = run_detail.render_html(detail)
        self.assertIn('id="journey"', html)
        self.assertIn("还没有目标契约", html)
        self.assertIn("目标退出与旅程", run_detail.render_summary(detail))
        self.assert_self_contained(html)

    def test_journey_projection_and_stop_are_shown(self):
        from core import goal_exit
        self.write_run()
        contract = goal_exit.build_contract(
            run_id="demo",
            goal_event={"event_type": "signup", "target": 1,
                        "metric": "signup"},
            observation_window={"start": "2026-11-01",
                                "end": "2026-12-01"},
            exit_events=[{"kind": "manual_stop",
                          "source_ref": "note:2026-11-18",
                          "subject_ref": "person@example.com"}],
            created_by="roy", now=NOW)
        goal_exit.write_contract(self.data, contract, dry_run=False)
        projection = goal_exit.evaluate(
            self.data, contract, subject_ref="person@example.com",
            now="2026-11-19T10:05:00")
        goal_exit.write_projection(self.data, projection, dry_run=False)
        goal_exit.write_stop(self.data, projection, dry_run=False)

        detail = self.build()
        journey = detail["journey"]
        self.assertEqual("ok", journey["state"])
        self.assertEqual(1, len(journey["contracts"]))
        self.assertEqual(1, len(journey["projections"]))
        self.assertEqual(1, journey["stop_count"])
        html = run_detail.render_html(detail)
        self.assertIn(contract["contract_id"], html)
        self.assertIn("person@example.com", html)
        self.assertIn("停止记录", html)
        self.assertIn("不解除 DNC", html)
        self.assertIn("契约 1 个", run_detail.render_summary(detail))

    def test_journey_unreadable_is_warned_not_zeroed(self):
        self.write_run()
        store = self.data / "outcomes" / "journey"
        store.mkdir(parents=True)
        (store / "journey-projections.jsonl").write_text(
            "{not json}\n", encoding="utf-8")
        detail = self.build()
        self.assertEqual("unreadable", detail["journey"]["state"])
        self.assertTrue(detail["journey"]["error"])
        html = run_detail.render_html(detail)
        self.assertIn("旅程存储读取失败", html)
        self.assertIn("不能把没有观察写成失败", html)

    # --- 信号与变更预演 ---------------------------------------------------
    def test_signals_empty_without_envelopes(self):
        self.write_run()
        detail = self.build()
        self.assertEqual("empty", detail["signals"]["state"])
        html = run_detail.render_html(detail)
        self.assertIn('id="signals"', html)
        self.assertIn("还没有信号", html)
        self.assertIn("信号与变更预演", run_detail.render_summary(detail))
        self.assert_self_contained(html)

    def test_signals_and_preview_remove_are_shown(self):
        from core import signal_envelope as se
        self.write_run()
        plans = self.data / "plans"
        plans.mkdir()
        (plans / "p1.json").write_text(json.dumps({
            "plan_id": "p1", "goal": "回访", "created": "2026-11-18",
            "hypothesis": "来源上下文提升回复率",
            "icp": {"beachhead": "独立开发者"},
            "audience": {"spec": "近 30 天安装 demo 的账号"},
            "waves": [{"wave": 1, "channel": "email", "action": "message",
                       "size": 1, "content_policy": "按来源改写",
                       "queue": "queues/q.json"}],
            "success_criteria": {"metric": "reply_rate", "target": 0.1,
                                 "stop": "投诉即停"},
            "review": {"human_approved": False},
        }, ensure_ascii=False), encoding="utf-8")
        (self.data / "queues").mkdir()
        (self.data / "queues" / "q.json").write_text(
            json.dumps({"items": [{"ref": "acct-a"}]}), encoding="utf-8")
        envelope = se.build_signal(
            source_family="reply", source_ref="https://x.example/1",
            observed_at="2026-11-18T00:00:00Z",
            raw_text="请按原始上下文回复。", run_id="demo", now=NOW)
        se.write_signal(self.data, envelope, dry_run=False)
        preview = se.build_preview(
            self.data, plan_id="p1",
            changes=["add:content:lp-1:新增段落",
                     "remove:queue:acct-a:移出队列"],
            run_id="demo", now=NOW)
        se.write_preview(self.data, preview, dry_run=False)

        detail = self.build()
        self.assertEqual("ok", detail["signals"]["state"])
        self.assertEqual(1, detail["signals"]["preview_count"])
        html = run_detail.render_html(detail)
        self.assertIn(envelope["signal_id"], html)
        self.assertIn(preview["preview_id"], html)
        self.assertIn("acct-a", html)
        self.assertIn("删除项（不折叠）", html)
        summary = run_detail.render_summary(detail)
        self.assertIn("预演 1 个", summary)
        self.assertIn("删除项 1 个", summary)

    def test_signals_unreadable_is_warned_not_zeroed(self):
        self.write_run()
        store = self.data / "signals"
        store.mkdir()
        (store / "signal-envelopes.jsonl").write_text(
            "{not json}\n", encoding="utf-8")
        detail = self.build()
        self.assertEqual("unreadable", detail["signals"]["state"])
        self.assertTrue(detail["signals"]["error"])
        html = run_detail.render_html(detail)
        self.assertIn("信号存储读取失败", html)
        self.assertIn("不补 0", html)

    # --- 自包含与只读 -----------------------------------------------------
    def test_html_is_offline_and_escapes_external_text(self):
        payload = _run_payload()
        payload["goal"]["name"] = "<img src=x onerror=alert(1)>"
        payload["strategy"]["primary"]["why"] = "<script>alert(2)</script>"
        self.write_run(payload)
        html = run_detail.render_html(self.build())
        self.assert_self_contained(html)
        self.assertNotIn("<img src=x", html)
        self.assertNotIn("<script>alert(2)</script>", html)
        self.assertIn("&lt;img", html)
        self.assertIn("&lt;script&gt;", html)

    def test_build_and_render_do_not_mutate_sources(self):
        run_path = self.write_run()
        ledger = self.write_ledger()
        before = (_digest(run_path), _digest(ledger))
        detail = self.build()
        run_detail.render_html(detail)
        run_detail.render_summary(detail)
        after = (_digest(run_path), _digest(ledger))
        self.assertEqual(before, after)
        self.assertTrue(detail["read_only"])

    def test_cli_html_refuses_overwrite(self):
        self.write_run()
        import subprocess
        import sys
        root = Path(__file__).resolve().parents[1]
        out = self.data / "page.html"
        out.write_text("keep", encoding="utf-8")
        proc = subprocess.run(
            [sys.executable, str(root / "mkt.py"), "grow", "detail", "demo",
             "--html", str(out)],
            cwd=str(root), capture_output=True, text=True)
        self.assertEqual(2, proc.returncode, proc.stdout + proc.stderr)
        self.assertEqual("keep", out.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
