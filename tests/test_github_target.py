#!/usr/bin/env python3
"""GitHub 回复 target 解析：短格式与 issue URL 等价。

队列里两种写法都出现过；runner 只认短格式会让 URL 行在波次里静默失败。
"""
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.action_runner import ActionError, _parse_issue


class GithubTargetTest(unittest.TestCase):
    def test_short_form(self):
        self.assertEqual(_parse_issue("DemonGiggle/q-agent#66"),
                         ("DemonGiggle/q-agent", 66))

    def test_prefixed_short_form(self):
        self.assertEqual(_parse_issue("issue:foo/bar#3"), ("foo/bar", 3))

    def test_issue_url(self):
        self.assertEqual(
            _parse_issue("https://github.com/DanielH2018/dotfiles/issues/1452"),
            ("DanielH2018/dotfiles", 1452))

    def test_pull_url_is_not_an_issue(self):
        with self.assertRaises(ActionError):
            _parse_issue("https://github.com/foo/bar/pull/7")


if __name__ == "__main__":
    unittest.main()
