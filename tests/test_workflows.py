#!/usr/bin/env python3
"""Workflow: locator ladder, approval fingerprint, guarded replay and the run journal."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core import locators, workflows  # noqa: E402

MKT = ROOT / "mkt.py"


class FakeLocator:
    def __init__(self, page, element=None, label=""):
        self.page = page
        self.element = element
        self.label = label
        self.value = ""

    @property
    def first(self):
        return self

    def is_visible(self):
        return bool(self.element and self.element.get("visible", True))

    def fill(self, value):
        if not self.is_visible():
            raise RuntimeError("not visible")
        self.value = value
        self.element["value"] = value
        self.page.fills.append((self.label, value))

    def input_value(self):
        return self.value if self.element is not None else ""

    def click(self):
        if not self.is_visible():
            raise RuntimeError("not visible")
        self.page.clicks.append(self.label)

    def press(self, key):
        self.page.presses.append((self.label, key))

    def inner_text(self):
        return str((self.element or {}).get("text") or self.value)


class FakePage:
    def __init__(self, elements):
        self.elements = [dict(item) for item in elements]
        self.url = "about:blank"
        self.fills = []
        self.clicks = []
        self.presses = []

    def goto(self, url):
        self.url = url

    def _match(self, **criteria):
        found = []
        for element in self.elements:
            if all(str(element.get(key, "")).lower() == str(value).lower()
                   for key, value in criteria.items() if value is not None):
                found.append(element)
        return found

    def locator(self, css):
        matches = self._match(css=css)
        return FakeLocator(self, matches[0] if matches else None, css)

    def get_by_role(self, role, name=None):
        matches = self._match(role=role, name=name)
        return FakeLocator(self, matches[0] if matches else None, f"role:{role}")

    def get_by_label(self, value):
        matches = self._match(label=value)
        return FakeLocator(self, matches[0] if matches else None, f"label:{value}")

    def get_by_placeholder(self, value):
        matches = self._match(placeholder=value)
        return FakeLocator(self, matches[0] if matches else None, f"placeholder:{value}")

    def get_by_text(self, value):
        matches = self._match(text=value)
        return FakeLocator(self, matches[0] if matches else None, f"text:{value}")


def make_workflow(**over):
    workflow = {
        "id": "hn-reply",
        "name": "HN reply",
        "version": 1,
        "channel": "hn",
        "action": "reply",
        "steps": [
            {"id": "s1", "kind": "open", "intent": "open item",
             "url": "https://news.ycombinator.com/item?id={{item}}"},
            {"id": "s2", "kind": "fill", "intent": "type reply", "value": "{{text}}",
             "locators": [
                 {"strategy": "css", "value": "textarea[name=text]"},
                 {"strategy": "role", "role": "textbox", "name": "Comment"},
             ],
             "verify": {"readback": "{{text}}"}},
            {"id": "s3", "kind": "click", "intent": "submit",
             "locators": [{"strategy": "css", "value": "button[type=submit]"}]},
        ],
        "exceptions": [{"match": "login", "branch": "stop", "reason": "login wall"}],
        "recovery": {"ladder": list(locators.STRATEGIES), "visual": {"provider": "", "enabled": False}},
    }
    workflow.update(over)
    return workflows.normalize(workflow)


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.data = Path(tmp.name) / "data"
        self.data.mkdir()

    def test_ladder_recovers_with_a_semantic_locator(self):
        page = FakePage([
            {"role": "textbox", "name": "Comment", "text": "", "visible": True},
        ])
        resolved = locators.resolve(page, [
            {"strategy": "css", "value": "textarea[name=text]"},
            {"strategy": "role", "role": "textbox", "name": "Comment"},
        ])
        self.assertEqual("role", resolved["strategy"])
        self.assertEqual("missing_or_hidden", resolved["tried"][0]["result"])

    def test_ladder_stops_when_visual_is_not_configured(self):
        page = FakePage([])
        with self.assertRaises(locators.LocatorUnresolved) as caught:
            locators.resolve(page, [
                {"strategy": "css", "value": "#gone"},
                {"strategy": "visual", "value": "submit button"},
            ])
        self.assertFalse(caught.exception.visual_configured)
        self.assertIn("visual_unavailable", str(caught.exception))

    def test_visual_provider_is_used_when_supplied(self):
        page = FakePage([{"css": ".visual", "text": "submit"}])
        provider = lambda page, spec: page.locator(".visual")  # noqa: E731
        resolved = locators.resolve(page, [{"strategy": "visual", "value": "submit"}], visual=provider)
        self.assertEqual("visual", resolved["strategy"])

    def test_lint_rejects_unknown_strategy_and_step(self):
        workflow = make_workflow()
        workflow["steps"].append({"id": "s4", "kind": "teleport"})
        workflow["steps"].append({"id": "s5", "kind": "click",
                                  "locators": [{"strategy": "xpath", "value": "//div"}]})
        errors = workflows.lint(workflow)
        self.assertTrue(any("unknown kind" in error for error in errors))
        self.assertTrue(any("xpath" in error for error in errors))

    def test_learn_from_trace_builds_an_unapproved_workflow(self):
        trace = {
            "channel": "hn", "action": "reply",
            "steps": [
                {"intent": "open item", "kind": "open",
                 "url": "https://news.ycombinator.com/item?id={{item}}"},
                {"intent": "type reply", "kind": "fill", "value": "{{text}}",
                 "locators": [{"strategy": "role", "role": "textbox", "name": "Comment"},
                              {"strategy": "css", "value": "textarea"}]},
            ],
        }
        workflow = workflows.learn_from_trace(trace, workflow_id="hn-reply-learned")
        self.assertEqual(2, len(workflow["steps"]))
        self.assertEqual("css", workflow["steps"][1]["locators"][0]["strategy"])
        self.assertFalse(workflow["review"]["approved"])

    def test_replay_requires_approval_for_go(self):
        workflow = make_workflow()
        page = FakePage([{"css": "textarea[name=text]", "text": ""},
                         {"css": "button[type=submit]", "text": ""}])
        with self.assertRaises(workflows.WorkflowNotApproved):
            workflows.replay(self.data, workflow, page=page, go=True, values={"item": "1", "text": "hi"})

    def test_dry_run_resolves_without_filling(self):
        workflow = make_workflow()
        page = FakePage([{"role": "textbox", "name": "Comment", "text": ""},
                         {"css": "button[type=submit]", "text": ""}])
        result = workflows.replay(self.data, workflow, page=page, go=False,
                                  values={"item": "1", "text": "hello"})
        self.assertEqual("dry-run", result["status"])
        self.assertEqual([], page.fills)
        self.assertEqual("role", result["run"]["steps"][1]["detail"]["strategy"])

    def test_approved_workflow_replays_and_journals(self):
        workflow = workflows.approve(self.data, make_workflow(), by="tester")["workflow"]
        page = FakePage([{"css": "textarea[name=text]", "text": ""},
                         {"css": "button[type=submit]", "text": ""}])
        result = workflows.replay(self.data, workflow, page=page, go=True,
                                  values={"item": "42", "text": "hello"}, campaign="c1")
        self.assertEqual("completed", result["status"], json.dumps(result, ensure_ascii=False))
        self.assertEqual([("textarea[name=text]", "hello")], page.fills)
        self.assertTrue((self.data / "workflows" / "runs" / f"{result['run_id']}.json").is_file())
        run = workflows.load_run(self.data, result["run_id"])
        self.assertTrue(all(step["state"] == "done" for step in run["steps"]))
        self.assertEqual("https://news.ycombinator.com/item?id=42", page.url)

    def test_editing_an_approved_workflow_invalidates_it(self):
        workflow = workflows.approve(self.data, make_workflow(), by="tester")["workflow"]
        workflow["steps"][1]["value"] = "{{other}}"
        state = workflows.approval(self.data, workflow)
        self.assertFalse(state["approved"])
        self.assertIn("changed", state["reason"])

    def test_readback_mismatch_stops_the_run(self):
        workflow = make_workflow()
        workflow["steps"][1]["verify"] = {"readback": "expected"}
        workflow = workflows.approve(self.data, workflow, by="tester")["workflow"]
        page = FakePage([{"css": "textarea[name=text]", "text": ""},
                         {"css": "button[type=submit]", "text": ""}])
        result = workflows.replay(self.data, workflow, page=page, go=True,
                                  values={"item": "1", "text": "actual"})
        self.assertEqual("step_failed", result["status"])
        self.assertIn("readback", result["stop_reason"])

    def test_login_wall_matches_an_exception_branch(self):
        workflow = workflows.approve(self.data, make_workflow(), by="tester")["workflow"]
        page = FakePage([{"css": "textarea[name=text]", "text": ""}])

        def goto(url):
            raise RuntimeError("redirected to /login")

        page.goto = goto
        result = workflows.replay(self.data, workflow, page=page, go=True,
                                  values={"item": "1", "text": "x"})
        self.assertEqual("stopped_by_policy", result["status"])
        self.assertEqual("login wall", result["stop_reason"])

    def test_unknown_action_result_requires_reconciliation(self):
        workflow = make_workflow(steps=[
            {"id": "s1", "kind": "action", "intent": "reply", "action": "reply",
             "payload": {"target": "item:1", "text": "{{text}}"}},
        ])
        workflow = workflows.approve(self.data, workflow, by="tester")["workflow"]
        executor = lambda **kwargs: {"result": "indeterminate"}  # noqa: E731
        result = workflows.replay(self.data, workflow, executor=executor, go=True,
                                  values={"text": "hi"})
        self.assertEqual("indeterminate", result["status"])
        with self.assertRaises(workflows.WorkflowError):
            workflows.replay(self.data, workflow, executor=executor, go=True, values={"text": "hi"})
        resolved = workflows.reconcile(self.data, result["run_id"], outcome="not_sent",
                                       note="checked the item, no comment", by="tester")
        self.assertEqual("resolved_not_sent", resolved["status"])
        with self.assertRaises(workflows.WorkflowError):
            workflows.reconcile(self.data, result["run_id"], outcome="sent", note="x", by="tester")

    def test_commit_click_obeys_pause_and_writes_the_ledger(self):
        workflow = make_workflow(steps=[
            {"id": "s1", "kind": "open", "url": "https://example.test/post"},
            {"id": "s2", "kind": "click", "intent": "submit", "commit": True,
             "target": "https://example.test/post",
             "locators": [{"strategy": "css", "value": "button[type=submit]"}]},
        ])
        workflow = workflows.approve(self.data, workflow, by="tester")["workflow"]
        page = FakePage([{"css": "button[type=submit]", "text": ""}])
        from core import safety
        safety.set_pause(self.data, True, "incident")
        blocked = workflows.replay(self.data, workflow, page=page, go=True)
        self.assertEqual("step_failed", blocked["status"])
        self.assertIn("pause", blocked["stop_reason"])
        self.assertEqual([], page.clicks)
        safety.set_pause(self.data, False)
        result = workflows.replay(self.data, workflow, page=page, go=True, campaign="c1")
        self.assertEqual("completed", result["status"], json.dumps(result, ensure_ascii=False))
        ledger = (self.data / "ledger.jsonl").read_text(encoding="utf-8").strip().splitlines()
        records = [json.loads(line) for line in ledger]
        self.assertEqual(["committed"], [record["result"] for record in records])
        self.assertEqual("workflow-ui", records[0]["transport"])
        self.assertEqual("c1", records[0]["campaign"])

    def test_cli_learn_approve_and_dry_run(self):
        trace = {
            "channel": "hn", "action": "reply",
            "steps": [{"intent": "reply", "kind": "action", "action": "reply",
                       "payload": {"target": "item:{{item}}", "text": "{{text}}"}}],
        }
        trace_path = Path(tempfile.mkdtemp()) / "trace.json"
        trace_path.write_text(json.dumps(trace), encoding="utf-8")
        learned = subprocess.run(
            [sys.executable, str(MKT), "--workspace", str(self.data), "workflow", "learn",
             "hn-action", "--trace", str(trace_path)],
            capture_output=True, text=True, timeout=120)
        self.assertEqual(0, learned.returncode, learned.stdout + learned.stderr)
        approved = subprocess.run(
            [sys.executable, str(MKT), "--workspace", str(self.data), "workflow", "approve",
             "hn-action", "--by", "tester"],
            capture_output=True, text=True, timeout=120)
        self.assertEqual(0, approved.returncode, approved.stdout + approved.stderr)
        dry = subprocess.run(
            [sys.executable, str(MKT), "--workspace", str(self.data), "workflow", "replay",
             "hn-action", "--var", "item=1", "--var", "text=hello"],
            capture_output=True, text=True, timeout=120)
        self.assertEqual(0, dry.returncode, dry.stdout + dry.stderr)
        self.assertEqual("dry-run", json.loads(dry.stdout)["status"])


if __name__ == "__main__":
    unittest.main()
