import tempfile
import unittest
from unittest import mock

from core import growth_runtime, growth_worker


class GrowthWorkerTest(unittest.TestCase):
    def test_component_failure_is_isolated_and_heartbeat_persists(self):
        with tempfile.TemporaryDirectory() as data:
            github = mock.Mock()
            github.sync_community_interactions.return_value = []
            gmail = mock.Mock()
            gmail.full_sync.side_effect = RuntimeError("mailbox unavailable")
            with mock.patch("core.github_app.GitHubAppClient.from_env", return_value=github), \
                    mock.patch("core.gmail_api.GmailClient.from_env", return_value=gmail):
                result = growth_worker.run_once(
                    data, "svc-test", github_repo="o/r", gmail_sync=True)
            self.assertTrue(result["components"]["github"]["ok"])
            self.assertFalse(result["components"]["gmail"]["ok"])
            self.assertTrue(result["components"]["recovery"]["ok"])
            status = growth_worker.status(data, "svc-test")
            self.assertIn("last_run_at", status)
            self.assertFalse(status["components"]["gmail"]["ok"])

    def test_due_watch_is_renewed_before_pubsub_pull(self):
        with tempfile.TemporaryDirectory() as data:
            growth_runtime.save_connector_cursor(
                data, "gmail", "default", "svc-test",
                {"history_id": "10", "watch_expiration": "1"})
            client = mock.Mock()
            client.watch.return_value = {"historyId": "10", "expiration": "1795489999999"}
            client.pubsub_pull.return_value = {"receivedMessages": []}
            with mock.patch("core.gmail_api.GmailClient.from_env", return_value=client), \
                    mock.patch("core.gmail_events.sync", return_value={"added": 0}):
                result = growth_worker.run_once(
                    data, "svc-test", watch_topic="projects/p/topics/t",
                    pubsub_subscription="projects/p/subscriptions/s")
            self.assertTrue(result["components"]["watch"]["ok"])
            self.assertTrue(result["components"]["pubsub"]["ok"])
            client.watch.assert_called_once_with("projects/p/topics/t")
            client.pubsub_pull.assert_called_once_with("projects/p/subscriptions/s")

    def test_continuous_cycles_keep_history_and_failure_after_restart(self):
        with tempfile.TemporaryDirectory() as data:
            self.assertTrue(hasattr(growth_worker, "history"), "worker history must be queryable")
            with mock.patch("core.growth_worker._now", side_effect=["2026-11-24T23:59:59+08:00", "2026-11-25T00:00:01+08:00"]):
                growth_worker.serve(data, "svc-test", interval_seconds=1, max_cycles=2)
            with mock.patch("core.growth_runtime.recover_expired", side_effect=RuntimeError("database temporarily unavailable")):
                growth_worker.serve(data, "svc-test", once=True)
            rows = growth_worker.history(data, "svc-test")
            self.assertEqual(3, len(rows))
            self.assertEqual([1, 2, 1], [row["cycle"] for row in rows])
            self.assertEqual(3, len({row["cycle_id"] for row in rows}))
            self.assertEqual("2026-11-24T23:59:59+08:00", rows[0]["last_run_at"])
            self.assertEqual("2026-11-25T00:00:01+08:00", rows[1]["last_run_at"])
            self.assertFalse(rows[-1]["components"]["recovery"]["ok"])
            self.assertFalse(growth_worker.status(data, "svc-test")["components"]["recovery"]["ok"])

    def test_concurrent_heartbeats_do_not_lose_cycle_history(self):
        from concurrent.futures import ThreadPoolExecutor

        with tempfile.TemporaryDirectory() as data:
            self.assertTrue(hasattr(growth_worker, "history"), "worker history must be queryable")
            with ThreadPoolExecutor(max_workers=4) as pool:
                list(pool.map(lambda _: growth_worker.run_once(data, "svc-test"), range(4)))
            self.assertEqual(4, len(growth_worker.history(data, "svc-test")))
            self.assertEqual(4, len({row["cycle_id"] for row in growth_worker.history(data, "svc-test")}))


if __name__ == "__main__":
    unittest.main()
