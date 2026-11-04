"""账号、代理与报价实验的 CLI 契约。"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class AccountBillingSurfaceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / "data"
        self.home.mkdir()
        self.env = dict(os.environ, MKT_HOME=str(self.home))

    def run_mkt(self, *args, ok=(0,)):
        proc = subprocess.run([sys.executable, str(ROOT / "mkt.py"), *args], cwd=ROOT,
                              env=self.env, text=True, capture_output=True)
        self.assertIn(proc.returncode, ok, proc.stdout + proc.stderr)
        return proc

    def test_account_lifecycle_and_proxy_binding(self):
        add = self.run_mkt("account", "add", "--platform", "x", "--handle", "@acct",
                           "--identity-type", "real_authorized", "--account-id", "acct-a",
                           "--owner", "Roy", "--ownership-evidence", "owner-doc",
                           "--authorization-evidence", "scope-doc")
        self.assertIn("candidate", add.stdout)
        self.run_mkt("account", "warm", "--account-id", "acct-a")
        self.run_mkt("account", "eligible", "--account-id", "acct-a",
                     "--by", "roy", "--evidence", "warming-review")
        check = self.run_mkt("account", "check", "--account-id", "acct-a")
        self.assertIn("acct-a", check.stdout)
        self.run_mkt("network", "bind", "--account-id", "acct-a", "--env-var", "REGION_NET",
                     "--purpose", "authorized_access")
        exported = self.run_mkt("network", "export")
        self.assertIn("REGION_NET", exported.stdout)
        self.assertIn('"quota_multiplier": 1', exported.stdout)
        self.assertNotIn("secret", exported.stdout.lower())

    def test_subscription_experiment_acceptance_is_not_payment(self):
        pricing = json.dumps({"model": "subscription", "monthly_fee": 100, "currency": "USD"})
        self.run_mkt("billing", "--experiment-id", "sub-1", "--pricing", pricing,
                     "--prospect", "example")
        self.run_mkt("billing", "--experiment-id", "sub-1", "--record-acceptance", "accepted",
                     "--by", "roy")
        summary = self.run_mkt("billing", "--summary")
        payload = json.loads(summary.stdout)
        self.assertEqual(1, payload["accepted"])
        self.assertIn("不等于已付款", payload["note"])


if __name__ == "__main__":
    unittest.main()
