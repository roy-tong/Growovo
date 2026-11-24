import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from core import seo_audit
from tests.geo_fixtures import entity_document


class Handler(BaseHTTPRequestHandler):
    def _send(self, body, content_type="text/html; charset=utf-8", status=200):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        port = self.server.server_port
        base = "http://127.0.0.1:%d" % port
        clean = """<!doctype html><html><head>
        <title>Growovo authorized growth operations</title>
        <meta name="description" content="Growovo runs authorized small-batch growth work with receipts and explicit boundaries.">
        <link rel="canonical" href="%s/">
        <link rel="alternate" hreflang="en" href="%s/en">
        <script type="application/ld+json">{"@context":"https://schema.org","@type":"SoftwareApplication","name":"Growovo","url":"%s/"}</script>
        </head><body><h1>Growovo</h1><p>Authorized growth operations with receipts.</p>
        <a href="/about">About</a></body></html>""" % (base, base, base)
        city = """<!doctype html><html><head>
        <title>Growovo authorized growth operations in %s</title>
        <meta name="description" content="Growovo runs authorized small-batch growth work with receipts and explicit boundaries in %s.">
        <link rel="canonical" href="%s/city/%s">
        <script type="application/ld+json">{"@type":"SoftwareApplication","name":"Growovo"}</script>
        </head><body><h1>Growovo %s</h1><p>Authorized growth operations with receipts for local teams in %s.</p></body></html>"""
        if self.path == "/":
            self._send(clean)
        elif self.path == "/about":
            self._send(clean.replace("<h1>Growovo</h1>", "<h1>About Growovo</h1>"))
        elif self.path == "/duplicate":
            self._send(clean)
        elif self.path in ("/city/beijing", "/city/shanghai", "/city/shenzhen"):
            name = self.path.rsplit("/", 1)[-1]
            self._send(city % (name, name, base, name, name, name))
        elif self.path == "/robots.txt":
            self._send("User-agent: *\nDisallow: /blocked\nAllow: /\n", "text/plain")
        elif self.path == "/sitemap.xml":
            urls = ["", "/about", "/duplicate", "/city/beijing",
                    "/city/shanghai", "/city/shenzhen"]
            body = "<?xml version=\"1.0\"?><urlset>" + "".join(
                "<url><loc>%s%s</loc></url>" % (base, path) for path in urls) + "</urlset>"
            self._send(body, "application/xml")
        elif self.path == "/llms.txt":
            self._send("Growovo: authorized growth operations.", "text/plain")
        elif self.path == "/blocked":
            self._send(clean)
        elif self.path == "/bad":
            self._send("<html><body><p>No title or h1.</p>"
                       "<script type=\"application/ld+json\">{not-json}</script></body></html>")
        else:
            self._send("not found", "text/plain", 404)

    def log_message(self, *args):
        pass


class SeoAuditTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = "http://127.0.0.1:%d" % cls.server.server_port

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def checks(self, result):
        return {row["id"]: row for row in result["checks"]}

    def test_clean_page_passes_core_checks(self):
        result = seo_audit.audit(self.base + "/", entity=entity_document(), sample=1)
        checks = self.checks(result)
        for check_id in ("fetch", "robots", "sitemap", "canonical", "hreflang",
                         "title", "description", "h1", "structured_data",
                         "facts_match", "llms_txt", "doorway"):
            self.assertEqual("pass", checks[check_id]["status"], check_id)
        self.assertEqual("unknown", checks["index_status"]["status"])

    def test_robots_disallow_is_a_fail(self):
        result = seo_audit.audit(self.base + "/blocked", entity=entity_document(), sample=1)
        self.assertEqual("fail", self.checks(result)["robots"]["status"])

    def test_bad_page_flags_missing_and_invalid_fields(self):
        result = seo_audit.audit(self.base + "/bad", entity=entity_document(), sample=1)
        checks = self.checks(result)
        self.assertEqual("fail", checks["title"]["status"])
        self.assertEqual("warn", checks["h1"]["status"])
        self.assertEqual("fail", checks["structured_data"]["status"])

    def test_duplicate_and_city_template_are_flagged(self):
        result = seo_audit.audit(self.base + "/", entity=entity_document(), sample=10)
        checks = self.checks(result)
        self.assertEqual("warn", checks["duplicate"]["status"])
        self.assertEqual("fail", checks["doorway"]["status"])

    def test_result_is_saved(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        result = seo_audit.audit(self.base + "/", entity=entity_document(), sample=1)
        path = seo_audit.save(tmp.name, result)
        loaded = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(result["audit_id"], loaded["audit_id"])
        self.assertIn("checks", loaded)


if __name__ == "__main__":
    unittest.main()
