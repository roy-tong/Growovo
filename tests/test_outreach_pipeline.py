#!/usr/bin/env python3
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from core.contribution import ContributionStore
from core.content_pipeline import prepare_draft
from core.outreach import AccountMap, HookEvidence, OutreachError
import mkt

TODAY = "2026-11-23"
BODY = ("Your retry fixture showed how repeated rendering can hide a second billable event. "
        "I am Roy Tong, building a small open validator for agent receipts, and it complements "
        "the tracing work in your repository. Would you read fixtures/retry.json and tell me "
        "whether the missing attempt id is the first boundary you would correct? A missing field, "
        "an unclear verdict, or one representative trace would all be useful. My identity and "
        "project are at https://github.com/roy-tong, and the fixture keeps the attempt count at 2.")


def account():
    return AccountMap(handle="alice", account_type="company", company_id="acme",
                      person_id="alice-1", department="Eng", persona_rank=1,
                      evidence=["https://example.com/alice"])


def hook():
    return HookEvidence(source_url="https://example.com/retry", quote="retry fixture",
                        subject_ref="alice-1", observed_at=TODAY + "T08:00:00+08:00",
                        verified_at=TODAY + "T09:00:00+08:00")


def request():
    return {"mode": "expert", "account": account().__dict__, "hook": hook().__dict__,
            "source_text": BODY, "body": BODY, "subject": "your retry fixture",
            "artifact_ref": "fixtures/retry.json", "feedback_examples": ["missing field", "trace"],
            "personalization_token": "retry fixture", "cta_level": 2,
            "sender_name": "Roy Tong", "identity_link": "https://github.com/roy-tong",
            "facts": ["2"], "disclosures": [],
            "voice_profile": {"source_version": "my-voice/2026-11-23", "scene": "expert"}}


class OutreachTests(unittest.TestCase):
    def test_company_requires_person_and_replacement_is_bounded(self):
        with self.assertRaises(OutreachError):
            AccountMap(handle="acme", account_type="company", company_id="acme",
                       evidence=["https://example.com"]).validate()
        account().validate()
        self.assertEqual(2, account().next_replacement()["persona_rank"])
        exhausted = AccountMap(**dict(account().__dict__, company_rounds=3))
        self.assertFalse(exhausted.next_replacement()["allowed"])

    def test_hook_requires_same_day_verification(self):
        self.assertTrue(hook().fresh(TODAY))
        stale = HookEvidence(**dict(hook().__dict__, verified_at="2026-11-22T09:00:00+08:00"))
        self.assertFalse(stale.fresh(TODAY))

    def test_expert_draft_passes_only_with_artifact_examples_and_preserved_facts(self):
        got = prepare_draft(request(), today=TODAY)
        self.assertTrue(got["ok"])
        self.assertEqual("expert", got["draft"]["mode"])
        self.assertEqual(64, len(got["draft"]["body_sha256"]))
        missing = request()
        missing["artifact_ref"] = ""
        self.assertIn("artifact_required", prepare_draft(missing, today=TODAY)["errors"])
        lost_fact = request()
        lost_fact["body"] = BODY.replace("at 2", "at 3")
        self.assertIn("facts_not_preserved", prepare_draft(lost_fact, today=TODAY)["errors"])

    def test_stale_hook_and_template_openers_fail_before_draft_acceptance(self):
        stale = request()
        stale["hook"] = dict(stale["hook"], verified_at="2026-11-22T09:00:00+08:00")
        self.assertIn("stale_hook", prepare_draft(stale, today=TODAY)["errors"])
        generic = request()
        generic["body"] = "I saw your work. " + BODY
        self.assertIn("anti_template", prepare_draft(generic, today=TODAY)["errors"])

    def test_voice_scene_is_metadata_not_a_global_rewrite(self):
        gtm = request()
        gtm["voice_profile"] = {"source_version": "my-voice/2026-11-23", "scene": "gtm"}
        got = prepare_draft(gtm, today=TODAY)
        self.assertEqual("gtm", got["voice_scene"])
        self.assertEqual(BODY, got["draft"]["body"])


class ContributionTests(unittest.TestCase):
    def test_contribution_requires_review_and_change_reference(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ContributionStore(Path(tmp))
            got = store.record("reply-1", kind="material", by="roy", apply=True)
            cid = got["contribution"]["contribution_id"]
            store.transition(cid, "triaged", by="roy", note="valid fixture", apply=True)
            store.transition(cid, "patch_proposed", by="roy", change_ref="patch-1", apply=True)
            with self.assertRaises(ValueError):
                store.transition(cid, "merged", by="roy", apply=True)
            store.transition(cid, "reviewed", by="reviewer", change_ref="patch-1", apply=True)
            merged = store.transition(cid, "merged", by="roy", change_ref="commit:abc", apply=True)
            self.assertEqual("merged", merged["contribution"]["status"])
            self.assertEqual("commit:abc", merged["contribution"]["change_ref"])

    def test_cli_feedback_defaults_to_dry_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            source = data / "feedback.json"
            source.write_text(json.dumps({"interaction_id": "reply-1", "kind": "material", "by": "roy"}),
                              encoding="utf-8")
            output = io.StringIO()
            with mock.patch.object(mkt, "DATA", data), contextlib.redirect_stdout(output):
                code = mkt.cmd_outreach(SimpleNamespace(action="feedback", file=str(source), apply=False))
            self.assertEqual(0, code)
            self.assertFalse(json.loads(output.getvalue())["apply"])
            self.assertFalse((data / "state" / "contributions.jsonl").exists())


if __name__ == "__main__":
    unittest.main()
