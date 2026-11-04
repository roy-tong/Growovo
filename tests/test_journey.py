"""Journey：统一范围过滤、真实进展、断点建议与产品问题优先级。"""
import copy
import unittest

from core import journey

WINDOW = {"start": "2026-11-01", "end": "2026-11-07"}


def _event(event_type, subject="u1", campaign="c1", ts="2026-11-02T10:00:00+08:00", **extra):
    row = {
        "id": extra.pop("id", f"{campaign}-{subject}-{event_type}-{ts}"),
        "kind": "event", "campaign": campaign, "subject": subject,
        "event_type": event_type, "ts": ts,
    }
    row.update(extra)
    return row


def _value(event_type, **extra):
    row = _event(event_type, **extra)
    row["source_ref"] = extra.get("source_ref", f"proof/{row['id']}")
    if event_type in ("activation", "activated", "paid", "payment", "purchase"):
        row["verification"] = {"status": "verified", "method": "product-db",
                               "ref": f"verify-{row['id']}"}
    return row


def _diagnose(events, **extra):
    args = {"campaign": "c1", "subject": "u1", "time_window": WINDOW}
    args.update(extra)
    return journey.diagnose(events, **args)


class JourneyTests(unittest.TestCase):
    def test_empty_data_is_no_data_not_audience_failure(self):
        result = _diagnose([])
        self.assertEqual("no_data", result["status"])
        self.assertEqual(journey.STAGES, tuple(row["stage"] for row in result["funnel"]))
        self.assertTrue(all(row["subjects"] == 0 for row in result["funnel"]))
        self.assertEqual([], result["breakpoints"])
        self.assertEqual("collect_evidence", result["priority_recommendation"]["code"])
        self.assertNotEqual("fix_audience", result["priority_recommendation"]["code"])

    def test_campaign_subject_and_time_window_are_applied_together(self):
        events = [
            _event("signal", ts="2026-11-01T00:00:00+08:00"),
            _event("reply", ts="2026-11-08T00:00:00+08:00"),  # 出窗口
            _event("reply", subject="u2"),
            _value("activation", campaign="old-campaign"),  # 历史 campaign
            _value("paid", campaign="old-campaign"),
            _value("activation", ts="2026-11-03T10:00:00+08:00"),
        ]
        selected = journey.filter_events(events, campaign="c1", subject="u1", time_window=WINDOW)
        self.assertEqual(2, len(selected))
        result = _diagnose(events)
        trace = result["subject_journeys"][0]
        self.assertEqual("activation", trace["current_stage"])
        stages = {row["stage"]: row["subjects"] for row in result["funnel"]}
        self.assertEqual(1, stages["discovery"])
        self.assertEqual(0, stages["engagement"])
        self.assertEqual(0, stages["payment"])
        self.assertEqual("retention", trace["next_stage"])

    def test_historical_paid_object_does_not_advance_current_campaign(self):
        events = [
            _value("paid", campaign="2026-w40", ts="2026-10-20T10:00:00+08:00"),
            _value("activation", campaign="2026-w40", ts="2026-10-19T10:00:00+08:00"),
            _event("signal", campaign="2026-w45"),
        ]
        result = _diagnose(events, campaign="2026-w45")
        trace = result["subject_journeys"][0]
        self.assertEqual("discovery", trace["current_stage"])
        self.assertEqual(["discovery"], trace["reached_stages"])
        self.assertEqual(0, result["funnel"][-1]["subjects"])

    def test_full_journey_uses_distinct_subjects_and_verified_value_events(self):
        types = ["signal", "reply", "signup", "activation", "retained_use", "paid"]
        events = []
        for index, event_type in enumerate(types):
            maker = _value if event_type in ("activation", "retained_use", "paid") else _event
            events.append(maker(event_type, ts=f"2026-11-0{index + 1}T10:00:00+08:00"))
        events.append(_event("signal", id="duplicate-discovery"))  # 同人同事阶段只数一次
        snapshot = copy.deepcopy(events)
        result = _diagnose(events)
        self.assertEqual(snapshot, events)
        self.assertEqual("complete", result["status"])
        self.assertEqual("payment", result["subject_journeys"][0]["current_stage"])
        self.assertEqual("no_breakpoint", result["priority_recommendation"]["code"])
        self.assertTrue(all(row["subjects"] == 1 for row in result["funnel"]))
        self.assertEqual(2, result["funnel"][0]["events"])

    def test_approximated_and_similarly_named_events_never_confirm_progress(self):
        events = [
            _event("signal"),
            _value("activation", id="approx-activation", approximated=True,
                   ts="2026-11-03T10:00:00+08:00"),
            _event("signup", id="approx-signup", approximated=True,
                   ts="2026-11-03T11:00:00+08:00"),
            _event("almost_paid", id="near-name", ts="2026-11-04T10:00:00+08:00"),
        ]
        result = _diagnose(events)
        trace = result["subject_journeys"][0]
        self.assertEqual("discovery", trace["current_stage"])
        self.assertEqual(2, len(result["quality"]["approximated"]))
        self.assertEqual(1, len(result["quality"]["unknown_events"]))
        stages = {row["stage"]: row["subjects"] for row in result["funnel"]}
        self.assertEqual(0, stages["trial"])
        self.assertEqual(0, stages["activation"])
        self.assertEqual(0, stages["payment"])

    def test_unverified_activation_and_paid_are_disclosed_not_counted(self):
        events = [
            _event("signup"),
            _event("activation", id="unverified-activation", source_ref="product/a1",
                   ts="2026-11-03T10:00:00+08:00"),
            _event("paid", id="unverified-paid", source_ref="billing/p1",
                   ts="2026-11-04T10:00:00+08:00"),
        ]
        result = _diagnose(events)
        self.assertEqual("trial", result["subject_journeys"][0]["current_stage"])
        self.assertEqual(2, len(result["quality"]["unverified_value_events"]))
        self.assertEqual(0, result["funnel"][3]["subjects"])
        self.assertEqual(1, result["funnel"][3]["observed_subjects"])

    def test_breakpoints_map_to_all_five_repair_areas(self):
        cases = [
            (_event("signal", audience_match=False), "fix_audience"),
            (_event("signal"), "fix_content"),
            (_event("reply"), "fix_handoff"),
            (_event("signup"), "fix_product"),
            (_value("activation"), "fix_product"),
            (_value("retained_use"), "fix_pricing"),
        ]
        for event, expected in cases:
            with self.subTest(event=event["event_type"], expected=expected):
                result = _diagnose([event])
                self.assertEqual(expected, result["priority_recommendation"]["code"])

    def test_product_issue_precedes_acquisition_and_other_breakpoints(self):
        result = _diagnose(
            [_event("signal")],
            product_issues={
                "id": "issue-activation", "campaign": "c1", "subject": "u1",
                "ts": "2026-11-03T09:00:00+08:00", "summary": "首次任务必现崩溃",
                "severity": "high", "blocks_activation": True,
                "source_ref": "product/issues/activation-1",
            },
        )
        self.assertEqual("fix_product", result["priority_recommendation"]["code"])
        self.assertTrue(result["acquisition"]["should_pause"])
        self.assertFalse(any(row["code"] == "fix_content" for row in result["recommendations"]))
        self.assertIn("暂停", result["priority_recommendation"]["reason"])

    def test_historical_or_other_campaign_product_issue_does_not_override(self):
        result = _diagnose(
            [_event("signal")],
            product_issues=[{
                "id": "old-issue", "campaign": "old", "subject": "u1",
                "ts": "2026-11-03T09:00:00+08:00", "severity": "critical",
                "summary": "历史问题",
            }],
        )
        self.assertEqual([], result["product_issues"])
        self.assertEqual("fix_content", result["priority_recommendation"]["code"])
        self.assertFalse(result["acquisition"]["should_pause"])

    def test_product_issue_embedded_in_current_event_is_also_prioritized(self):
        result = _diagnose([_event("signup", product_issue=True, severity="high",
                                   blocks_activation=True, source_ref="product/trial-fail-1")])
        self.assertEqual("fix_product", result["priority_recommendation"]["code"])
        self.assertTrue(result["acquisition"]["should_pause"])

    def test_window_boundaries_and_event_envelope(self):
        events = {"events": [
            _event("signal", id="start", ts="2026-11-01"),
            _event("reply", id="end", ts="2026-11-07T23:59:59"),
            _event("signup", id="outside", ts="2026-11-08"),
        ]}
        selected = journey.filter_events(events, campaign="c1", subject="u1", time_window=WINDOW)
        self.assertEqual({"start", "end"}, {row["id"] for row in selected})
        with self.assertRaises(ValueError):
            journey.filter_events([], campaign="", time_window=WINDOW)
        with self.assertRaises(ValueError):
            journey.filter_events([], campaign="c1", time_window={})
        with self.assertRaises(ValueError):
            journey.filter_events([], campaign="c1", time_window={
                "start": "2026-11-01T00:00:00+08:00", "end": "2026-11-07",
            })

    def test_out_of_order_event_does_not_backfill_or_rewind_journey(self):
        events = [
            _value("activation", id="activation-first", ts="2026-11-04T10:00:00+08:00"),
            _event("signup", id="late-trial", ts="2026-11-05T10:00:00+08:00"),
        ]
        result = _diagnose(events)
        trace = result["subject_journeys"][0]
        self.assertEqual("activation", trace["current_stage"])
        self.assertEqual(["activation"], trace["reached_stages"])
        self.assertIn("trial", trace["missing_prior_stages"])
        self.assertEqual(["late-trial"], trace["out_of_order_event_refs"])
        self.assertEqual("late-trial", result["quality"]["out_of_order"][0]["event_ref"])
        self.assertEqual(0, result["funnel"][2]["subjects"])

    def test_real_timestamp_order_not_input_order_controls_sequence(self):
        events = [
            _value("activation", id="activation", ts="2026-11-04T10:00:00+08:00"),
            _event("signup", id="trial", ts="2026-11-03T10:00:00+08:00"),
        ]
        result = _diagnose(events)
        self.assertEqual([], result["quality"]["out_of_order"])
        self.assertEqual(["trial", "activation"], result["subject_journeys"][0]["reached_stages"])

    def test_identity_normalization_and_explicit_alias_map_merge_only_known_subject(self):
        events = [
            _event("signal", id="discover", subject="User@Example.com", ts="2026-11-01T10:00:00+08:00"),
            _value("activation", id="activate", subject="employee-42", ts="2026-11-03T10:00:00+08:00"),
            _value("paid", id="other-user", subject="other@example.com", ts="2026-11-04T10:00:00+08:00"),
        ]
        result = journey.diagnose(events, campaign="c1", subject="USER@example.com", time_window=WINDOW,
                                  identity_map={"employee-42": "user@example.com"})
        self.assertEqual(1, len(result["subject_journeys"]))
        trace = result["subject_journeys"][0]
        self.assertEqual("user@example.com", trace["subject"])
        self.assertEqual("activation", trace["current_stage"])
        self.assertEqual(2, len(result["quality"]["identity_aliases"]))
        self.assertEqual(0, result["funnel"][-1]["subjects"])

    def test_first_activation_is_distinct_from_repeat_activation_and_repeat_use(self):
        events = [
            _event("signup", id="trial", ts="2026-11-01T10:00:00+08:00"),
            _value("activation", id="first-activation", ts="2026-11-02T10:00:00+08:00"),
            _value("retained_use", id="repeat-use", ts="2026-11-03T10:00:00+08:00"),
            _value("activation", id="second-activation", ts="2026-11-04T10:00:00+08:00"),
        ]
        result = _diagnose(events)
        trace = result["subject_journeys"][0]
        self.assertEqual("retention", trace["current_stage"])
        self.assertEqual("first-activation", trace["first_activation_event_ref"])
        self.assertEqual(["second-activation"], trace["repeated_activation_event_refs"])
        self.assertEqual(1, result["funnel"][3]["subjects"])
        self.assertEqual("second-activation", result["quality"]["repeated_activation"][0]["event_ref"])

    def test_multi_touch_is_disclosed_without_last_event_causal_credit(self):
        events = [
            _event("signal", id="touch-reddit", channel="reddit", ts="2026-11-01T10:00:00+08:00"),
            _event("reply", id="touch-email", channel="email", ts="2026-11-02T10:00:00+08:00"),
        ]
        result = _diagnose(events)
        relation = result["multi_touch_relationships"][0]
        self.assertEqual(["email", "reddit"], relation["channels"])
        self.assertEqual("unassigned", relation["causal_credit"])
        self.assertFalse(relation["last_touch_is_cause"])
        self.assertFalse(result["attribution"]["last_event_is_cause"])
        self.assertIn("不是因果", result["attribution"]["note"])


if __name__ == "__main__":
    unittest.main()
