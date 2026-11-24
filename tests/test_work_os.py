#!/usr/bin/env python3
import copy
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import metrics, work
from core.sop import SOPError, SOPRegistry, SOPRunStore, lint_sop
from core.work import ContextStore, ObjectiveStore, WorkError, WorkStore, lint_work_order

ROOT = Path(__file__).resolve().parents[1]


class WorkOSCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        self.context = {
            "context_id": "ctx-example-product",
            "version": "2026-11-04.1",
            "source": {"kind": "user_declared", "ref": "fixture:user-input"},
            "product_facts": [{"fact": "example API"}],
            "value_event_definitions": [{"event": "first_successful_api_call"}],
            "non_goals": ["no autonomous publishing"],
            "claims": [{"claim_id": "claim-example-api",
                        "statement": "示例 API 可被开发者直接调用",
                        "status": "evidence_supported",
                        "evidence_refs": [{"evidence_id": "fixture:user-input",
                                           "source": "fixture:user-input"}]}],
            "approved_by": "human-owner",
            "content_sha256": "fixture-sha",
        }
        ContextStore(self.data).import_snapshot(self.context, by="human-owner")
        self.objective = {
            "objective_id": "obj-first-success-001",
            "version": "1",
            "context_ref": {"context_id": self.context["context_id"], "version": self.context["version"]},
            "business_question": "开发者能否独立完成首次成功调用",
            "subject_type": "developer",
            "primary_metric_ref": {"metric_id": "developer_activation_rate", "version": "1.0.0"},
            "window": "2026-11-05/2026-11-21",
            "owner": "person-product-growth-owner",
        }
        ObjectiveStore(self.data).create(self.objective, by="human-owner")
        self.order = json.loads((ROOT / "docs/gtm-os/examples/work-order.example.json").read_text(encoding="utf-8"))

    def test_context_is_immutable_and_user_declared(self):
        store = ContextStore(self.data)
        with self.assertRaises(WorkError):
            store.import_snapshot(self.context, by="human-owner")
        loaded = store.get(self.context["context_id"], self.context["version"])
        self.assertEqual("user_declared", loaded["source"]["kind"])

    def test_askovo_context_requires_existing_file_and_matching_hash(self):
        src = self.data / "chain.json"
        src.write_text('{"chains":[]}', encoding="utf-8")
        digest = hashlib.sha256(src.read_bytes()).hexdigest()
        base = copy.deepcopy(self.context)
        base.update({
            "context_id": "ctx-askovo-ok",
            "source": {"kind": "askovo", "ref": str(src), "chain_id": "PR01"},
            "content_sha256": digest,
        })
        ContextStore(self.data).import_snapshot(base, by="human-owner")

        bad_hash = copy.deepcopy(base)
        bad_hash["context_id"] = "ctx-askovo-bad-hash"
        bad_hash["content_sha256"] = "0" * 64
        with self.assertRaises(WorkError):
            ContextStore(self.data).import_snapshot(bad_hash, by="human-owner")

        missing = copy.deepcopy(base)
        missing["context_id"] = "ctx-askovo-missing"
        missing["source"] = {"kind": "askovo",
                             "ref": str(self.data / "nope.json"),
                             "chain_id": "PR01"}
        with self.assertRaises(WorkError):
            ContextStore(self.data).import_snapshot(missing, by="human-owner")

    def test_legacy_askovo_snapshot_migrates_without_upgrading_claims(self):
        source = self.data / "legacy-askovo.json"
        source.write_text(json.dumps({
            "context_id": "ctx-cleaning-robot", "version": "2026-11-19.1",
            "source": {"kind": "askovo", "ref": "A-02/study"},
            "product_facts": [
                {"fact": "产品是家用扫地机器人，主打自动集尘基站"},
                {"fact": "目标用户是 80–120 平米、有娃或养宠的家庭"},
                {"fact": "自动集尘版本比普通版贵 300 元"},
            ],
            "value_event_definitions": [{"event": "first_auto_empty_cycle"}],
            "non_goals": ["不做商用清洁"], "approved_by": "roy",
        }, ensure_ascii=False), encoding="utf-8")
        self.assertTrue(hasattr(work, "context_snapshot_from_file"), "legacy import adapter is missing")
        migrated = work.context_snapshot_from_file(source)
        saved = ContextStore(self.data).import_snapshot(migrated, by="human-owner")
        self.assertEqual(3, len(saved["claims"]))
        self.assertEqual({"insufficient_evidence"}, {claim["status"] for claim in saved["claims"]})
        self.assertTrue(all(claim["evidence_refs"] for claim in saved["claims"]))
        self.assertEqual("A-02/study", saved["source"]["original_ref"])
        self.assertEqual(str(source), saved["source"]["ref"])
        self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), saved["content_sha256"])
        self.assertEqual("legacy_askovo_product_facts/1", saved["migration"]["kind"])
        self.assertNotIn("evidence_supported", json.dumps(saved["claims"]))

    def test_modern_askovo_snapshot_is_not_rewritten_by_legacy_adapter(self):
        source = self.data / "modern-askovo.json"
        modern = copy.deepcopy(self.context)
        modern["source"] = {"kind": "askovo", "ref": str(source)}
        modern.pop("content_sha256")
        source.write_text(json.dumps(modern), encoding="utf-8")
        self.assertEqual(modern, work.context_snapshot_from_file(source))
        with self.assertRaises(WorkError):
            ContextStore(self.data).import_snapshot(work.context_snapshot_from_file(source), by="human-owner")

    def test_work_order_requires_accepted_inputs_before_ready(self):
        store = WorkStore(self.data)
        invalid = copy.deepcopy(self.order)
        invalid["input_bindings"][0]["acceptance_status"] = "submitted"
        store.create(invalid, by="owner")
        with self.assertRaises(WorkError):
            store.ready(invalid["work_order_id"], by="owner")
        self.assertEqual("blocked", store.get(invalid["work_order_id"])["state"])
        self.assertTrue(any("未验收" in e for e in lint_work_order(invalid)))

    def test_ready_start_and_complete_sop_with_evidence(self):
        store = WorkStore(self.data)
        store.create(self.order, by="owner")
        self.assertEqual([], lint_work_order(self.order))
        store.ready(self.order["work_order_id"], by="owner")
        runs = SOPRunStore(self.data)
        run = runs.start(self.order["work_order_id"], by="owner")
        self.assertEqual("running", store.get(self.order["work_order_id"])["state"])
        sop = SOPRegistry(self.data).get(self.order["sop_version_ref"]["sop_id"], self.order["sop_version_ref"]["version"])
        for index, step in enumerate(sop["steps"]):
            outputs = [{"deliverable_type": o["deliverable_type"],
                        "artifact_id": f"artifact-{index}",
                        "version": "1", "evidence_ref": f"fixture:output:{index}"}
                       for o in step["outputs"] if o.get("required", True)]
            results = [{"rule_id": d["rule_id"], "status": "pass",
                        "evidence_ref": f"fixture:dod:{index}"} for d in step["dod"]]
            approval = {"by": "human-reviewer", "scope": "fixture"} if step.get("approval", {}).get("required") else None
            run = runs.complete_step(run["run_id"], step["step_id"], outputs=outputs,
                                     dod_results=results, by="operator", approval=approval)
            if index + 1 < len(sop["steps"]):
                self.assertEqual(sop["steps"][index + 1]["step_id"], run["current_step_id"])
                self.assertEqual("ready", run["steps"][sop["steps"][index + 1]["step_id"]]["state"])
        self.assertEqual("done", run["state"])
        # Fresh projection has exactly the same state; no in-memory-only updates.
        self.assertEqual("done", runs.get(run["run_id"])["state"])

    def test_sop_rejects_missing_dod_evidence(self):
        WorkStore(self.data).create(self.order, by="owner")
        WorkStore(self.data).ready(self.order["work_order_id"], by="owner")
        runs = SOPRunStore(self.data)
        run = runs.start(self.order["work_order_id"], by="owner")
        sop = SOPRegistry(self.data).get(run["sop_id"], run["sop_version"])
        step = sop["steps"][0]
        outputs = [{"deliverable_type": o["deliverable_type"], "artifact_id": "a1",
                    "version": "1", "evidence_ref": "fixture:out"} for o in step["outputs"]]
        results = [{"rule_id": d["rule_id"], "status": "pass", "evidence_ref": ""} for d in step["dod"]]
        with self.assertRaisesRegex(ValueError, "evidence_ref"):
            runs.complete_step(run["run_id"], step["step_id"], outputs=outputs,
                               dod_results=results, by="operator",
                               approval={"by": "human", "scope": "technical"})

    def test_consumer_content_social_and_email_sops_are_built_in(self):
        registry = SOPRegistry(self.data)
        expected = {
            "sop-content-x-email": "C02",
            "sop-x-publish": "C03",
            "sop-lifecycle-email": "C13",
        }
        coverage = registry.coverage()
        self.assertGreaterEqual(coverage["covered"], 4)
        for sop_id, capability in expected.items():
            sop = registry.get(sop_id, "0.1.0")
            self.assertEqual(capability, sop["capability_id"])
            self.assertEqual([], lint_sop(sop))
            self.assertIn("close", {step["on_pass"] for step in sop["steps"]})
            self.assertFalse(any(tool["kind"] not in ("read_only", "draft", "action_request")
                                 for step in sop["steps"] for tool in step["tools"]))

    def test_sop_lint_rejects_vague_or_short_process(self):
        self.assertTrue(lint_sop({"sop_id": "bad", "version": "1", "capability_id": "C18", "steps": []}))

    def test_dependency_must_be_accepted(self):
        store = WorkStore(self.data)
        first = copy.deepcopy(self.order)
        store.create(first, by="owner")
        second = copy.deepcopy(self.order)
        second["work_order_id"] = "wo-dependent"
        second["dependencies"] = [first["work_order_id"]]
        store.create(second, by="owner")
        with self.assertRaisesRegex(WorkError, "依赖"):
            store.ready(second["work_order_id"], by="owner")

    def test_sop_capability_mismatch_blocks_lint_and_ready(self):
        store = WorkStore(self.data)
        order = copy.deepcopy(self.order)
        order["work_order_id"] = "wo-capability-mismatch"
        order["capability_id"] = "C01"
        order["role_family"] = "07"
        store.create(order, by="owner")
        errors = lint_work_order(order, sop_registry=SOPRegistry(self.data))
        self.assertTrue(any("属于 C18" in e for e in errors), errors)
        self.assertTrue(any("属于" in e for e in store.lint(order["work_order_id"])))
        with self.assertRaisesRegex(WorkError, "capability_id"):
            store.ready(order["work_order_id"], by="owner")
        self.assertEqual("blocked", store.get(order["work_order_id"])["state"])

    def test_sop_start_refuses_capability_mismatch_without_writing_run(self):
        store = WorkStore(self.data)
        order = copy.deepcopy(self.order)
        order["work_order_id"] = "wo-capability-mismatch-start"
        order["capability_id"] = "C01"
        order["role_family"] = "07"
        store.create(order, by="owner")
        store._set_state(order["work_order_id"], "ready", by="owner")
        runs = SOPRunStore(self.data)
        with self.assertRaisesRegex(SOPError, "属于"):
            runs.start(order["work_order_id"], by="owner")
        self.assertEqual([], runs.list())

    def _claims_context(self, claims, context_id):
        snapshot = copy.deepcopy(self.context)
        snapshot["context_id"] = context_id
        snapshot["claims"] = claims
        return snapshot

    def test_context_requires_claims_with_evidence_or_assumption(self):
        store = ContextStore(self.data)
        missing = copy.deepcopy(self.context)
        missing["context_id"] = "ctx-no-claims"
        missing.pop("claims")
        with self.assertRaisesRegex(WorkError, "claims"):
            store.import_snapshot(missing, by="human-owner")

        short = [{"claim_id": "c1", "statement": "太短", "status": "evidence_supported",
                  "evidence_refs": [{"evidence_id": "e1", "source": "fixture"}]}]
        with self.assertRaisesRegex(WorkError, "8"):
            store.import_snapshot(self._claims_context(short, "ctx-short"), by="human-owner")

        bad_status = [{"claim_id": "c1", "statement": "示例 API 可被直接调用",
                       "status": "true", "evidence_refs": [{"evidence_id": "e1", "source": "fixture"}]}]
        with self.assertRaisesRegex(WorkError, "status"):
            store.import_snapshot(self._claims_context(bad_status, "ctx-bad-status"), by="human-owner")

        no_ref = [{"claim_id": "c1", "statement": "示例 API 可被直接调用",
                   "status": "evidence_supported", "evidence_refs": []}]
        with self.assertRaisesRegex(WorkError, "evidence_refs"):
            store.import_snapshot(self._claims_context(no_ref, "ctx-no-ref"), by="human-owner")

        hypothesis = [{"claim_id": "c1", "statement": "开发者会持续调用示例 API",
                       "status": "hypothesis", "evidence_refs": []}]
        with self.assertRaisesRegex(WorkError, "assumption"):
            store.import_snapshot(self._claims_context(hypothesis, "ctx-hypothesis"), by="human-owner")

        duplicate = [{"claim_id": "c1", "statement": "示例 API 可被直接调用",
                      "status": "insufficient_evidence",
                      "evidence_refs": [{"evidence_id": "e1", "source": "fixture"}]},
                     {"claim_id": "c1", "statement": "示例 API 有公开文档",
                      "status": "insufficient_evidence",
                      "evidence_refs": [{"evidence_id": "e2", "source": "fixture"}]}]
        with self.assertRaisesRegex(WorkError, "重复"):
            store.import_snapshot(self._claims_context(duplicate, "ctx-duplicate"), by="human-owner")

        accepted = store.import_snapshot(self._claims_context(
            [{"claim_id": "c1", "statement": "开发者会持续调用示例 API",
              "status": "hypothesis", "assumption": True, "evidence_refs": []}],
            "ctx-hypothesis-ok"), by="human-owner")
        self.assertEqual("c1", accepted["claims"][0]["claim_id"])

    def test_work_order_due_must_be_declared_or_not_applicable(self):
        order = copy.deepcopy(self.order)
        order["schedule"].pop("due")
        order.pop("due", None)
        self.assertTrue(any("not_applicable" in e for e in lint_work_order(order)))
        order["due"] = "not_applicable"
        self.assertEqual([], lint_work_order(order))

    def test_depends_on_requires_ids_or_not_applicable(self):
        empty = copy.deepcopy(self.order)
        empty["depends_on"] = []
        self.assertTrue(any("depends_on" in e for e in lint_work_order(empty)))
        self.assertEqual([], lint_work_order(self.order))

        store = WorkStore(self.data)
        store.create(copy.deepcopy(self.order), by="owner")
        dependent = copy.deepcopy(self.order)
        dependent["work_order_id"] = "wo-depends-on"
        dependent["depends_on"] = [{"work_order_id": self.order["work_order_id"]}]
        store.create(dependent, by="owner")
        with self.assertRaisesRegex(WorkError, "依赖"):
            store.ready(dependent["work_order_id"], by="owner")

    def test_update_merges_patch_and_bumps_revision(self):
        store = WorkStore(self.data)
        store.create(self.order, by="owner")
        before = store.get(self.order["work_order_id"])
        updated = store.update(self.order["work_order_id"], {"title": "改过的标题"}, by="owner")
        self.assertEqual("改过的标题", updated["title"])
        self.assertEqual(2, updated["revision"])
        self.assertEqual(before["business_purpose"], updated["business_purpose"])

        merged = store.update(self.order["work_order_id"],
                              {"schedule": {"due": "2026-12-01T18:00:00+08:00"}}, by="owner")
        self.assertEqual(3, merged["revision"])
        for key, value in before["schedule"].items():
            if key != "due":
                self.assertEqual(value, merged["schedule"][key])
        self.assertEqual("2026-12-01T18:00:00+08:00", merged["schedule"]["due"])

        with self.assertRaisesRegex(WorkError, "未知字段"):
            store.update(self.order["work_order_id"], {"not_a_field": 1}, by="owner")
        with self.assertRaisesRegex(WorkError, "state"):
            store.update(self.order["work_order_id"], {"state": "ready"}, by="owner")

    def test_update_and_replace_refuse_non_draft_order(self):
        store = WorkStore(self.data)
        store.create(self.order, by="owner")
        store.ready(self.order["work_order_id"], by="owner")
        with self.assertRaisesRegex(WorkError, "draft/blocked"):
            store.update(self.order["work_order_id"], {"title": "x"}, by="owner")
        with self.assertRaisesRegex(WorkError, "draft/blocked"):
            store.replace(self.order["work_order_id"], self.order, by="owner")

    def _mkt(self, *args):
        result = subprocess.run(
            [sys.executable, str(ROOT / "mkt.py"), "--workspace", str(self.data), *args],
            cwd=ROOT, text=True, capture_output=True)
        self.assertEqual(0, result.returncode, msg=f"{result.stdout}\n{result.stderr}")
        return json.loads(result.stdout)

    def test_cli_work_update_bumps_revision(self):
        order_path = self.data / "order.json"
        order_path.write_text(json.dumps(self.order, ensure_ascii=False), encoding="utf-8")
        self._mkt("work", "create", "--file", str(order_path), "--by", "owner")
        patch_path = self.data / "patch.json"
        patch_path.write_text(json.dumps({"title": "CLI 改标题"}, ensure_ascii=False), encoding="utf-8")
        payload = self._mkt("work", "update", "--id", self.order["work_order_id"],
                            "--file", str(patch_path), "--by", "owner")
        self.assertEqual(2, payload["work_order"]["revision"])
        self.assertEqual("CLI 改标题", payload["work_order"]["title"])
        self.assertEqual([], payload["lint_errors"])


