import tempfile
import unittest
from pathlib import Path

from core import geo_entity, geo_facts
from tests.geo_fixtures import entity_document


class GeoFactsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = Path(self.tmp.name) / "data"
        self.repo = Path(self.tmp.name) / "repo"
        self.repo.mkdir()
        geo_entity.init(str(self.data), entity_document())

    def write(self, name, text):
        (self.repo / name).write_text(text, encoding="utf-8")

    def test_consistent_assets_pass(self):
        self.write("README.md", "Growovo\nAgent-native growth operations for authorized small-batch work\n"
                                "https://example.com https://github.com/example/growovo\n")
        self.write("llms.txt", "Growovo: https://github.com/example/growovo https://example.com\n")
        self.write("pyproject.toml", '[project]\nname = "growovo"\ndescription = "Agent-native growth operations for authorized small-batch work"\n')
        result = geo_facts.check(str(self.data), str(self.repo))
        self.assertEqual(0, result["summary"]["fail"])
        self.assertGreater(result["summary"]["pass"], 0)

    def test_missing_name_is_a_warning(self):
        self.write("README.md", "Some other product https://example.com\n")
        result = geo_facts.check(str(self.data), str(self.repo))
        checks = {(row["asset"], row["check"]): row for row in result["checks"]}
        self.assertEqual("warn", checks[("README.md", "name")]["status"])

    def test_foreign_repo_is_a_warning(self):
        self.write("README.md", "Growovo https://github.com/other/project https://example.com\n")
        result = geo_facts.check(str(self.data), str(self.repo))
        checks = {(row["asset"], row["check"]): row for row in result["checks"]}
        self.assertEqual("warn", checks[("README.md", "foreign_repo")]["status"])

    def test_package_name_mismatch_is_a_warning(self):
        self.write("pyproject.toml", '[project]\nname = "other-package"\ndescription = "Other"\n')
        result = geo_facts.check(str(self.data), str(self.repo))
        checks = {(row["asset"], row["check"]): row for row in result["checks"]}
        self.assertEqual("warn", checks[("pyproject.toml", "package_name")]["status"])


if __name__ == "__main__":
    unittest.main()
