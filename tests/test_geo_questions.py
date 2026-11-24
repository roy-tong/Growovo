import copy
import tempfile
import unittest

from core import geo_questions
from tests.geo_fixtures import question_document


class GeoQuestionsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = self.tmp.name

    def test_import_freeze_and_load(self):
        imported = geo_questions.import_set(self.data, question_document())
        self.assertEqual("imported", imported["status"])
        frozen = geo_questions.freeze(self.data, "growovo-test", 1)
        self.assertEqual("frozen", frozen["status"])
        loaded = geo_questions.load(self.data, "growovo-test")
        self.assertTrue(loaded["frozen_at"])
        self.assertEqual(frozen["content_sha256"], loaded["content_sha256"])

    def test_freeze_is_idempotent(self):
        geo_questions.import_set(self.data, question_document())
        first = geo_questions.freeze(self.data, "growovo-test")
        second = geo_questions.freeze(self.data, "growovo-test")
        self.assertEqual("already_frozen", second["status"])
        self.assertEqual(first["content_sha256"], second["content_sha256"])

    def test_duplicate_text_is_rejected(self):
        document = question_document()
        document["questions"][1]["text"] = document["questions"][0]["text"]
        with self.assertRaises(ValueError) as ctx:
            geo_questions.import_set(self.data, document)
        self.assertIn("重复", str(ctx.exception))

    def test_missing_success_reason_is_rejected(self):
        document = question_document()
        document["questions"][0]["success_reason"] = ""
        with self.assertRaises(ValueError) as ctx:
            geo_questions.import_set(self.data, document)
        self.assertIn("success_reason", str(ctx.exception))

    def test_frozen_version_cannot_be_overwritten(self):
        geo_questions.import_set(self.data, question_document())
        geo_questions.freeze(self.data, "growovo-test", 1)
        changed = copy.deepcopy(question_document())
        changed["questions"][0]["text"] = "different question"
        with self.assertRaises(ValueError) as ctx:
            geo_questions.import_set(self.data, changed)
        self.assertIn("v1", str(ctx.exception))

    def test_new_version_keeps_old_version(self):
        geo_questions.import_set(self.data, question_document())
        geo_questions.freeze(self.data, "growovo-test", 1)
        changed = copy.deepcopy(question_document())
        changed["version"] = 2
        changed["questions"][0]["text"] = "different question"
        imported = geo_questions.import_set(self.data, changed)
        self.assertEqual(2, imported["version"])
        self.assertEqual("open-source AI growth agent",
                         geo_questions.load(self.data, "growovo-test", 1)["questions"][0]["text"])

    def test_tampered_frozen_hash_is_rejected(self):
        geo_questions.import_set(self.data, question_document())
        geo_questions.freeze(self.data, "growovo-test", 1)
        path = geo_questions.path(self.data, "growovo-test", 1)
        document = geo_questions.load(self.data, "growovo-test", 1)
        document["questions"][0]["text"] = "tampered"
        from core.storage import AtomicJSONStore
        AtomicJSONStore(path).save(document)
        with self.assertRaises(ValueError) as ctx:
            geo_questions.load(self.data, "growovo-test", 1)
        self.assertIn("content_sha256", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
