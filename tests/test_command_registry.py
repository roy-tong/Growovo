#!/usr/bin/env python3
import argparse
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from core.command_registry import CommandRegistry, CommandSpec
from core import cli_runtime
import mkt


class CommandRegistryTests(unittest.TestCase):
    def test_duplicate_names_fail_and_handler_is_lazy(self):
        registry = CommandRegistry()
        spec = CommandSpec("one", "tests.test_projections", "ProjectionTests", lambda parser: None)
        registry.register(spec)
        with self.assertRaises(ValueError):
            registry.register(spec)
        self.assertIsNone(spec._handler)
        self.assertTrue(callable(registry.resolve("one")))

    def test_runtime_parser_dispatches_without_mkt_parser_growth(self):
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="command", required=True)
        seen = {}
        def runtime(args):
            seen["args"] = args
            return 0
        cli_runtime.register(sub, runtime_handler=runtime, outreach_handler=lambda args: 0)
        args = parser.parse_args(["runtime", "project", "business", "--night", "2026-11-23"])
        self.assertEqual(0, args.fn(args))
        self.assertEqual("business", seen["args"].name)

    def test_runtime_registration_uses_shared_command_registry(self):
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="command", required=True)
        with mock.patch("core.command_registry.CommandRegistry.attach", autospec=True) as attach:
            cli_runtime.register(sub, runtime_handler=lambda args: 0,
                                 outreach_handler=lambda args: 0)
        self.assertTrue(attach.called)

    def test_modern_runtime_projection_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            output = io.StringIO()
            with mock.patch.object(mkt, "DATA", data), contextlib.redirect_stdout(output):
                code = mkt.cmd_runtime(SimpleNamespace(action="project", name="business",
                                                       night="2026-11-23", scheduled_only=False))
            self.assertEqual(0, code)
            got = json.loads(output.getvalue())
            self.assertEqual("business", got["name"])
            self.assertEqual("not_included", got["body"]["status"])

    def test_validation_command_is_registered_as_read_only(self):
        parser = mkt.build_parser()
        args = parser.parse_args(["validation", "run", "--file", "validation.json"])
        self.assertEqual("run", args.action)
        self.assertEqual("validation.json", args.file)

    def test_legacy_cycle_and_modern_commands_coexist(self):
        parser = mkt.build_parser()
        cycle = parser.parse_args(["cycle", "quota"])
        runtime = parser.parse_args(["runtime", "context", "--campaign", "c", "--wave", "w"])
        outreach = parser.parse_args(["outreach", "feedback", "--file", "x.json"])
        self.assertEqual("quota", cycle.action)
        self.assertEqual("context", runtime.action)
        self.assertEqual("feedback", outreach.action)


if __name__ == "__main__":
    unittest.main()
