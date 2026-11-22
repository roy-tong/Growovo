from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from core import action_claims
from core.ledger import Ledger
from core.pipeline import WritePipeline
from core.throttle import Throttle

ROOT = Path(__file__).resolve().parents[1]


class ActionClaimTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = Path(self.tmp.name)

    def test_crash_after_claim_blocks_replay_as_indeterminate(self):
        key = action_claims.stable_key("email", "message", "person@example.com", "body", "sender")
        action_claims.claim(self.data, key=key)
        with self.assertRaises(action_claims.DuplicateAction) as caught:
            action_claims.claim(self.data, key=key)
        self.assertEqual("indeterminate", caught.exception.state)

    def test_two_processes_cannot_claim_the_same_action(self):
        key = action_claims.stable_key("x", "publish", "", "same body", "account")
        code = (
            "from core import action_claims; import sys; "
            "action_claims.claim(sys.argv[1], key=sys.argv[2]); print('claimed')"
        )
        env = {"PYTHONPATH": str(ROOT)}
        first = subprocess.run([sys.executable, "-c", code, str(self.data), key],
                               capture_output=True, text=True, env=env)
        second = subprocess.run([sys.executable, "-c", code, str(self.data), key],
                                capture_output=True, text=True, env=env)
        self.assertEqual(0, first.returncode, first.stderr)
        self.assertNotEqual(0, second.returncode)
        claims = action_claims.claims(self.data)
        self.assertEqual(1, len(claims))

    def test_pipeline_send_crash_cannot_send_twice(self):
        calls = []
        ledger = Ledger(self.data / "ledger.jsonl")

        class Gate:
            def check(self, **kwargs):
                return None

        def send(item):
            calls.append(item["id"])
            raise RuntimeError("connection lost after DATA")

        pipeline = WritePipeline(gate=Gate(), ledger=ledger,
                                 throttle=Throttle(self.data / "state"))
        item = {"id": "one-message"}
        for _ in range(2):
            rec = pipeline.run_one(
                item, channel="email", action="message", campaign="campaign",
                transport="smtp", expect="body", target="person@example.com",
                compose=lambda item: None, fill=lambda item: None,
                readback=lambda item: "body", send=send,
                confirm=lambda item: True, dry_run=False)
            self.assertEqual("indeterminate", rec["result"])
        self.assertEqual(["one-message"], calls)


if __name__ == "__main__":
    unittest.main()
