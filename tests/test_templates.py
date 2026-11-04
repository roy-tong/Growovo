"""GTM OS 模板注册表与实例校验契约。"""
import re
import unittest

from core import templates


def valid_fields(template):
    fields = {}
    for slot in template["required_slots"]:
        if slot == "evidence_refs":
            fields[slot] = [{
                "source_id": "source-001",
                "locator": "https://example.test/evidence/001",
                "version": "1",
            }]
        else:
            fields[slot] = "filled-%s" % slot
    return fields


class TemplateRegistryTests(unittest.TestCase):
    def test_exact_32_coverage_and_complete_metadata(self):
        rows = templates.list_templates()
        self.assertEqual(32, len(rows))
        self.assertEqual(["T%02d" % n for n in range(1, 33)],
                         [row["template_id"] for row in rows])
        for template in rows:
            with self.subTest(template=template["template_id"]):
                self.assertEqual("1.0.0", template["version"])
                self.assertTrue(template["name"].strip())
                self.assertTrue(template["roles"])
                self.assertTrue(template["required_slots"])
                self.assertTrue(template["professional_fields"])
                self.assertTrue(template["qa"])
                self.assertEqual(len(template["required_slots"]),
                                 len(set(template["required_slots"])))
                for slot in template["required_slots"]:
                    self.assertRegex(slot, re.compile(r"^[a-z][a-z0-9_]*$"))
                field_ids = [field["field_id"]
                             for field in template["professional_fields"]]
                self.assertEqual(len(field_ids), len(set(field_ids)))
                self.assertTrue(set(field_ids) <= set(template["required_slots"]))
                for field in template["professional_fields"]:
                    self.assertTrue(field["label"].strip())

    def test_common_templates_cover_all_roles(self):
        expected_roles = ["%02d" % n for n in range(7, 47)]
        for template_id in ("T01", "T02", "T31", "T32"):
            with self.subTest(template=template_id):
                self.assertEqual(expected_roles,
                                 templates.get_template(template_id)["roles"])

    def test_list_and_get_return_deep_copies(self):
        listed = templates.list_templates()
        listed[0]["roles"].clear()
        listed[0]["required_slots"].append("polluted")
        listed[0]["professional_fields"][0]["label"] = "污染"
        listed[0]["qa"].clear()
        fresh = templates.get_template("T01")
        self.assertTrue(fresh["roles"])
        self.assertNotIn("polluted", fresh["required_slots"])
        self.assertNotEqual("污染", fresh["professional_fields"][0]["label"])
        self.assertTrue(fresh["qa"])

        fresh["professional_fields"].clear()
        fresh["required_slots"].clear()
        self.assertTrue(templates.get_template("T01")["professional_fields"])
        self.assertTrue(templates.get_template("T01")["required_slots"])
        self.assertIsNone(templates.get_template("T33"))
        self.assertIsNone(templates.get_template(None))

    def test_registry_lint_passes_after_caller_mutations(self):
        self.assertEqual([], templates.lint_registry())
        template = templates.get_template("T12")
        template["roles"] = ["99"]
        template["professional_fields"][0]["field_id"] = "Bad Slot"
        self.assertEqual([], templates.lint_registry())


class TemplateInstanceTests(unittest.TestCase):
    def test_valid_instance_for_every_template(self):
        for template in templates.list_templates():
            with self.subTest(template=template["template_id"]):
                errors = templates.lint_instance(
                    template["template_id"], template["version"],
                    valid_fields(template))
                self.assertEqual([], errors)

    def test_missing_required_slots(self):
        template = templates.get_template("T03")
        errors = templates.lint_instance("T03", template["version"], {})
        self.assertEqual(len(template["required_slots"]), len(errors))
        self.assertTrue(all(error.startswith("missing required slot: ")
                            for error in errors))
        self.assertIn("missing required slot: big_idea", errors)
        self.assertIn("missing required slot: evidence_refs", errors)

    def test_blank_and_empty_values_are_rejected_but_zero_and_false_are_valid(self):
        template = templates.get_template("T05")
        fields = valid_fields(template)
        fields["owner"] = "  "
        fields["budget"] = None
        fields["contributors"] = []
        errors = templates.lint_instance("T05", template["version"], fields)
        self.assertIn("empty required slot: owner", errors)
        self.assertIn("empty required slot: budget", errors)
        self.assertIn("empty required slot: contributors", errors)

        fields = valid_fields(template)
        fields["owner"] = 0
        fields["budget"] = False
        self.assertEqual([], templates.lint_instance("T05", template["version"], fields))

    def test_version_unknown_template_and_fields_type_return_errors(self):
        template = templates.get_template("T30")
        errors = templates.lint_instance("T30", "0.9.0", valid_fields(template))
        self.assertEqual(1, len(errors))
        self.assertIn("version mismatch", errors[0])
        self.assertIn("unknown template: T99",
                      templates.lint_instance("T99", "1.0.0", {}))
        self.assertIn("unknown template: None",
                      templates.lint_instance(None, "1.0.0", {}))
        self.assertEqual(["fields must be a flat dictionary"],
                         templates.lint_instance("T30", template["version"], None))

    def test_evidence_reference_structure(self):
        template = templates.get_template("T28")
        bad_values = [
            None,
            "source-001",
            {"source_id": "source-001", "locator": "uri"},
            [],
            ["source-001"],
            [{}],
            [{"source_id": "source-001"}],
            [{"locator": "https://example.test/source"}],
            [{"source_id": " ", "locator": "https://example.test/source"}],
            [{"source_id": "source-001", "locator": " "}],
            [{"source_id": "source-001", "locator": "uri", "version": ""}],
        ]
        for value in bad_values:
            with self.subTest(value=value):
                fields = valid_fields(template)
                fields["evidence_refs"] = value
                self.assertTrue(templates.lint_instance(
                    "T28", template["version"], fields))

        fields = valid_fields(template)
        fields["evidence_refs"] = [
            {"source_id": "source-001", "locator": "ledger/events.jsonl#L1"},
            {"source_id": "source-002", "locator": "https://example.test/source",
             "version": "2"},
        ]
        self.assertEqual([], templates.lint_instance("T28", template["version"], fields))

    def test_overlay_fields_are_allowed(self):
        template = templates.get_template("T14")
        fields = valid_fields(template)
        fields["regional_overlay"] = "EU-v1"
        self.assertEqual([], templates.lint_instance(
            "T14", template["version"], fields))


if __name__ == "__main__":
    unittest.main()
