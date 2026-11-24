import unittest

from core.growth_goals import compile_goal, validate_goal


def goal(human_minutes=120):
    return {
        "schema": "growovo.growth-goal/1",
        "product": {"name": "Growovo", "url": "https://github.com/roy-tong/Growovo"},
        "window": {"start": "2026-11-23T22:00:00+08:00",
                   "end": "2026-11-24T07:00:00+08:00"},
        "outcomes": [
            {"id": "activation", "role": "primary", "metric": "activated_users", "target": 1},
            {"id": "stars", "role": "secondary", "metric": "github_stars", "target_delta": 3},
            {"id": "followers", "role": "secondary", "metric": "x_followers", "target_delta": 3},
        ],
        "execution_intent": "live",
        "on_channel_blocked": "manual_handoff",
        "constraints": {"human_minutes": human_minutes, "channels": ["x", "github"],
                        "no_paid_procurement": True},
        "audience": {"id": "agent-builders", "role": "Agent 开发者",
                     "trigger": "无法判断 Agent 动作是否带来结果"},
        "assets": [{"id": "demo", "kind": "demo", "url": "https://github.com/roy-tong/Growovo",
                    "cta": "run_demo", "reproduced": True, "sha256": "demo-sha"}],
    }


class ValidateGoalTest(unittest.TestCase):
    def test_short_window_and_multiple_outcomes_are_valid(self):
        got = validate_goal(goal())
        self.assertEqual(3, len(got["outcomes"]))
        self.assertEqual("live", got["execution_intent"])
        self.assertEqual(9 * 3600, got["window"]["duration_seconds"])

    def test_primary_outcome_is_required(self):
        payload = goal()
        payload["outcomes"] = payload["outcomes"][1:]
        with self.assertRaisesRegex(ValueError, "primary"):
            validate_goal(payload)

    def test_window_must_be_ordered_and_timezone_aware(self):
        payload = goal()
        payload["window"]["end"] = payload["window"]["start"]
        with self.assertRaisesRegex(ValueError, "window"):
            validate_goal(payload)
        payload = goal()
        payload["window"]["start"] = "2026-11-23 22:00:00"
        with self.assertRaisesRegex(ValueError, "时区"):
            validate_goal(payload)


class CompileGoalTest(unittest.TestCase):
    def test_actions_form_a_business_dag_with_required_context(self):
        got = compile_goal(goal())
        ids = {row["id"] for row in got["actions"]}
        self.assertIn("x-publish", ids)
        self.assertIn("github-engage", ids)
        self.assertIn("goal-evaluate", ids)
        self.assertIn("next-experiment", ids)
        for row in got["actions"]:
            for key in ("goal_ids", "hypothesis_id", "segment", "asset_ref", "cta",
                        "conversion_event", "measurement_ref", "depends_on", "execution"):
                self.assertIn(key, row, row["id"])
        x = next(row for row in got["actions"] if row["id"] == "x-publish")
        self.assertEqual(["baseline-observe", "asset-prepare"], x["depends_on"])
        self.assertEqual("demo", x["asset_ref"])
        self.assertEqual("demo-sha", x["asset_sha256"])
        final = next(row for row in got["actions"] if row["id"] == "final-observe")
        self.assertEqual({"github-engage", "x-publish"}, set(final["depends_on"]))

    def test_budget_defers_optional_channel_actions_instead_of_inflating_volume(self):
        got = compile_goal(goal(human_minutes=5))
        self.assertTrue(got["deferred_actions"])
        self.assertTrue(got["feasibility"]["blocked"])
        self.assertFalse(any(row["id"] == "x-publish" for row in got["actions"]))

    def test_advisor_mode_does_not_create_live_channel_work(self):
        payload = goal()
        payload["mode"] = "advisor"
        got = compile_goal(payload)
        self.assertFalse(any(row["kind"] == "channel" for row in got["actions"]))
        self.assertFalse(got["feasibility"]["blocked"])

    def test_autopilot_mode_is_preserved_in_the_service_contract(self):
        payload = goal()
        payload["mode"] = "autopilot"
        got = compile_goal(payload)
        self.assertEqual("autopilot", got["goal"]["mode"])
        self.assertIn("diagnose-product", {row["id"] for row in got["actions"]})


if __name__ == "__main__":
    unittest.main()
