#!/usr/bin/env python3
import json
import tempfile
import unittest
from pathlib import Path

from core import projections
from core.ledger import Ledger
from core.runtime import RunContext
from core.storage import AppendOnlyJSONL


class ProjectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = Path(self.tmp.name)

    def test_missing_business_projection_is_deterministic_unknown(self):
        first = projections.ProjectionRegistry.build("business", self.data, night="2026-11-23")
        second = projections.ProjectionRegistry.build("business", self.data, night="2026-11-23")
        self.assertEqual(first, second)
        self.assertEqual("not_included", first["body"]["status"])
        self.assertEqual(64, len(first["projection_sha256"]))

    def test_wave_projection_rebuilds_same_bytes(self):
        directory = self.data / "state" / "engagement-2026-11-23"
        directory.mkdir(parents=True)
        (directory / "wave-01-1905.json").write_text(json.dumps({
            "ok": True, "wave": "01-1905", "campaign": "c", "dry": False,
            "planned": 1, "supply": {"x/publish": {"effective": 1, "scheduled": 1}},
            "results": [{"channel": "x", "action": "publish", "result": "committed",
                         "ledger_result": "committed", "ref": "r1"}],
            "inbox": {}, "content": {},
        }), encoding="utf-8")
        one = projections.ProjectionRegistry.build("wave", self.data, night="2026-11-23")
        two = projections.ProjectionRegistry.build("wave", self.data, night="2026-11-23")
        self.assertEqual(projections.canonical(one), projections.canonical(two))
        self.assertEqual(1, one["body"]["totals"]["committed"])

    def test_run_correlation_does_not_invent_legacy_ids(self):
        context = RunContext.create(self.data, "c", "w")
        operations = AppendOnlyJSONL(self.data / "operations.jsonl")
        operations.append({"attempt_id": context.attempt_id, "action_id": "a1", "result": "committed"})
        Ledger(self.data / "ledger.jsonl").append(channel="x", action="reply", target="t",
                                                  result="committed", campaign="c", ref="r1")
        got = projections.correlation_report(self.data, context)
        self.assertEqual(1, got["counts"]["operations"])
        self.assertEqual(1, got["counts"]["ledger"])
        self.assertEqual(1, got["counts"]["legacy_unlinked"])
        self.assertEqual(["a1"], got["action_ids"])
        self.assertIn("legacy ids are not fabricated", got["note"])

    def test_unknown_projection_is_rejected(self):
        with self.assertRaises(ValueError):
            projections.ProjectionRegistry.build("unknown", self.data)


if __name__ == "__main__":
    unittest.main()
