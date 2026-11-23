from __future__ import annotations

import copy
import importlib
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


NOW = datetime(2026, 11, 23, 20, 0, tzinfo=timezone(timedelta(hours=8)))


def valid_candidate():
    return {
        "schema": "growovo.review-candidate/1",
        "mode": "expert_review",
        "person": {
            "name": "Ada Lovelace", "target": "ada@example.com", "channel": "email",
            "account_type": "individual", "company": "Analytical Engines",
            "role": "", "department": "", "persona_hop": 1, "rotation": 0,
        },
        "hook": {
            "url": "https://github.com/example/project/pull/42#discussion-r1",
            "contribution": "availability versus influence distinction",
            "quote": "availability is not influence",
            "checked_at": "2026-11-23T08:00:00+08:00",
        },
        "artifact": {
            "ref": "healthcheck/tests/test_proof_export.py",
            "url": "https://github.com/roy-tong/Verovo/blob/main/healthcheck/tests/test_proof_export.py",
        },
        "feedback_examples": [
            "missing unknown state", "misleading level label", "counterexample fixture",
        ],
        "business_goal": {
            "objective": "review_material",
            "reader_value": "A fixture that separates observed evidence from causal claims.",
            "success_event": "One counterexample or field correction adopted in the repository.",
            "non_goal": "A signup, demo or sales call.",
        },
        "sender": {"name": "Roy Tong", "identity_url": "https://github.com/roy-tong"},
        "subject": "your availability distinction, one proof fixture later",
        "body": (
            "Hi Ada,\n\n"
            "Your availability versus influence distinction in DR-005 gave us a useful way "
            "to separate evidence from effect. "
            "Verovo now applies that boundary in its read-only proof exporter, beside the "
            "existing canonical finance checks. "
            "Would you review the proof-export fixture and tell me whether the P4 wording "
            "still claims too much? "
            "A missing unknown state or a misleading level label would be useful. "
            "A small counterexample fixture would also help.\n\n"
            "Roy Tong\nhttps://github.com/roy-tong"
        ),
    }


