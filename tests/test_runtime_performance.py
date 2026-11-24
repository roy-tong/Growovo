import tempfile
import threading
import time
import unittest
from unittest import mock

from core import github_app, gmail_api, growth_runtime
from tests.test_gmail_api import message


class CursorWrapper:
    def __init__(self, cursor):
        self.cursor = cursor

    def _convert(self, row):
        if row is None or isinstance(row, dict):
            return row
        names = [item[0] for item in self.cursor.description]
        return dict(zip(names, row))

    def fetchone(self):
        return self._convert(self.cursor.fetchone())

    def fetchall(self):
        return [self._convert(row) for row in self.cursor.fetchall()]


class ConnectionWrapper:
    def __init__(self, connection, calls):
        self.connection = connection
        self.calls = calls

    def __getattr__(self, name):
        return getattr(self.connection, name)

    def execute(self, *args):
        return CursorWrapper(self.connection.execute(*args))

    def executescript(self, script):
        self.calls.append(script)
        return self.connection.executescript(script)


class RuntimePerformanceTest(unittest.TestCase):
    def test_schema_initialization_runs_once_per_data_directory(self):
        with tempfile.TemporaryDirectory() as data:
            calls = []
            original = growth_runtime.sqlite3.connect
            with mock.patch("core.growth_runtime.sqlite3.connect",
                            side_effect=lambda *args, **kwargs: ConnectionWrapper(
                                original(*args, **kwargs), calls)):
                growth_runtime.load_engagement(data, "svc-test")
                growth_runtime.load_engagement(data, "svc-test")
            self.assertEqual(1, len(calls))

    def test_gmail_full_sync_uses_bounded_parallel_reads(self):
        messages = {f"m{index}": message(f"m{index}", f"thread-{index}", f"question {index}")
                    for index in range(8)}
        current = [0]
        maximum = [0]
        lock = threading.Lock()

        def transport(method, path, token=None, params=None, payload=None):
            if path == "/gmail/v1/users/me/messages":
                return {"messages": [{"id": f"m{index}"} for index in range(8)]}
            with lock:
                current[0] += 1
                maximum[0] = max(maximum[0], current[0])
            time.sleep(0.03)
            with lock:
                current[0] -= 1
            return messages[path.rsplit("/", 1)[-1]]

        client = gmail_api.GmailClient(access_token="token", transport=transport)
        result = client.full_sync()
        self.assertEqual(8, len(result["messages"]))
        self.assertGreaterEqual(maximum[0], 2)
        self.assertLessEqual(maximum[0], 4)

    def test_github_comment_reads_use_bounded_parallelism(self):
        issues = [{"number": index, "title": f"Issue {index}", "body": "question",
                   "state": "open", "created_at": "2026-11-24T00:00:00Z",
                   "updated_at": "2026-11-24T00:00:00Z",
                   "html_url": f"https://github.com/o/r/issues/{index}",
                   "user": {"id": 1, "login": "alice"}, "comments": 1} for index in range(1, 9)]
        current = [0]
        maximum = [0]
        lock = threading.Lock()

        def transport(method, path, token=None, payload=None):
            if path.startswith("/repos/o/r/issues?"):
                return issues
            if path.endswith("page=2"):
                return []
            with lock:
                current[0] += 1
                maximum[0] = max(maximum[0], current[0])
            time.sleep(0.03)
            with lock:
                current[0] -= 1
            number = int(path.split("/")[-2])
            return [{"id": number, "body": "comment", "created_at": "2026-11-24T00:00:00Z",
                     "html_url": f"https://github.com/o/r/issues/{number}#issuecomment-{number}",
                     "user": {"id": 2, "login": "bob"}}]

        with mock.patch.dict("os.environ", {"MKT_GITHUB_APP_INSTALLATION_TOKEN": "token"}):
            rows = github_app.GitHubAppClient(transport=transport).sync_issue_interactions("o/r")
        self.assertEqual(16, len(rows))
        self.assertGreaterEqual(maximum[0], 2)
        self.assertLessEqual(maximum[0], 4)


if __name__ == "__main__":
    unittest.main()
