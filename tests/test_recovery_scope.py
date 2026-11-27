import copy
import unittest

from core import recovery_scope


class RecoveryScopeTest(unittest.TestCase):
    def setUp(self):
        self.originals = [
            {"id": "ask-x", "product": "askovo", "platform": "x", "action": "reply",
             "target": {"url": "https://x.com/original/status/1"}},
            {"id": "grow-gh", "product": "growovo", "platform": "github", "action": "reply",
             "target": {"url": "https://github.com/example/repo/issues/1"}},
        ]
        self.results = [
            {"queue_item_id": "ask-x", "result": "failed",
             "target": "https://x.com/original/status/1"},
            {"queue_item_id": "grow-gh", "result": "committed",
             "target": "https://github.com/example/repo/issues/1"},
        ]
        self.recovery = {
            "id": "recovery-ask-x", "replaces": "ask-x", "product": "askovo",
            "platform": "x", "action": "reply",
            "target": {"url": "https://x.com/new/status/2"},
        }

    def test_failed_original_can_be_replaced_once_by_an_untouched_target(self):
        self.assertEqual([], recovery_scope.validate(
            [self.recovery], self.originals, self.results))

    def test_completed_recovery_receipt_does_not_make_its_own_target_invalid(self):
        results = self.results + [{"queue_item_id": self.recovery["id"], "result": "committed",
                                   "target": self.recovery["target"]["url"]}]
        self.assertEqual([], recovery_scope.validate([self.recovery], self.originals, results))

    def test_recovery_must_name_the_original_failure(self):
        item = copy.deepcopy(self.recovery)
        del item["replaces"]
        self.assertIn("recovery_original_required", recovery_scope.validate(
            [item], self.originals, self.results))

    def test_committed_or_unknown_original_cannot_be_replaced(self):
        for result in ("committed", "indeterminate"):
            with self.subTest(result=result):
                results = copy.deepcopy(self.results)
                results[0]["result"] = result
                self.assertIn("recovery_original_not_replaceable", recovery_scope.validate(
                    [self.recovery], self.originals, results))

    def test_recovery_preserves_product_platform_and_action(self):
        item = dict(self.recovery, product="verovo")
        self.assertIn("recovery_scope_mismatch", recovery_scope.validate(
            [item], self.originals, self.results))

    def test_same_target_cannot_be_reused(self):
        item = copy.deepcopy(self.recovery)
        item["target"]["url"] = "https://x.com/original/status/1"
        self.assertIn("recovery_target_already_touched", recovery_scope.validate(
            [item], self.originals, self.results))

    def test_two_recoveries_cannot_replace_the_same_failure(self):
        second = dict(self.recovery, id="recovery-ask-x-2")
        second["target"] = {"url": "https://x.com/other/status/3"}
        self.assertIn("duplicate_recovery_original", recovery_scope.validate(
            [self.recovery, second], self.originals, self.results))

    def test_unrelated_new_items_cannot_fill_a_quota(self):
        item = dict(self.recovery, replaces="grow-gh")
        errors = recovery_scope.validate([item], self.originals, self.results)
        self.assertIn("recovery_scope_mismatch", errors)
        self.assertIn("recovery_original_not_replaceable", errors)


if __name__ == "__main__":
    unittest.main()
