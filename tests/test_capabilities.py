"""GTM OS 能力域 / 岗位族注册表契约。"""
import unittest

from core import capabilities


EXPECTED_CAPABILITY_ROLES = {
    "C01": ["07", "08"],
    "C02": ["09", "10", "11"],
    "C03": ["12", "15"],
    "C04": ["13"],
    "C05": ["14"],
    "C06": ["16", "17", "22"],
    "C07": ["18"],
    "C08": ["19"],
    "C09": ["20", "21", "23", "32"],
    "C10": ["24", "25"],
    "C11": ["26", "27", "28"],
    "C12": ["29", "30", "31", "33"],
    "C13": ["34", "35", "36", "37"],
    "C14": ["38", "39", "40"],
    "C15": ["41", "42"],
    "C16": ["43", "44"],
    "C17": ["45"],
    "C18": ["46"],
}


class CapabilityRegistryTests(unittest.TestCase):
    def test_exact_18_40_coverage_and_documented_mapping(self):
        rows = capabilities.list_capabilities()
        roles = capabilities.list_role_families()
        self.assertEqual(18, len(rows))
        self.assertEqual(40, len(roles))
        self.assertEqual(["C%02d" % n for n in range(1, 19)],
                         [row["capability_id"] for row in rows])
        self.assertEqual(["%02d" % n for n in range(7, 47)],
                         [row["role_id"] for row in roles])
        self.assertEqual(EXPECTED_CAPABILITY_ROLES,
                         {row["capability_id"]: row["role_ids"] for row in rows})
        for capability_id, role_ids in EXPECTED_CAPABILITY_ROLES.items():
            for role_id in role_ids:
                self.assertEqual(capability_id,
                                 capabilities.get_role_family(role_id)["domain"])

    def test_every_role_has_complete_professional_contract(self):
        required = {
            "role_id", "name", "domain", "mission", "responsibilities",
            "result_kpis", "process_kpis", "guardrail_kpis", "sop_steps",
            "deliverables", "template_refs", "handoff", "human_approval", "dod",
        }
        for role in capabilities.list_role_families():
            with self.subTest(role=role["role_id"]):
                self.assertTrue(required <= set(role))
                self.assertTrue(role["mission"].strip())
                for field in ("responsibilities", "result_kpis", "process_kpis",
                              "guardrail_kpis", "deliverables", "human_approval", "dod"):
                    self.assertTrue(role[field], field)
                    self.assertTrue(all(str(item).strip() for item in role[field]))
                self.assertGreaterEqual(len(role["sop_steps"]), 5)
                self.assertLessEqual(len(role["sop_steps"]), 8)
                self.assertEqual(len(role["sop_steps"]),
                                 len({step["step_id"] for step in role["sop_steps"]}))
                for step in role["sop_steps"]:
                    self.assertTrue(step["step_id"].strip())
                    self.assertTrue(step["summary"].strip())
                self.assertTrue({"T01", "T02", "T31", "T32"} <=
                                set(role["template_refs"]))
                self.assertTrue(set(role["template_refs"]) -
                                {"T01", "T02", "T31", "T32"})
                self.assertTrue(role["handoff"]["receivers"])
                self.assertTrue(role["handoff"]["package"].strip())
                self.assertTrue(role["handoff"]["acceptance"].strip())
                self.assertNotIn(role["role_id"], role["handoff"]["receivers"])

    def test_list_and_get_return_deep_copies(self):
        listed = capabilities.list_capabilities()
        listed[0]["name"] = "污染"
        listed[0]["role_ids"].append("46")
        listed[0]["outputs"].clear()
        fresh = capabilities.get_capability("C01")
        self.assertEqual("品牌与公共传播", fresh["name"])
        self.assertEqual(["07", "08"], fresh["role_ids"])
        self.assertTrue(fresh["outputs"])

        fresh["role_ids"].append("09")
        fresh["outputs"][0] = "污染"
        self.assertEqual(["07", "08"], capabilities.get_capability("C01")["role_ids"])
        self.assertNotEqual("污染", capabilities.get_capability("C01")["outputs"][0])

        roles = capabilities.list_role_families()
        roles[0]["sop_steps"][0]["summary"] = "污染"
        roles[0]["handoff"]["receivers"].clear()
        roles[0]["result_kpis"].clear()
        role = capabilities.get_role_family("07")
        self.assertNotEqual("污染", role["sop_steps"][0]["summary"])
        self.assertTrue(role["handoff"]["receivers"])
        self.assertTrue(role["result_kpis"])

        role["handoff"]["package"] = "污染"
        role["sop_steps"].clear()
        self.assertNotEqual("污染", capabilities.get_role_family("07")["handoff"]["package"])
        self.assertEqual(7, len(capabilities.get_role_family("07")["sop_steps"]))

    def test_unknown_ids_return_none(self):
        self.assertIsNone(capabilities.get_capability("C19"))
        self.assertIsNone(capabilities.get_capability("07"))
        self.assertIsNone(capabilities.get_role_family("06"))
        self.assertIsNone(capabilities.get_role_family("47"))
        self.assertIsNone(capabilities.get_role_family(7))

    def test_registry_lint_passes_after_caller_mutations(self):
        self.assertEqual([], capabilities.lint_registry())
        role = capabilities.get_role_family("18")
        role["template_refs"] = ["T99"]
        role["handoff"]["receivers"] = ["99"]
        self.assertEqual([], capabilities.lint_registry())


if __name__ == "__main__":
    unittest.main()