class TestMetrics(unittest.TestCase):
    def setUp(self):
        self.registry = metrics.MetricRegistry(Path(tempfile.mkdtemp()))
        self.metric = self.registry.get("developer_activation_rate", "1.0.0")
        self.events = [
            {"event_type": "eligible_tutorial_cohort_entry", "developer_id": "d1"},
            {"event_type": "eligible_tutorial_cohort_entry", "developer_id": "d2"},
            {"event_type": "first_successful_api_call", "developer_id": "d1", "verified": True, "approximated": False},
            {"event_type": "first_successful_api_call", "developer_id": "d1", "verified": True, "approximated": False},
            {"event_type": "first_successful_api_call", "developer_id": "d2", "verified": False, "approximated": False},
        ]

    def test_dedupes_and_does_not_count_unverified(self):
        result = metrics.measure(self.metric, self.events)
        self.assertEqual(2, result["denominator"])
        self.assertEqual(1, result["numerator"])
        self.assertEqual(0.5, result["value"])

    def test_zero_denominator_is_null_not_zero_percent(self):
        result = metrics.measure(self.metric, [])
        self.assertIsNone(result["value"])
        self.assertEqual("insufficient_denominator", result["status"])

    def test_pending_maturity_is_not_failure(self):
        result = metrics.measure(self.metric, self.events,
                                 cohort_end="2026-11-01T00:00:00+08:00",
                                 as_of="2026-11-02T00:00:00+08:00")
        self.assertEqual("pending_maturity", result["status"])
        self.assertIsNone(result["value"])

    def test_scope_has_no_full_history_fallback(self):
        result = metrics.measure(self.metric, self.events, scope={"campaign": "current-only"})
        self.assertEqual("insufficient_denominator", result["status"])

    def test_date_only_until_includes_the_whole_day(self):
        events = [
            {"event_type": "eligible_tutorial_cohort_entry", "developer_id": "d1", "ts": "2026-11-14T23:59:00+08:00"},
            {"event_type": "first_successful_api_call", "developer_id": "d1", "verified": True,
             "approximated": False, "ts": "2026-11-14T23:59:30+08:00"},
        ]
        result = metrics.measure(self.metric, events, scope={"since": "2026-11-14", "until": "2026-11-14"})
        self.assertEqual(1.0, result["value"])


if __name__ == "__main__":
    unittest.main()
