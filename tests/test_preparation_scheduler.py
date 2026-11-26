import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core import cycle_scheduler
from core import output_strategy
from core import scheduled_scope


def write_strategy(directory):
    document = {"schema": "growovo.execution-strategy/1", "strategy_sha256": "test",
                "campaign": {"id": "test"}, "waves": [], "items": [],
                "preparation": {"tasks": []}}
    path = Path(directory) / "execution-plan.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


class StrategyBindingTest(unittest.TestCase):
    def test_strategy_binding_rejects_changed_document(self):
        with tempfile.TemporaryDirectory() as tmp:
            strategy = write_strategy(tmp)
            payload = json.loads(strategy.read_text())
            digest = output_strategy.strategy_digest(
                {key: value for key, value in payload.items() if key != "strategy_sha256"})
            job = {"strategy": strategy.name, "strategy_sha256": digest}
            self.assertEqual(digest, scheduled_scope.strategy_binding(
                Path(tmp) / "jobs.json", job)["sha256"])
            payload["items"] = [{"id": "changed"}]
            strategy.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(scheduled_scope.ScopeError, "strategy_changed"):
                scheduled_scope.strategy_binding(Path(tmp) / "jobs.json", job)


class PreparationSchedulerTest(unittest.TestCase):
    def config(self, directory, at="2030-01-01T20:00:00+08:00"):
        write_strategy(directory)
        config = Path(directory) / "jobs.json"
        config.write_text(json.dumps({
            "strategy": "execution-plan.json",
            "preparation": {"enabled": True, "max_tasks_per_tick": 1},
            "jobs": [{"id": "w01", "kind": "wave", "at": at,
                      "queue": "w01.json", "accounts": {"x": "owner"},
                      "targets": {"x/publish": 1}}],
        }), encoding="utf-8")
        return config

    def test_top_level_strategy_is_inherited_by_wave_execution_scope(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = self.config(tmp)
            loaded = cycle_scheduler._load_config(config)
            self.assertEqual("execution-plan.json", loaded["jobs"][0]["strategy"])

    def test_tick_advances_preparation_before_first_wave_is_due(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = self.config(tmp)
            with mock.patch("core.cycle_scheduler.preparation_runtime.advance",
                            return_value={"executed": 1, "external_writes": 0,
                                          "wave_status": "preparing"}) as advance:
                got = cycle_scheduler.tick(tmp, config, now="2029-01-01T20:00:00+08:00", go=True)
            self.assertEqual(1, advance.call_count)
            self.assertEqual(0, got["executed"])
            self.assertEqual(1, got["preparation"]["executed"])

    def test_short_supply_defers_wave_without_calling_executor(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = self.config(tmp, at="2029-01-01T20:00:00+08:00")
            executor = mock.Mock()
            with mock.patch("core.cycle_scheduler.preparation_runtime.advance",
                            return_value={"executed": 1, "external_writes": 0,
                                          "wave_status": "preparing", "wave_id": "w01"}):
                got = cycle_scheduler.tick(tmp, config, now="2029-01-01T20:00:00+08:00",
                                           go=True, executor=executor)
            stored = json.loads(cycle_scheduler.state_path(tmp).read_text())
            self.assertEqual("supply_not_ready", stored["jobs"]["w01"]["reason"])
            self.assertEqual("blocked", got["jobs"][0]["status"])
            executor.assert_not_called()

    def test_dry_run_reports_preparation_without_running_adapters(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = self.config(tmp)
            with mock.patch("core.cycle_scheduler.preparation_runtime.advance") as advance, \
                 mock.patch("core.cycle_scheduler.preparation_runtime.status",
                            return_value={"wave_status": "preparing", "eligible": 0}) as status:
                got = cycle_scheduler.tick(tmp, config, now="2029-01-01T20:00:00+08:00", go=False)
            advance.assert_not_called()
            status.assert_called_once()
            self.assertEqual("preparing", got["preparation"]["wave_status"])

    def test_successful_wave_marks_preparation_reconciled(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = self.config(tmp, at="2029-01-01T20:00:00+08:00")
            result = {"ok": True, "planned": 1, "committed": 1, "failed": 0,
                      "indeterminate": 0, "results": [{"result": "committed",
                      "channel": "x", "action": "publish"}]}
            with mock.patch("core.cycle_scheduler.preparation_runtime.advance",
                            return_value={"executed": 0, "external_writes": 0,
                                          "wave_status": "ready_to_send", "wave_id": "w01"}), \
                 mock.patch("core.cycle_scheduler.preparation_runtime.mark_wave_sent") as sent, \
                 mock.patch("core.cycle_scheduler.preparation_runtime.mark_wave_reconciled") as reconciled:
                got = cycle_scheduler.tick(tmp, config, now="2029-01-01T20:00:00+08:00",
                                           go=True, executor=mock.Mock(return_value=result))
            self.assertEqual("succeeded", got["jobs"][0]["status"])
            sent.assert_called_once()
            reconciled.assert_called_once()


if __name__ == "__main__":
    unittest.main()
