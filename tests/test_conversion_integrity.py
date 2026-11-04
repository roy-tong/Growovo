"""R0 deal integrity: cohort funnels, append-only events, and evidence gates."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core import conversion


PASSING_QUALIFICATION = {"trigger": True, "contactable": True, "data": True}


class DealIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "deals.jsonl"
        self.deals = conversion.Deals(self.path)

    def _engaged(self, contact_id="c1"):
        deal = self.deals.add(contact_id, name=contact_id, campaign="integrity")
        deal = self.deals.advance(deal["id"], "contacted", "test", evidence="message-url")
        return self.deals.advance(deal["id"], "engaged", "test", evidence="reply-url")

    def _qualified(self, contact_id="c1"):
        deal = self._engaged(contact_id)
        self.deals.qualify(deal["id"], PASSING_QUALIFICATION)
        return self.deals.advance(deal["id"], "qualified", "test", evidence="qualification-review")

    def _readback(self, contact_id="c1"):
        deal = self._qualified(contact_id)
        for stage in ("sample_offered", "sample_received", "sample_delivered", "readback"):
            deal = self.deals.advance(deal["id"], stage, "test", evidence=f"evidence-{stage}")
        return deal

    def _sow_sent(self, contact_id="c1"):
        deal = self._readback(contact_id)
        for stage in ("quoted", "sow_sent"):
            deal = self.deals.advance(deal["id"], stage, "test", evidence=f"evidence-{stage}")
        return deal

    def test_cohort_rates_survive_forward_progress_and_paid_uses_history(self):
        deals = [self.deals.add(f"c{i}", campaign="integrity", on="2026-11-01")
                 for i in range(10)]
        for deal in deals[:5]:
            self.deals.advance(deal["id"], "contacted", "test", evidence="contact", on="2026-11-02")
            self.deals.advance(deal["id"], "engaged", "test", evidence="reply", on="2026-11-03")
        for deal in deals[:4]:
            self.deals.qualify(deal["id"], PASSING_QUALIFICATION)
            self.deals.advance(deal["id"], "qualified", "test", evidence="review", on="2026-11-04")
            # Current qualified stock becomes zero, but the qualified cohort stays four.
            self.deals.advance(deal["id"], "sample_offered", "test", evidence="scope", on="2026-11-05")

        funnel = self.deals.funnel(on="2026-11-05")
        self.assertEqual(10, funnel["cohort_counts"]["sourced"])
        self.assertEqual(5, funnel["cohort_counts"]["engaged"])
        self.assertEqual(4, funnel["cohort_counts"]["qualified"])
        self.assertEqual(5, funnel["current_counts"]["sourced"])
        self.assertEqual(1, funnel["current_counts"]["engaged"])
        self.assertEqual(0, funnel["current_counts"]["qualified"])
        self.assertEqual(funnel["counts"], funnel["current_counts"])
        # Five of ten sourced deals passed through contacted; four of five engaged
        # deals passed qualification. Continued progress does not change either cohort.
        self.assertEqual(0.5, funnel["rates"]["sourced->contacted"])
        self.assertEqual(0.8, funnel["rates"]["engaged->qualified"])
        self.assertIn("当前阶段存量", funnel["counts_note"])
        self.assertIn("history", funnel["rates_note"])

        first = deals[0]
        for stage in ("sample_received", "sample_delivered", "readback", "quoted",
                      "sow_sent", "paid"):
            first = self.deals.advance(first["id"], stage, "test", evidence=f"evidence-{stage}")
        self.assertEqual(0.1, self.deals.funnel()["overall_paid_rate"])
        first = self.deals.advance(first["id"], "delivered", "test", evidence="signed-readback")
        funnel = self.deals.funnel()
        self.assertEqual(0, funnel["current_counts"]["paid"])
        self.assertEqual(1, funnel["current_counts"]["delivered"])
        self.assertEqual(1, funnel["cohort_counts"]["paid"])
        self.assertEqual(0.1, funnel["overall_paid_rate"])
        self.assertEqual(0.5, funnel["rates"]["sourced->contacted"])
        self.assertEqual(0.8, funnel["rates"]["engaged->qualified"])

    def test_zero_cohort_denominators_are_none(self):
        funnel = self.deals.funnel()
        self.assertIsNone(funnel["overall_paid_rate"])
        self.assertTrue(funnel["rates"])
        self.assertTrue(all(rate is None for rate in funnel["rates"].values()))

    def test_add_advance_qualify_append_events_without_rewriting_prefix(self):
        first = self.deals.add("c1", campaign="integrity")
        after_add = self.path.read_bytes()
        with mock.patch.object(Path, "write_text",
                               side_effect=AssertionError("whole-file rewrite is forbidden")):
            second = self.deals.add("c2", campaign="integrity")
            after_second_add = self.path.read_bytes()
            self.assertTrue(after_second_add.startswith(after_add))
            self.deals.advance(first["id"], "contacted", "test", evidence="message-url")
            after_advance = self.path.read_bytes()
            self.assertTrue(after_advance.startswith(after_second_add))
            self.deals.qualify(first["id"], PASSING_QUALIFICATION)
            after_qualify = self.path.read_bytes()
            self.assertTrue(after_qualify.startswith(after_advance))

        lines = self.path.read_text(encoding="utf-8").splitlines()
        self.assertEqual(4, len(lines))
        self.assertTrue(all(json.loads(line)["kind"] == conversion.DEAL_EVENT_KIND for line in lines))
        self.assertEqual(2, len(self.deals.all()))
        self.assertEqual("contacted", self.deals.get(first["id"])["stage"])
        self.assertTrue(self.deals.get(first["id"])["qualified"])
        self.assertEqual("sourced", self.deals.get(second["id"])["stage"])

    def test_legacy_current_rows_are_read_and_updated_by_append(self):
        legacy = {
            "id": "deal-0001", "contact_id": "old", "name": "Legacy",
            "campaign": "old", "owner": "roy", "stage": "sourced",
            "offer": "", "value": 0, "currency": "USD", "qualification": {},
            "qualified": False, "created": "2026-10-01", "updated": "2026-10-01",
            "next_due": "2026-10-02",
            "history": [{"at": "2026-10-01", "stage": "sourced", "by": "roy",
                         "evidence": "", "note": "legacy"}],
        }
        original = json.dumps(legacy, ensure_ascii=False) + "\n"
        self.path.write_text(original, encoding="utf-8")
        self.assertEqual([legacy], self.deals.all())
        self.deals.advance("deal-0001", "contacted", "test", evidence="legacy-message")
        raw = self.path.read_text(encoding="utf-8")
        self.assertTrue(raw.startswith(original))
        self.assertEqual(2, len(raw.splitlines()))
        current = self.deals.get("deal-0001")
        self.assertEqual("contacted", current["stage"])
        self.assertEqual(["sourced", "contacted"], [h["stage"] for h in current["history"]])

    def test_every_advance_target_requires_nonempty_evidence(self):
        previous = {}
        for source, targets in conversion.TRANSITIONS.items():
            for target in targets:
                previous.setdefault(target, source)
        for index, (target, source) in enumerate(previous.items()):
            with self.subTest(target=target):
                deal = self.deals.add(f"c{index}", campaign="integrity", stage=source,
                                      migration=source != "sourced")
                with self.assertRaises(conversion.ConversionError) as ctx:
                    self.deals.advance(deal["id"], target, "test", evidence="  \n")
                self.assertIn("evidence", str(ctx.exception))

    def test_add_requires_sourced_unless_explicit_migration(self):
        with self.assertRaises(conversion.ConversionError):
            self.deals.add("c1", campaign="integrity", stage="engaged")
        self.assertFalse(self.path.exists())
        migrated = self.deals.add("c1", campaign="integrity", stage="engaged", migration=True)
        self.assertEqual("engaged", migrated["stage"])
        self.assertTrue(migrated["history"][0]["migration"])

    def test_qualified_requires_a_passing_qualification_record(self):
        deal = self._engaged()
        with self.assertRaises(conversion.ConversionError):
            self.deals.advance(deal["id"], "qualified", "test", evidence="review")
        self.deals.qualify(deal["id"], {"trigger": True, "contactable": True})
        with self.assertRaises(conversion.ConversionError):
            self.deals.advance(deal["id"], "qualified", "test", evidence="review")
        self.deals.qualify(deal["id"], PASSING_QUALIFICATION)
        advanced = self.deals.advance(deal["id"], "qualified", "test", evidence="review")
        self.assertEqual("qualified", advanced["stage"])
        self.assertTrue(self.deals.get(deal["id"])["qualified"])

    def test_quote_is_allowed_only_at_current_readback(self):
        with self.assertRaises(conversion.ConversionError):
            conversion.quote({"id": "d", "stage": "engaged"})
        deal = self._readback()
        self.assertEqual(1500, conversion.quote(deal)["offer"]["price"])
        deal = self.deals.advance(deal["id"], "quoted", "test", evidence="quote-url")
        with self.assertRaises(conversion.ConversionError):
            conversion.quote(deal)

    def test_paid_accepts_evidence_and_validates_optional_verification_pair(self):
        deal = self._sow_sent()
        with self.assertRaises(conversion.ConversionError):
            self.deals.advance(deal["id"], "paid", "test", evidence="")
        with self.assertRaises(conversion.ConversionError):
            self.deals.advance(deal["id"], "paid", "test", evidence="bank-receipt",
                               verification_method="bank_api")
        paid = self.deals.advance(deal["id"], "paid", "test", evidence="bank-receipt",
                                  verification_method="bank_api", verification_ref="payout-42")
        self.assertEqual("bank_api", paid["history"][-1]["verification_method"])
        self.assertEqual("payout-42", self.deals.get(deal["id"])["history"][-1]["verification_ref"])

        # Existing callers that only have evidence remain valid; verification is
        # strongly checked when supplied rather than making every old call fail.
        other = self._sow_sent("c2")
        paid = self.deals.advance(other["id"], "paid", "test", evidence="legacy-bank-receipt")
        self.assertEqual("paid", paid["stage"])


if __name__ == "__main__":
    unittest.main()
