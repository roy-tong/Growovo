#!/usr/bin/env python3
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import mcp_server

ROOT = Path(__file__).resolve().parents[1]


class GTMOSCLICase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name) / "data"
        self.addCleanup(self.tmp.cleanup)

    def cli(self, *args, expected=0):
        result = subprocess.run([sys.executable, str(ROOT / "mkt.py"), "--workspace", str(self.data), *args],
                                cwd=ROOT, text=True, capture_output=True)
        self.assertEqual(expected, result.returncode,
                         msg=f"command={args}\nstdout={result.stdout}\nstderr={result.stderr}")
        return json.loads(result.stdout) if result.stdout.strip() else result.stderr

    def write(self, name, payload):
        path = Path(self.tmp.name) / name
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return str(path)

    def context(self):
        return {"context_id": "ctx-example-product", "version": "2026-11-04.1",
                "source": {"kind": "user_declared", "ref": "fixture"},
                "value_event_definitions": [{"event": "first_successful_api_call"}],
                "non_goals": ["no sends"], "approved_by": "human", "content_sha256": "fixture"}

    def objective(self):
        return {"objective_id": "obj-first-success-001", "version": "1",
                "context_ref": {"context_id": "ctx-example-product", "version": "2026-11-04.1"},
                "business_question": "能否首次成功", "subject_type": "developer",
                "primary_metric_ref": {"metric_id": "developer_activation_rate", "version": "1.0.0"},
                "window": "2026-11", "owner": "human"}

    def test_registry_and_metric_cli(self):
        self.assertTrue(self.cli("capability", "lint")["ok"])
        self.assertTrue(self.cli("template", "lint")["ok"])
        events = self.write("events.json", [
            {"event_type": "eligible_tutorial_cohort_entry", "developer_id": "d1"},
            {"event_type": "first_successful_api_call", "developer_id": "d1", "verified": True, "approximated": False},
        ])
        result = self.cli("metric", "measure", "--metric-id", "developer_activation_rate", "--events", events)
        self.assertEqual(1.0, result["value"])

    def test_orchestrate_cli_selects_minimum_team_and_expression(self):
        product = self.write("product.json", {"product_type": "devtool", "name": "API X"})
        out = str(Path(self.tmp.name) / "orchestration.json")
        plan = self.cli("orchestrate", "--product", product, "--goal", "首次成功调用",
                        "--task", "写开发者教程并做 GitHub 发布", "--audience", "developer",
                        "--channel", "github", "--out", out)
        self.assertEqual(["46", "24", "44", "12"], plan["selection"]["role_families"])
        self.assertEqual("technical_issue_or_comment", plan["content_plan"]["expression"]["format"])
        self.assertTrue(Path(out).exists())

    def test_work_order_vertical_slice_cli(self):
        self.cli("context", "import", "--file", self.write("context.json", self.context()), "--by", "human")
        self.cli("objective", "create", "--file", self.write("objective.json", self.objective()), "--by", "human")
        order = json.loads((ROOT / "docs/gtm-os/examples/work-order.example.json").read_text(encoding="utf-8"))
        self.cli("work", "create", "--file", self.write("order.json", order), "--by", "human")
        ready = self.cli("work", "ready", "--id", order["work_order_id"], "--by", "human")
        self.assertEqual("ready", ready["state"])
        run = self.cli("sop", "start", "--work-order-id", order["work_order_id"], "--by", "human")
        self.assertEqual("confirm-task-and-value-event", run["current_step_id"])
        shown = self.cli("work", "show", "--id", order["work_order_id"])
        self.assertEqual("running", shown["state"])

    def test_ready_failure_is_json_error_and_blocked(self):
        self.cli("context", "import", "--file", self.write("context.json", self.context()), "--by", "human")
        self.cli("objective", "create", "--file", self.write("objective.json", self.objective()), "--by", "human")
        order = json.loads((ROOT / "docs/gtm-os/examples/work-order.example.json").read_text(encoding="utf-8"))
        order["input_bindings"][0]["acceptance_status"] = "submitted"
        self.cli("work", "create", "--file", self.write("order.json", order), "--by", "human")
        result = subprocess.run([sys.executable, str(ROOT / "mkt.py"), "--workspace", str(self.data),
                                 "work", "ready", "--id", order["work_order_id"], "--by", "human"],
                                cwd=ROOT, text=True, capture_output=True)
        self.assertEqual(2, result.returncode)
        self.assertIn("未验收", result.stderr)
        self.assertEqual("blocked", self.cli("work", "show", "--id", order["work_order_id"])["state"])


class TestGTMOSMCP(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def call(self, name, args):
        response = mcp_server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                      "params": {"name": name, "arguments": args}})
        self.assertFalse(response["result"].get("isError", False), response)
        return json.loads(response["result"]["content"][0]["text"])

    def test_read_only_tool_surface(self):
        names = {t["name"] for t in mcp_server.TOOLS}
        self.assertTrue({"mkt_orchestrate", "mkt_gtm_design", "mkt_capabilities", "mkt_templates",
                         "mkt_work_order", "mkt_metric_measure"} <= names)
        self.assertNotIn("mkt_work_create", names)
        self.assertNotIn("mkt_deliverable_review", names)

    def test_registry_and_metric_calls(self):
        with patch.object(mcp_server, "DATA", self.data):
            plan = self.call("mkt_orchestrate", {"product": {"product_type": "b2b_saas"},
                                                 "goal": "合格会议", "task": "给 SDR 做资格交接"})
            self.assertEqual(["26", "27", "42"], plan["selection"]["role_families"])
            caps = self.call("mkt_capabilities", {"lint": True})
            self.assertTrue(caps["ok"])
            templates = self.call("mkt_templates", {"lint_registry": True})
            self.assertTrue(templates["ok"])
            result = self.call("mkt_metric_measure", {
                "metric_id": "developer_activation_rate",
                "events": [],
            })
            self.assertEqual("insufficient_denominator", result["status"])
            self.assertEqual([], self.call("mkt_work_order", {"action": "next"})["next"])
            design = self.call("mkt_gtm_design", {
                "task": "增长一个小红书摄影账号，目标是目标粉丝",
                "constraints": {"authorized_channels": ["xiaohongshu"], "assets": ["content"],
                                "data_sources": ["platform_insights"]},
            })
            self.assertEqual("content_account", design["object"]["id"])
            self.assertEqual("content_led", design["selected_motion"])
            self.assertTrue(design["validation"]["ok"])
            self.assertEqual([], design["validation"]["problems"])


if __name__ == "__main__":
    unittest.main()
