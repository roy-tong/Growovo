import json
import tempfile
import unittest
from pathlib import Path

from core import geo_entity
from tests.geo_fixtures import entity_document


class GeoEntityTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = self.tmp.name

    def test_init_check_and_update(self):
        result = geo_entity.init(self.data, entity_document())
        self.assertEqual("growovo", result["entity"]["entity_id"])
        self.assertEqual(1, result["entity"]["version"])
        checked = geo_entity.check(self.data)
        self.assertTrue(checked["ok"])

        updated = entity_document()
        updated["canonical"]["one_line"] = "Authorized growth operations with receipts"
        saved = geo_entity.init(self.data, updated)
        self.assertEqual(2, saved["entity"]["version"])
        self.assertIn("receipts", geo_entity.load(self.data)["canonical"]["one_line"])

    def test_same_content_is_unchanged(self):
        first = geo_entity.init(self.data, entity_document())
        again = geo_entity.init(self.data, entity_document())
        self.assertEqual("unchanged", again["status"])
        self.assertEqual(first["fingerprint"], again["fingerprint"])

    def test_update_without_current_version_is_rejected(self):
        geo_entity.init(self.data, entity_document())
        changed = entity_document()
        changed["canonical"]["category"] = "Other"
        changed.pop("version")
        with self.assertRaises(ValueError):
            geo_entity.init(self.data, changed)

    def test_missing_limits_is_rejected(self):
        document = entity_document()
        document["canonical"]["capabilities"][0]["limits"] = ""
        with self.assertRaises(ValueError) as ctx:
            geo_entity.init(self.data, document)
        self.assertIn("limits", str(ctx.exception))

    def test_bad_source_hash_is_rejected(self):
        document = entity_document()
        document["fact_sources"][0]["sha256"] = "not-a-hash"
        with self.assertRaises(ValueError) as ctx:
            geo_entity.init(self.data, document)
        self.assertIn("sha256", str(ctx.exception))

    def test_missing_entity_has_an_actionable_error(self):
        with self.assertRaises(ValueError) as ctx:
            geo_entity.load(self.data)
        self.assertIn("entity init", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