class ReviewOutreachTest(unittest.TestCase):
    def setUp(self):
        try:
            self.review = importlib.import_module("core.review_outreach")
        except ImportError:
            self.fail("core.review_outreach is not implemented")

    def evaluate(self, candidate, **kwargs):
        params = {"now": NOW, "contacts": [], "suppressions": set()}
        params.update(kwargs)
        return self.review.evaluate_candidate(candidate, **params)

    def test_eligible_expert_candidate_builds_native_approval_queue(self):
        candidate = valid_candidate()
        self.assertEqual([], self.evaluate(candidate))
        report = self.review.build_email_queue(
            [candidate], campaign="expert_review_v2", now=NOW,
            contacts=[], suppressions=set())
        self.assertEqual([], report["rejected"])
        queue = report["queue"]
        self.assertEqual("email", queue["channel"])
        self.assertEqual("message", queue["action"])
        self.assertEqual("expert_review_v2-001", queue["items"][0]["id"])
        self.assertEqual("healthcheck/tests/test_proof_export.py",
                         queue["items"][0]["review"]["artifact"]["ref"])
        self.assertTrue(report["human_approval_required"])
        self.assertEqual("none", report["external_action"])

    def test_stale_hook_and_cross_campaign_recipient_cooldown_are_rejected(self):
        stale = valid_candidate()
        stale["hook"]["checked_at"] = "2026-11-22T23:30:00+08:00"
        self.assertIn("hook_not_checked_today", self.evaluate(stale))

        contacted = valid_candidate()
        contacts = [{
            "target": "ada@example.com", "company": "Other Co",
            "campaign": "another_track", "sent_at": (NOW - timedelta(days=15)).isoformat(),
        }]
        self.assertIn("recipient_cooldown_30d", self.evaluate(contacted, contacts=contacts))

    def test_company_account_requires_person_and_company_spacing(self):
        generic = valid_candidate()
        generic["person"].update({
            "target": "contact@example.com", "account_type": "company",
            "role": "Support Operations Lead", "department": "Support",
            "persona_hop": 1, "rotation": 1,
        })
        self.assertIn("company_account_requires_person", self.evaluate(generic))

        other = valid_candidate()
        other["person"].update({
            "target": "ada@example.com", "account_type": "company",
            "role": "Support Operations Lead", "department": "Support",
            "persona_hop": 1, "rotation": 1,
        })
        contacts = [{
            "target": "other@example.com", "company": "Analytical Engines",
            "campaign": "other", "sent_at": (NOW - timedelta(days=2)).isoformat(),
        }]
        self.assertIn("company_cooldown_3d", self.evaluate(other, contacts=contacts))

    def test_expert_content_requires_specific_artifact_examples_and_one_question(self):
        broad = valid_candidate()
        broad["artifact"]["ref"] = ""
        self.assertIn("artifact_too_broad", self.evaluate(broad))

        thin = valid_candidate()
        thin["feedback_examples"] = ["missing unknown state"]
        self.assertIn("feedback_examples_missing", self.evaluate(thin))

        no_question = valid_candidate()
        no_question["body"] = no_question["body"].replace("still claims too much?", "still claims too much.")
        self.assertIn("question_count", self.evaluate(no_question))

        template = valid_candidate()
        template["body"] = "I saw your work. " + template["body"]
        self.assertIn("anti_template", self.evaluate(template))

    def test_suppression_and_duplicate_body_fail_closed(self):
        suppressed = valid_candidate()
        self.assertIn("suppressed", self.evaluate(suppressed, suppressions={"ada@example.com"}))
        first, second = valid_candidate(), valid_candidate()
        second["person"]["target"] = "other@example.com"
        report = self.review.build_email_queue(
            [first, second], campaign="expert_review_v2", now=NOW,
            contacts=[], suppressions=set())
        self.assertEqual(1, len(report["queue"]["items"]))
        self.assertEqual("duplicate_body", report["rejected"][0]["issues"][0])

    def test_github_review_candidate_builds_native_reply_queue_without_email_signature(self):
        candidate = valid_candidate()
        candidate["person"].update({
            "name": "Gunjan Jaswal", "channel": "github",
            "target": "issue:roy-tong/Verovo#11",
        })
        candidate["artifact"] = {
            "ref": "healthcheck/verovo_healthcheck/proof_export.py",
            "url": "https://github.com/roy-tong/Verovo/blob/main/healthcheck/verovo_healthcheck/proof_export.py",
        }
        candidate["body"] = (
            "Gunjan, your spec-version drift guard in PR 38 kept a prose specification "
            "and a conformance runner from silently diverging. "
            "The read-only proof exporter now separates observed state, professional review, "
            "and external outcomes in a similar checkable form. "
            "Would you review the P4 check at "
            "https://github.com/roy-tong/Verovo/blob/main/healthcheck/verovo_healthcheck/proof_export.py "
            "and say whether its external-result wording preserves your availability versus "
            "influence boundary? "
            "A missing unknown state or a misleading level label would be useful. "
            "A small counterexample fixture would also help."
        )
        self.assertEqual([], self.evaluate(candidate))
        report = self.review.build_github_queue(
            [candidate], campaign="expert_review_v2_github", now=NOW,
            contacts=[], suppressions=set())
        self.assertEqual([], report["rejected"])
        self.assertEqual("github", report["queue"]["channel"])
        self.assertEqual("reply", report["queue"]["action"])
        self.assertEqual("issue:roy-tong/Verovo#11", report["queue"]["items"][0]["target"])
        self.assertTrue(report["human_approval_required"])

    def test_business_goal_and_low_marketing_cta_are_admission_gates(self):
        missing = valid_candidate()
        del missing["business_goal"]
        self.assertIn("business_goal_missing", self.evaluate(missing))

        volume = valid_candidate()
        volume["business_goal"]["objective"] = "lead_volume"
        self.assertIn("business_goal_invalid", self.evaluate(volume))

        pitch = valid_candidate()
        pitch["body"] += " Book a demo and sign up today."
        self.assertIn("marketing_cta", self.evaluate(pitch))

        goal = valid_candidate()
        report = self.review.build_email_queue(
            [goal], campaign="expert_review_v2", now=NOW,
            contacts=[], suppressions=set())
        self.assertEqual("review_material",
                         goal["business_goal"]["objective"])
        self.assertEqual("review_material",
                         report["queue"]["items"][0]["review"]["business_goal"]["objective"])
        self.assertEqual(3, report["capacity_per_channel_per_wave"])

    def test_fourth_candidate_in_same_channel_wave_is_rejected(self):
        candidates = []
        for index in range(4):
            candidate = valid_candidate()
            candidate["person"]["target"] = "person%d@example.com" % index
            candidate["body"] = candidate["body"].replace(
                "A small counterexample fixture would also help.",
                "A small counterexample fixture would also help for case %d." % index)
            candidates.append(candidate)
        report = self.review.build_email_queue(
            candidates, campaign="expert_review_v2", now=NOW,
            contacts=[], suppressions=set())
        self.assertEqual(3, len(report["queue"]["items"]))
        self.assertEqual(["wave_channel_cap_3"], report["rejected"][0]["issues"])

    def test_legacy_history_is_normalized_for_cross_campaign_cooldown(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "sent_log.txt").write_text(
                "expert_review_v1:NEW:abc|ada@example.com|2026-10-01\n"
                "ada@example.com|expert_review_v1:NEW|2026-10-02\n",
                encoding="utf-8")
            rows = self.review.load_legacy_history(root)
        self.assertEqual(2, len(rows))
        self.assertTrue(all(row["target"] == "ada@example.com" for row in rows))
        self.assertTrue(all(row["campaign"] == "expert_review_v1" for row in rows))

    def test_feedback_projection_requires_named_review_and_never_writes_repository(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            base = {
                "reply_case_id": "rc-test", "version": 1,
                "source_ref": "synthetic:message:1", "raw_text": "Please add a missing unknown state.",
                "target": "private@example.com", "run_id": "expert_review_v2",
                "classification_candidate": "interested", "review_state": "proposed",
                "human_label": "", "reviewer": "", "reviewed_at": "",
                "received_at": NOW.isoformat(),
            }
            path = data / "replies" / "reply-cases.jsonl"
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps(base) + "\n", encoding="utf-8")
            pending = self.review.sync_feedback(data, now=NOW)
            self.assertEqual("human_review_required", pending["skipped"][0]["reason"])
            self.assertEqual([], pending["written"])

            confirmed = dict(base, version=2, review_state="confirmed",
                             human_label="interested", reviewer="Roy",
                             reviewed_at=NOW.isoformat())
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(confirmed) + "\n")
            result = self.review.sync_feedback(data, now=NOW)
            self.assertEqual(1, len(result["written"]))
            record_path = Path(result["written"][0])
            record = json.loads(record_path.read_text(encoding="utf-8"))
            self.assertTrue(record["private"])
            self.assertTrue(record["sanitization_required"])
            self.assertEqual("not_performed", record["repository_write"])
            draft = record_path.with_suffix(".md").read_text(encoding="utf-8")
            self.assertIn("PRIVATE DRAFT", draft)
            self.assertNotIn("private@example.com", draft)
            self.assertTrue(str(record_path).startswith(str(data)))
            repeated = self.review.sync_feedback(data, now=NOW + timedelta(hours=1))
            self.assertEqual([], repeated["written"])
            self.assertEqual([str(record_path)], repeated["unchanged"])


if __name__ == "__main__":
    unittest.main()
