#!/usr/bin/env python3
"""Run manifests and independent, non-mutating receipt audits."""
import datetime as dt
import contextlib
import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from core import engagement_cycle as cycle
from core import cycle_report
from core import receipt_audit
from core import run_manifest
from core.ledger import Ledger
import mkt

TZ = dt.timezone(dt.timedelta(hours=8))


def wave_result(state="committed", ref="op-1", channel="github"):
    target = "owner/repo#1" if channel == "github" else "https://x.com/a/status/1"
    return {"channel": channel, "action": "reply", "target": target,
            "actor": "a", "result": state, "ledger_result": state,
            "ref": ref, "ok": state == "committed"}


class ManifestTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = Path(self.tmp.name) / "data"
        self.source = Path(self.tmp.name) / "source"
        (self.source / "core").mkdir(parents=True)
        (self.source / "channels").mkdir()
        (self.source / "core" / "a.py").write_text("A = 1\n", encoding="utf-8")
        (self.source / "mkt.py").write_text("# entry\n", encoding="utf-8")
        self.queue = self.data / "state" / "engagement-queue.json"
        cycle.save_queue(self.queue, [{"channel": "x", "action": "publish", "text": "hello"}])

    def write(self):
        return run_manifest.write_manifest(
            self.data, source_dir=self.source, queue_path=self.queue,
            policy={"window": {"start_hour": 22}}, night="2026-11-23", wave="01-2205",
            campaign="night-test", session="test", preflight={"enabled": False},
            now=dt.datetime(2026, 11, 23, 22, 5, tzinfo=TZ), attempt_id="attempt-1")

    def test_manifest_hashes_source_queue_policy_and_authorization_without_body(self):
        policy = self.data / "policies" / "engagement-cycle.json"
        policy.parent.mkdir(parents=True)
        policy.write_text('{"window":{"start_hour":22}}', encoding="utf-8")
        auth = self.data / "policies" / "owner-attested-x.json"
        auth.write_text('{"token":"do-not-copy"}', encoding="utf-8")
        got = self.write()
        text = Path(got["manifest_path"]).read_text(encoding="utf-8")
        self.assertEqual(64, len(got["source_sha256"]))
        self.assertEqual(hashlib.sha256(self.queue.read_bytes()).hexdigest(), got["queue_sha256"])
        self.assertEqual(hashlib.sha256(auth.read_bytes()).hexdigest(),
                         got["authorization_files"][0]["sha256"])
        self.assertNotIn("do-not-copy", text)
        self.assertIn("preflight", got)

        before = got["source_sha256"]
        (self.source / "core" / "new_untracked.py").write_text("B = 2\n", encoding="utf-8")
        after = self.write()["source_sha256"]
        self.assertNotEqual(before, after)

    def test_real_wave_writes_manifest_and_returns_its_path(self):
        row = {"channel": "github", "action": "reply", "target": "owner/repo#1", "text": "hello",
               "source": "queue", "actor": "", "context": "peer context"}
        cycle.save_queue(self.queue, [row])
        now = dt.datetime(2026, 11, 23, 22, 5, tzinfo=TZ)
        fake = {"ok": True, "result": "committed", "ledger_result": "committed", "ref": "op-1"}
        with mock.patch.object(cycle, "_now", return_value=now), \
                mock.patch("core.action_runner.run_action", return_value=fake):
            got = cycle.run(self.data, go=True, queue_path=self.queue,
                            wave_id="01-2205", log=lambda *_: None)
        self.assertTrue(got["manifest"]["manifest_path"])
        manifest = json.loads(Path(got["manifest"]["manifest_path"]).read_text(encoding="utf-8"))
        self.assertEqual("night-2026-11-23-w01-2205", manifest["campaign"])
        self.assertEqual(64, len(manifest["source_sha256"]))
        self.assertEqual(64, len(manifest["queue_sha256"]))
        self.assertEqual(got["run_context"]["attempt_id"], manifest["attempt_id"])
        self.assertEqual(got["run_context"]["run_id"], got["results"][0]["run_id"])
        self.assertEqual(manifest["attempt_id"], got["results"][0]["attempt_id"])
        self.assertEqual(got["results"][0]["queue_item_id"], got["results"][0]["action_id"])
        report = cycle_report.build_report(self.data, night="2026-11-23")
        self.assertEqual(1, report["wave_count"])
        self.assertEqual([], report["errors"])

    def test_manifest_failure_blocks_before_external_dispatch(self):
        row = {"channel": "x", "action": "publish", "target": "", "text": "hello"}
        cycle.save_queue(self.queue, [row])
        now = dt.datetime(2026, 11, 23, 22, 5, tzinfo=TZ)
        with mock.patch.object(cycle, "_now", return_value=now), \
                mock.patch("core.run_manifest.write_manifest", side_effect=OSError("disk full")), \
                mock.patch("core.action_runner.run_action") as dispatch:
            got = cycle.run(self.data, go=True, queue_path=self.queue,
                            wave_id="01-2205", log=lambda *_: None)
        self.assertFalse(got["ok"])
        self.assertEqual("manifest_error", got["reason"])
        dispatch.assert_not_called()


class ReceiptAuditTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = Path(self.tmp.name)
        self.dir = self.data / "state" / "engagement-2026-11-23"
        self.dir.mkdir(parents=True)
        self.ledger = Ledger(self.data / "ledger.jsonl")

    def write_wave(self, results):
        path = self.dir / "wave-01-2205.json"
        path.write_text(json.dumps({
            "ok": True, "wave": "01-2205", "night": "2026-11-23",
            "campaign": "night-2026-11-23-w01-2205", "dry": False,
            "planned": len(results), "results": results,
        }), encoding="utf-8")

    def append_ledger(self, result="committed", evidence="https://github.com/owner/repo/issues/1#issuecomment-99",
                      channel="github", ref="op-1"):
        return self.ledger.append(
            channel=channel, action="reply",
            target="owner/repo#1" if channel == "github" else "https://x.com/a/status/1",
            result=result, campaign="night-2026-11-23-w01-2205",
            evidence=evidence, ref=ref)

    def test_structural_audit_matches_wave_and_ledger_receipts(self):
        self.write_wave([wave_result(), wave_result(ref="op-2", channel="x")])
        self.append_ledger()
        self.append_ledger(channel="x", ref="op-2", evidence="1234567890")
        got = receipt_audit.audit(self.data, night="2026-11-23")
        self.assertTrue(got["ok"])
        self.assertEqual("structural", got["level"])
        self.assertEqual(2, got["counts"]["structural_valid"])

    def test_missing_ledger_or_evidence_is_not_valid(self):
        self.write_wave([wave_result(), wave_result(ref="op-2", channel="x")])
        self.append_ledger(channel="x", ref="op-2", evidence="")
        got = receipt_audit.audit(self.data, night="2026-11-23")
        self.assertFalse(got["ok"])
        self.assertEqual(1, got["counts"]["missing_ledger"])
        self.assertEqual(1, got["counts"]["missing_evidence"])

    def test_live_mismatch_never_mutates_ledger_or_retries(self):
        self.write_wave([wave_result()])
        self.append_ledger()
        before = (self.data / "ledger.jsonl").read_bytes()
        verifier = mock.Mock(return_value={"status": "mismatch", "detail": "comment missing"})
        got = receipt_audit.audit(self.data, night="2026-11-23", live=True, verifier=verifier)
        self.assertFalse(got["ok"])
        self.assertEqual(1, got["counts"]["mismatch"])
        self.assertEqual(before, (self.data / "ledger.jsonl").read_bytes())
        verifier.assert_called_once()

    def test_indeterminate_resolution_uses_append_only_ledger_view(self):
        self.write_wave([wave_result(state="indeterminate", channel="x")])
        self.append_ledger(result="indeterminate", channel="x", evidence="123456")
        self.ledger.resolve("op-1", result="committed", by="roy", note="platform receipt 123456")
        got = receipt_audit.audit(self.data, night="2026-11-23")
        self.assertTrue(got["ok"])
        self.assertEqual(1, got["counts"]["structural_valid"])
        self.assertEqual("roy", got["items"][0]["resolved_by"])

    def test_cli_receipt_audit_is_read_only(self):
        self.write_wave([wave_result()])
        self.append_ledger()
        before = (self.data / "ledger.jsonl").read_bytes()
        output = io.StringIO()
        with mock.patch.object(mkt, "DATA", self.data), contextlib.redirect_stdout(output):
            code = mkt.cmd_cycle(SimpleNamespace(
                action="receipts", night="2026-11-23", scheduled_only=False, live=False))
        self.assertEqual(0, code)
        self.assertEqual(1, json.loads(output.getvalue())["counts"]["structural_valid"])
        self.assertEqual(before, (self.data / "ledger.jsonl").read_bytes())

    def test_github_live_verifier_uses_read_only_comment_lookup(self):
        record = self.append_ledger()
        response = {"ok": True, "out": json.dumps({"html_url": record["evidence"]})}
        with mock.patch("core.receipt_audit.gh_post.run", return_value=response) as call:
            got = receipt_audit.live_verify(record)
        self.assertEqual("verified", got["status"])
        args = call.call_args.args[0]
        self.assertIn("GET", args)
        self.assertNotIn("POST", args)


if __name__ == "__main__":
    unittest.main()
