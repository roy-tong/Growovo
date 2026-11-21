#!/usr/bin/env python3
"""The wheel only ships what `packages` lists; a missed subpackage ships broken."""
import ast
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKIP_DIRS = {".git", "build", "dist", "tests", "profiles", "data", "node_modules",
             ".venv", ".pytest_cache", "__pycache__", "website", "assets"}


def declared_packages() -> list:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    section = re.search(r"^\[tool\.setuptools\]\s*$(.*?)(?=^\[)", text,
                        re.MULTILINE | re.DOTALL)
    if not section:
        raise AssertionError("pyproject.toml has no [tool.setuptools] section")
    match = re.search(r"^packages\s*=\s*(\[.*?\])\s*$", section.group(1),
                      re.MULTILINE | re.DOTALL)
    if not match:
        raise AssertionError("[tool.setuptools] lists no packages")
    return ast.literal_eval(match.group(1))


def package_dirs() -> set:
    found = set()
    for path in ROOT.rglob("__init__.py"):
        rel = path.parent.relative_to(ROOT)
        if any(part in SKIP_DIRS for part in rel.parts):
            continue
        found.add(".".join(rel.parts) if rel.parts else "")
    return found


class PackagingTest(unittest.TestCase):
    def test_every_package_directory_is_declared(self):
        declared = set(declared_packages())
        on_disk = package_dirs()
        self.assertEqual(sorted(on_disk - declared), [],
                         "package directories missing from [tool.setuptools] packages; "
                         "a wheel built from this would fail on import")

    def test_every_declared_package_exists(self):
        missing = [name for name in declared_packages()
                   if not (ROOT / name.replace(".", "/") / "__init__.py").is_file()]
        self.assertEqual(missing, [])

    def test_browser_bridge_is_declared(self):
        self.assertIn("core.bridge", declared_packages())


if __name__ == "__main__":
    unittest.main()
