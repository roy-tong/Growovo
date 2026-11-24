import unittest

from core.action_resolution import resolve


def action(intent="live", on_blocked="manual_handoff"):
    return {
        "id": "x-publish", "kind": "channel", "channel": "x", "action": "publish",
        "execution": {"intent": intent, "on_channel_blocked": on_blocked,
                      "preferred_transports": ["official_api", "cdp", "manual_handoff"]},
    }


class ActionResolutionTest(unittest.TestCase):
    def test_official_api_is_preferred_over_cdp(self):
        got = resolve(action(), {"channels": {"x": {"official_api": True, "cdp": True}}})
        self.assertEqual("official_api", got["execution_mode"])
        self.assertFalse(got["blocked"])

    def test_cdp_is_used_when_api_is_unavailable(self):
        got = resolve(action(), {"channels": {"x": {"cdp": True}}})
        self.assertEqual("cdp", got["execution_mode"])
        self.assertFalse(got["blocked"])

    def test_live_without_fallback_never_silently_becomes_dry_run(self):
        row = action(on_blocked="stop")
        got = resolve(row, {"channels": {"x": {}}})
        self.assertTrue(got["blocked"])
        self.assertEqual("LIVE_BLOCKED", got["code"])
        self.assertNotEqual("dry-run", got["execution_mode"])

    def test_manual_fallback_is_a_real_work_package(self):
        got = resolve(action(), {"channels": {"x": {}}})
        self.assertEqual("manual_handoff", got["execution_mode"])
        self.assertTrue(got["handoff"]["target"])
        self.assertEqual("publish", got["handoff"]["action"])

    def test_explicit_preview_is_the_only_preview_mode(self):
        got = resolve(action(intent="preview"), {"channels": {"x": {"cdp": True}}})
        self.assertEqual("preview", got["execution_mode"])
        self.assertFalse(got["blocked"])

    def test_agent_solution_names_the_actual_managed_browser_route(self):
        got = resolve(action(), {"channels": {"x": {"cdp": True}}})
        solution = got.get("solution") or {}
        self.assertEqual("browser-attach", solution.get("route"))
        self.assertEqual("readback_receipt", solution.get("completion", {}).get("operation"))
        self.assertNotIn("verify_credentials", str(solution))

    def test_legacy_identity_error_recovers_through_default_route(self):
        got = resolve(action(), {
            "channels": {"x": {}},
            "error": {"code": "identity_unknown", "detail": "verify_credentials HTTP 403"},
        })
        recovery = (got.get("solution") or {}).get("recovery") or {}
        self.assertEqual("browser-attach", recovery.get("route"))
        self.assertEqual("ensure_managed_tab", recovery.get("steps", [{}])[0].get("operation"))
        self.assertFalse(recovery.get("retry_write", True))
        self.assertNotIn("login_legacy_cdp", str(recovery))

    def test_indeterminate_result_reconciles_without_repeating_the_write(self):
        got = resolve(action(), {
            "channels": {"x": {"cdp": True}},
            "error": {"code": "indeterminate", "detail": "readback unavailable"},
        })
        recovery = (got.get("solution") or {}).get("recovery") or {}
        self.assertEqual("reconcile_receipt", recovery.get("steps", [{}])[0].get("operation"))
        self.assertFalse(recovery.get("retry_write", True))


if __name__ == "__main__":
    unittest.main()
