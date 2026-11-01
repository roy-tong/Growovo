#!/usr/bin/env python3
"""Extract one CHANGELOG section for GitHub release notes.

Usage: python3 scripts/release_notes.py <version> <output.md>
"""
from __future__ import annotations

import pathlib
import re
import sys


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print("usage: release_notes.py <version> <output.md>", file=sys.stderr)
        return 2
    version, output = argv[1], argv[2]
    text = pathlib.Path("CHANGELOG.md").read_text(encoding="utf-8")
    pattern = r"^## " + re.escape(version) + r" [^\n]*\n(.*?)(?=^## |\Z)"
    match = re.search(pattern, text, re.M | re.S)
    if not match:
        print("missing CHANGELOG section for " + version, file=sys.stderr)
        return 1
    pathlib.Path(output).write_text(
        "# Growovo v" + version + "\n\n" + match.group(1).strip() + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
