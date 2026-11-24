import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

from core import growth_engagement, growth_experiments


def interaction(comment_id):
    return {"source": "github_app", "external_id": f"issue-comment-{comment_id}",
            "conversation_key": "o/r#7", "author": "bob", "body": f"question {comment_id}",
            "received_at": "2026-11-24T00:00:00Z", "repository": "o/r"}


class GrowthDomainStateTest(unittest.TestCase):
    def test_concurrent_engagement_imports_keep_all_interactions(self):
        with tempfile.TemporaryDirectory() as data:
            original_load = growth_engagement._load
            release = threading.Event()
            lock = threading.Lock()
            calls = [0]

            def slowed_load(*args):
                value = original_load(*args)
                with lock:
                    calls[0] += 1
                    current = calls[0]
                if current <= 2:
                    release.wait(timeout=1)
                return value

            with mock.patch("core.growth_engagement._load", side_effect=slowed_load):
                with ThreadPoolExecutor(max_workers=2) as pool:
                    tasks = [pool.submit(growth_engagement.import_interactions, data, "svc-test", [row])
                             for row in (interaction(1), interaction(2))]
                    time.sleep(0.2)
                    release.set()
                    for task in tasks:
                        task.result()
            rows = growth_engagement.list_conversations(data, "svc-test")
            self.assertEqual(1, len(rows))
            self.assertEqual({"issue-comment-1", "issue-comment-2"}, set(rows[0]["interaction_ids"]))

    def test_sqlite_remains_authoritative_without_compatibility_json(self):
        with tempfile.TemporaryDirectory() as data:
            growth_engagement.import_interactions(data, "svc-test", [interaction(1)])
            growth_engagement.list_conversations(data, "svc-test")
            path = Path(data) / "state" / "growth-engagement" / "svc-test.json"
            self.assertTrue(path.is_file())
            path.unlink()
            rows = growth_engagement.list_conversations(data, "svc-test")
            self.assertEqual(1, len(rows))
            self.assertTrue(path.is_file())

    def test_concurrent_experiment_observations_keep_every_event(self):
        with tempfile.TemporaryDirectory() as data:
            spec = {"id": "exp-1", "hypothesis": "h", "primary_metric": "activation", "target": 1,
                    "window": {"start": "2026-11-24T00:00:00Z", "end": "2026-11-25T00:00:00Z"},
                    "stop_rule": "stop"}
            growth_experiments.create(data, "svc-test", spec)
            events = [{"id": f"event-{index}", "type": "activation", "subject": f"user-{index}", "source": "test",
                       "at": "2026-11-24T00:00:00Z"} for index in range(2)]
            with ThreadPoolExecutor(max_workers=2) as pool:
                list(pool.map(lambda event: growth_experiments.observe(data, "svc-test", "exp-1", [event]), events))
            result = growth_experiments.diagnose(data, "svc-test", "exp-1")
            self.assertEqual(2, result["primary_count"])


if __name__ == "__main__":
    unittest.main()
