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

    def test_one_line_uses_containment_not_document_jaccard(self):
        self.write("README.md", "Growovo\nAgent-native growth operations for authorized small-batch work\n"
                                "https://example.com https://github.com/example/growovo\n"
                                + "unrelated filler content for other sections " * 200)
        result = geo_facts.check(str(self.data), str(self.repo))
        checks = {(row["asset"], row["check"]): row for row in result["checks"]}
        row = checks[("README.md", "one_line")]
        self.assertEqual("pass", row["status"])
        self.assertGreater(row["evidence"]["overlap"], 0.9)

    def test_chinese_readme_skips_one_line_overlap(self):
        self.write("README.zh-CN.md", "Growovo 是一个授权的小批量增长运营工具 "
                                      "https://example.com https://github.com/example/growovo\n")
        result = geo_facts.check(str(self.data), str(self.repo))
        checks = {(row["asset"], row["check"]): row for row in result["checks"]}
        self.assertEqual("not_applicable", checks[("README.zh-CN.md", "one_line")]["status"])

    def test_same_repo_with_git_suffix_is_not_foreign(self):
        self.write("README.md", "Growovo https://github.com/example/growovo.git https://example.com\n")
        result = geo_facts.check(str(self.data), str(self.repo))
        checks = {(row["asset"], row["check"]): row for row in result["checks"]}
        self.assertEqual("pass", checks[("README.md", "foreign_repo")]["status"])

    def test_package_name_mismatch_is_a_warning(self):
        self.write("pyproject.toml", '[project]\nname = "other-package"\ndescription = "Other"\n')
        result = geo_facts.check(str(self.data), str(self.repo))
        checks = {(row["asset"], row["check"]): row for row in result["checks"]}
        self.assertEqual("warn", checks[("pyproject.toml", "package_name")]["status"])


if __name__ == "__main__":
    unittest.main()
