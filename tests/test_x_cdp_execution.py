import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core import action_runner, platform_compliance


def auth(scopes):
    now = dt.datetime.now().astimezone()
    return {
        "route": "owner_attested_session", "approved_by": "tester",
        "approved_at": now.isoformat(), "window_start": now.isoformat(),
        "window_end": (now + dt.timedelta(hours=1)).isoformat(),
        "scopes": scopes, "stop_conditions": ["captcha"], "revoked": False,
    }


def identity():
    return {
        "enforced": True, "mode": "cdp_observed",
        "observed_platform_identity": "x:owner", "handle": "owner",
        "identity_proof_ref": "offline:identity", "browser_context": "offline:test",
        "observed_at": "2026-11-23T00:00:00+00:00",
    }


class XOwnerAttestedCdpTest(unittest.TestCase):
    def test_message_and_engage_are_supported_with_cdp_transport(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = platform_compliance.owner_attested_path(tmp, "x")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(auth(["x:message", "x:engage"])), encoding="utf-8")
            for action in ("message", "engage"):
                got = platform_compliance.require_owner_attested_browser_write(
                    "x", action, tmp, go=True, actual_transport="cdp", identity=identity())
                self.assertEqual("owner_attested_session", got["route"])
                self.assertFalse(got["platform_permission"])

    def test_action_runner_selects_owner_attested_cdp_when_window_is_valid(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = platform_compliance.owner_attested_path(tmp, "x")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(auth(["x:publish"])), encoding="utf-8")
            with mock.patch("core.action_runner.gate_account_identity", return_value=identity()), \
                    mock.patch("core.action_runner.precheck", return_value=None), \
                    mock.patch("channels.x.adapter.run", return_value=0) as vendor:
                action_runner._dispatch_action(
                    channel="x", action="publish", data_dir=tmp, campaign="test",
                    go=True, text="synthetic", target="tweet:1", legacy_cdp=True)
        self.assertEqual("owner_attested_cdp", vendor.call_args.kwargs["execution_mode"])


if __name__ == "__main__":
    unittest.main()
