import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

from core import output_strategy
from core import preparation_runtime


def ready_items(strategy, wave_id):
    wave = next(row for row in strategy["waves"] if row["id"] == wave_id)
    items = []
    for product, allocation in wave["allocations"].items():
        for key, count in allocation.items():
            platform, action = key.split("/")
            for index in range(count):
                text = "%s %s %s %s" % (wave_id, product, key, index)
                sha = hashlib.sha256(text.encode()).hexdigest()
                target = {"audience": "目标角色", "context": "具体讨论"}
                if action == "reply":
                    target.update(url="https://example.test/%s/%s/%s" % (product, key, index),
                                  qualification={"persona": "目标角色"})
                elif action == "message":
                    target.update(handle="@%s%s%s" % (product, index, wave_id),
                                  url="https://x.com/%s" % product,
                                  qualification={"dm_open": True})
                content = {"text": text, "sha256": sha}
                if action == "publish":
                    content["review"] = {"reviewer": "my-voice", "verdict": "pass",
                                         "source_sha256": sha}
                items.append({
                    "id": "%s-%s-%s-%s" % (wave_id, product, key.replace("/", "-"), index),
                    "wave_id": wave_id, "product": product,
                    "platform": platform, "action": action,
                    "goal_ids": [strategy["outcomes"][product]["primary"]["id"]],
                    "target": target, "content": content,
                    "measurement": {"execution_event": "sent", "interaction_event": "reply",
                                    "business_event": "result"},
                })
    return items


class PreparationRuntimeTest(unittest.TestCase):
    def setUp(self):
        payload = json.loads(Path("examples/execution-strategy-300.json").read_text())
        self.strategy = output_strategy.compile_strategy(payload)
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def test_preparation_starts_before_first_wave(self):
        calls = []
        def runner(task, strategy, state):
            calls.append(task["id"])
            return {"ok": True, "items": []}
        result = preparation_runtime.advance(
            self.tmp.name, self.strategy, "2026-11-26T19:00:00+08:00",
            runner=runner, max_tasks=2)
        self.assertEqual("preparing", result["wave_status"])
        self.assertEqual(["generate-original-titles", "mine-reply-topics"], calls)
        self.assertEqual(0, result["external_writes"])

    def test_shortage_is_reported_and_bounded(self):
        calls = []
        def runner(task, strategy, state):
            calls.append(task["id"])
            return {"ok": True, "items": []}
        result = preparation_runtime.advance(
            self.tmp.name, self.strategy, "2026-11-26T20:00:00+08:00",
            runner=runner, max_tasks=2)
        self.assertEqual("preparing", result["wave_status"])
        self.assertLessEqual(len(calls), 2)
        self.assertTrue(result["shortfall"])
        self.assertTrue(result["state"]["tasks"])

    def test_ready_wave_freezes_snapshot_and_resumes_after_receipt(self):
        state = preparation_runtime.load_state(self.tmp.name, self.strategy)
        state["supply"] = {"w01": {"items": ready_items(self.strategy, "w01")}}
        state["waves"] = {"w01": {"status": "sent"}}
        preparation_runtime.save_state(self.tmp.name, self.strategy, state)
        result = preparation_runtime.advance(
            self.tmp.name, self.strategy, "2026-11-26T20:00:00+08:00",
            runner=lambda *args: self.fail("发送前必须停止备料"), max_tasks=2)
        self.assertEqual("waiting_reconciliation", result["wave_status"])
        snapshot = result["snapshot"]
        before = snapshot["sha256"]
        snapshot["items"].append({"id": "unapproved"})
        stored = preparation_runtime.status(self.tmp.name, self.strategy, "w01")
        self.assertEqual(before, stored["snapshot"]["sha256"])
        self.assertEqual(30, len(stored["snapshot"]["items"]))
        done = preparation_runtime.mark_wave_reconciled(
            self.tmp.name, self.strategy, "w01")
        self.assertEqual("preparing", done["wave_status"])

    def test_ready_future_wave_shifts_preparation_to_next_deficient_wave(self):
        state = preparation_runtime.load_state(self.tmp.name, self.strategy)
        state["supply"] = {"w01": {"items": ready_items(self.strategy, "w01")}}
        preparation_runtime.save_state(self.tmp.name, self.strategy, state)
        calls = []
        def runner(task, strategy, state):
            calls.append(task["id"])
            return {"ok": True, "items": []}
        result = preparation_runtime.advance(
            self.tmp.name, self.strategy, "2026-11-26T19:00:00+08:00",
            runner=runner, max_tasks=1)
        self.assertEqual("w02", result["wave_id"])
        self.assertTrue(result["shortfall"])
        self.assertEqual(1, len(calls))

    def test_ready_unsent_wave_waits_for_exact_scope_approval(self):
        state = preparation_runtime.load_state(self.tmp.name, self.strategy)
        state["supply"] = {"w01": {"items": ready_items(self.strategy, "w01")}}
        preparation_runtime.save_state(self.tmp.name, self.strategy, state)
        result = preparation_runtime.advance(
            self.tmp.name, self.strategy, "2026-11-26T20:00:00+08:00",
            runner=lambda *args: self.fail("齐备时必须停止备料"), max_tasks=2)
        self.assertEqual("waiting_wave_approval", result["wave_status"])
        self.assertEqual(30, len(result["snapshot"]["items"]))
        first = result["snapshot"]["items"][0]
        self.assertIsInstance(first["target"], str)
        self.assertTrue(first["text"])
        self.assertTrue(first["actor"])

    def test_unknown_receipt_cannot_be_marked_reconciled(self):
        result = preparation_runtime.mark_wave_reconciled(
            self.tmp.name, self.strategy, "missing-wave")
        self.assertEqual("error", result["status"])

    def test_stale_my_voice_review_does_not_make_supply_eligible(self):
        items = ready_items(self.strategy, "w01")
        items[0]["content"]["review"]["source_sha256"] = "stale"
        state = preparation_runtime.load_state(self.tmp.name, self.strategy)
        state["supply"] = {"w01": {"items": items}}
        preparation_runtime.save_state(self.tmp.name, self.strategy, state)
        result = preparation_runtime.status(self.tmp.name, self.strategy, "w01")
        self.assertEqual(29, result["eligible"])
        self.assertTrue(result["shortfall"])

    def test_manual_wait_does_not_block_independent_miner(self):
        def runner(task, strategy, state):
            if task["runner"] == "manual":
                return {"ok": False, "waiting_input": True, "reason": "need writer"}
            return {"ok": True, "items": []}
        result = preparation_runtime.advance(
            self.tmp.name, self.strategy, "2026-11-26T19:00:00+08:00",
            runner=runner, max_tasks=2)
        self.assertEqual("waiting_input", result["state"]["tasks"]["generate-original-titles"]["status"])
        self.assertEqual("completed", result["state"]["tasks"]["mine-reply-topics"]["status"])

    def test_host_adapter_uses_declared_result_contract(self):
        adapter = {
            "schema": "growovo.host-adapter/1", "adapter_id": "test-writer",
            "version": "1", "producer_id": "test-producer",
            "argv": [sys.executable, "-c",
                     "import json,sys,pathlib; p=pathlib.Path(sys.argv[1]).parent/'result.json'; "
                     "p.write_text(json.dumps({'schema':'growovo.preparation-result/1',"
                     "'producer_id':'test-producer','items':[]}))"],
            "authorization": {"authorized_by": "tester"}, "timeout_seconds": 10,
        }
        task = {"id": "generate-original-titles", "_attempt_id": "attempt-1",
                "config": {"adapter": adapter}}
        result = preparation_runtime._run_host(self.tmp.name, task, self.strategy, {})
        self.assertTrue(result["ok"])
        self.assertEqual([], result["items"])

    def test_safety_stop_prevents_preparation_adapters(self):
        state = preparation_runtime.load_state(self.tmp.name, self.strategy)
        state["safety_stop"] = {"reason": "429"}
        preparation_runtime.save_state(self.tmp.name, self.strategy, state)
        result = preparation_runtime.advance(
            self.tmp.name, self.strategy, "2026-11-26T19:00:00+08:00",
            runner=lambda *args: self.fail("停止状态不能执行备料"))
        self.assertEqual("stopped", result["wave_status"])
        self.assertEqual(0, result["executed"])

    def test_changed_strategy_cannot_start_a_parallel_preparation_state(self):
        state = preparation_runtime.load_state(self.tmp.name, self.strategy)
        preparation_runtime.save_state(self.tmp.name, self.strategy, state)
        changed = json.loads(json.dumps(self.strategy))
        changed["items"] = [{"id": "unapproved-change"}]
        with self.assertRaisesRegex(ValueError, "strategy_changed"):
            preparation_runtime.load_state(self.tmp.name, changed)

    def test_host_recovers_existing_attempt_result_without_rerunning(self):
        root = Path(self.tmp.name) / "state" / "preparation-work" / "task" / "attempt-1"
        root.mkdir(parents=True)
        (root / "result.json").write_text(json.dumps({
            "schema": "growovo.preparation-result/1", "producer_id": "test-producer", "items": []}))
        adapter = {
            "schema": "growovo.host-adapter/1", "adapter_id": "test-writer",
            "version": "1", "producer_id": "test-producer",
            "argv": [sys.executable, "-c", "raise RuntimeError('must not rerun')"],
            "authorization": {"authorized_by": "tester"}, "timeout_seconds": 10,
        }
        task = {"id": "task", "_attempt_id": "attempt-1", "config": {"adapter": adapter}}
        result = preparation_runtime._run_host(self.tmp.name, task, self.strategy, {})
        self.assertTrue(result["ok"])


if __name__ == "__main__":
    unittest.main()
