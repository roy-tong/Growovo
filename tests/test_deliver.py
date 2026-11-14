"""结果单落盘与投递的契约测试：HTML 渲染/转义、卡片落盘、dry-run 邮件。

全部用例只用标准库与临时目录：默认 dry-run 不建 SMTP 连接；需要覆盖
dry_run=False 的路径时，要么只测「缺配置报错」，要么把 smtplib.SMTP_SSL
替换成 mock，绝不真正联网。
"""
import os
import tempfile
import unittest
from email import policy as email_policy
from email.parser import BytesParser
from pathlib import Path
from unittest import mock

from core.deliver import (
    EMAIL_PASSWORD_ENV,
    EMAIL_USER_ENV,
    SMTP_HOST,
    SMTP_PORT,
    DeliverError,
    deliver_card,
    deliver_email,
    render_html,
    write_card,
)

SCRIPT_MARKDOWN = "# 安全测试\n\n<script>alert('xss')</script>\n"


class TempDirTest(unittest.TestCase):
    """每个用例一个临时目录，用完即删。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)


class RenderHtmlTests(unittest.TestCase):
    """最小 Markdown 渲染：结构正确、该转义的都转义、输出自包含。"""

    def test_heading_levels(self):
        html = render_html("# 一级\n\n## 二级\n\n### 三级")
        self.assertIn("<h1>一级</h1>", html)
        self.assertIn("<h2>二级</h2>", html)
        self.assertIn("<h3>三级</h3>", html)

    def test_heading_inline_bold(self):
        self.assertIn("<h2>本周 <strong>12 条</strong></h2>",
                      render_html("## 本周 **12 条**"))

    def test_unordered_list(self):
        html = render_html("- x：回复 8 条\n- email：发信 4 封")
        self.assertIn("<ul>", html)
        self.assertIn("<li>x：回复 8 条</li>", html)
        self.assertIn("<li>email：发信 4 封</li>", html)
        self.assertIn("</ul>", html)

    def test_table_renders_header_and_rows(self):
        markdown = ("| 指标 | 数值 |\n"
                    "| --- | --- |\n"
                    "| 发布量 | 12 |\n"
                    "| 新增粉丝 | 7 |")
        html = render_html(markdown)
        self.assertIn("<table>", html)
        self.assertIn("<thead>", html)
        self.assertIn("<th>指标</th>", html)
        self.assertIn("<th>数值</th>", html)
        self.assertIn("<tbody>", html)
        self.assertIn("<td>发布量</td>", html)
        self.assertIn("<td>12</td>", html)
        self.assertIn("<td>新增粉丝</td>", html)
        # 分隔行只用于识别表格，不能原样出现在输出里
        self.assertNotIn("<td>---</td>", html)
        self.assertNotIn("| --- |", html)

    def test_table_cells_are_escaped(self):
        markdown = ("| 列 | 值 |\n"
                    "| --- | --- |\n"
                    "| <script> | 1 |")
        html = render_html(markdown)
        self.assertIn("&lt;script&gt;", html)
        self.assertNotIn("<td><script>", html)

    def test_bold(self):
        html = render_html("本周发了 **12 条**，全部有账本记录。")
        self.assertIn("<strong>12 条</strong>", html)
        self.assertIn("本周发了 ", html)

    def test_paragraph(self):
        html = render_html("第一段。\n\n第二段。")
        self.assertIn("<p>第一段。</p>", html)
        self.assertIn("<p>第二段。</p>", html)

    def test_code_block_preserved_and_escaped(self):
        markdown = ("```python\n"
                    "print(\"<script>alert(1)</script>\")\n"
                    "```")
        html = render_html(markdown)
        self.assertIn('<pre><code class="language-python">', html)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", html)
        self.assertNotIn("<script>alert(1)</script>", html)

    def test_script_is_escaped(self):
        html = render_html(SCRIPT_MARKDOWN)
        self.assertIn("&lt;script&gt;alert(&#x27;xss&#x27;)&lt;/script&gt;", html)
        self.assertNotIn("<script>", html)

    def test_escape_before_bold(self):
        """先转义再做粗体替换，粗体里塞标签也只会变成文本。"""
        html = render_html("**<b>不是加粗标签</b>**")
        self.assertIn("<strong>&lt;b&gt;不是加粗标签&lt;/b&gt;</strong>", html)
        self.assertNotIn("<b>", html)

    def test_ampersand_escaped(self):
        self.assertIn("收益 &amp; 风险", render_html("收益 & 风险"))

    def test_full_self_contained_document(self):
        html = render_html("# 结果单")
        self.assertTrue(html.startswith("<!DOCTYPE html>"))
        self.assertIn("<html", html)
        self.assertIn("<head>", html)
        self.assertIn("<body>", html)
        self.assertIn("<style>", html)
        self.assertTrue(html.rstrip().endswith("</html>"))
        # 无任何外部资源：样式内联，不引 CSS/JS/字体/图片
        for forbidden in ("<link", "<script", "@import", "url(", "src=",
                          "http://", "https://"):
            self.assertNotIn(forbidden, html, forbidden)

    def test_title_written_and_escaped(self):
        html = render_html("# 正文标题", title="周结果单 <W1>")
        self.assertIn("<title>周结果单 &lt;W1&gt;</title>", html)
        # 正文已有 H1 时不再重复补可见标题
        self.assertNotIn('<h1 class="page-title">', html)

    def test_title_falls_back_to_first_heading(self):
        html = render_html("# 我的结果单\n\n正文")
        self.assertIn("<title>我的结果单</title>", html)

    def test_title_adds_visible_header_without_h1(self):
        html = render_html("只有段落。", title="卡片标题")
        self.assertIn('<h1 class="page-title">卡片标题</h1>', html)
        self.assertIn("<title>卡片标题</title>", html)

    def test_empty_markdown_is_valid_document(self):
        html = render_html("")
        self.assertIn("<title>Growovo 结果单</title>", html)
        self.assertIn("</html>", html)


class WriteCardTests(TempDirTest):
    """write_card 只落盘，不发送；路径与内容按契约固定。"""

    def test_paths_and_file_content(self):
        markdown = "# 周结果单 W1\n\n发布 **12** 条\n"
        paths = write_card(self.dir, "g1", "week", "W1", markdown, title="W1")
        expected_md = self.dir / "grow" / "g1" / "cards" / "week-W1.md"
        expected_html = self.dir / "grow" / "g1" / "cards" / "week-W1.html"
        self.assertEqual(str(expected_md), paths["markdown"])
        self.assertEqual(str(expected_html), paths["html"])
        self.assertTrue(expected_md.is_file())
        self.assertTrue(expected_html.is_file())
        self.assertEqual(markdown, expected_md.read_text(encoding="utf-8"))
        html = expected_html.read_text(encoding="utf-8")
        self.assertIn("<h1>周结果单 W1</h1>", html)
        self.assertIn("<strong>12</strong>", html)

    def test_html_escapes_script_in_card(self):
        paths = write_card(self.dir, "g1", "day", "D1", SCRIPT_MARKDOWN)
        html = Path(paths["html"]).read_text(encoding="utf-8")
        self.assertIn("&lt;script&gt;", html)
        self.assertNotIn("<script>", html)

    def test_creates_nested_directories(self):
        paths = write_card(self.dir, "2026-11-14-run", "week", "W2", "正文")
        self.assertTrue(Path(paths["markdown"]).parent.is_dir())

    def test_same_card_overwrites_in_place(self):
        first = write_card(self.dir, "g1", "week", "W1", "第一版")
        second = write_card(self.dir, "g1", "week", "W1", "第二版")
        self.assertEqual(first, second)
        self.assertEqual("第二版",
                         Path(second["markdown"]).read_text(encoding="utf-8"))

    def test_rejects_path_traversal(self):
        with self.assertRaises(DeliverError):
            write_card(self.dir, "../escape", "week", "W1", "正文")
        with self.assertRaises(DeliverError):
            write_card(self.dir, "g1", "a/b", "W1", "正文")
        self.assertFalse((self.dir.parent / "escape").exists())

    def test_rejects_empty_components(self):
        with self.assertRaises(DeliverError):
            write_card(self.dir, "g1", "week", "", "正文")


class _EmailFixtures:
    """邮件相关用例共用的 Markdown 与解析辅助。"""

    markdown = ("# 周结果单 W1\n\n"
                "发布 **12 条**。\n\n"
                "| 指标 | 数值 |\n| --- | --- |\n| 发布量 | 12 |\n")

    @staticmethod
    def _parse(path):
        return BytesParser(policy=email_policy.default).parsebytes(
            Path(path).read_bytes())

    def _only_eml(self, outbox):
        files = sorted(Path(outbox).glob("*.eml"))
        self.assertEqual(1, len(files), files)
        return files[0]


class DeliverEmailDryRunTests(TempDirTest, _EmailFixtures):
    """dry_run 默认值：只写 .eml，绝不联网。"""

    def test_dry_run_writes_eml_with_headers(self):
        outbox = self.dir / "outbox"
        result = deliver_email(self.markdown, to="owner@example.com",
                               subject="Weekly result W1", outbox_dir=outbox)
        self.assertFalse(result["sent"])
        self.assertTrue(result["dry_run"])
        self.assertEqual("owner@example.com", result["to"])
        self.assertEqual("Weekly result W1", result["subject"])
        self.assertEqual("", result["error"])
        eml = Path(result["path"])
        self.assertTrue(eml.is_file())
        text = eml.read_text(encoding="utf-8")
        self.assertIn("To: owner@example.com", text)
        self.assertIn("Subject: Weekly result W1", text)
        self.assertIn("MIME-Version: 1.0", text)
        self.assertIn("Content-Type:", text)
        self.assertIn("text/plain", text)

    def test_dry_run_does_not_open_smtp(self):
        outbox = self.dir / "outbox"
        with mock.patch("core.deliver.smtplib.SMTP_SSL",
                        side_effect=AssertionError("dry-run 不允许联网")) as smtp:
            deliver_email(self.markdown, to="owner@example.com",
                          subject="Weekly W1", outbox_dir=outbox)
            smtp.assert_not_called()

    def test_dry_run_body_contains_markdown_and_html(self):
        outbox = self.dir / "outbox"
        markdown = "# 结果单\n\n发布 **12 条**\n\n<script>alert(1)</script>\n"
        result = deliver_email(markdown, to="owner@example.com",
                               subject="Card", outbox_dir=outbox)
        message = self._parse(result["path"])
        plain = message.get_body(preferencelist=("plain",)).get_content()
        html = message.get_body(preferencelist=("html",)).get_content()
        self.assertIn("# 结果单", plain)
        self.assertIn("发布 **12 条**", plain)
        self.assertIn("<h1>结果单</h1>", html)
        self.assertIn("<strong>12 条</strong>", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertNotIn("<script>", html)

    def test_chinese_subject_round_trips(self):
        result = deliver_email(self.markdown, to="owner@example.com",
                               subject="周结果单 W1", outbox_dir=self.dir / "outbox")
        message = self._parse(result["path"])
        self.assertEqual("周结果单 W1", message["Subject"])
        self.assertEqual("owner@example.com", message["To"])

    def test_outbox_is_created(self):
        outbox = self.dir / "deep" / "nested" / "outbox"
        deliver_email(self.markdown, to="owner@example.com",
                      subject="Card", outbox_dir=outbox)
        self.assertTrue(outbox.is_dir())
        self._only_eml(outbox)

    def test_dry_run_requires_outbox_dir(self):
        with self.assertRaises(DeliverError):
            deliver_email(self.markdown, to="owner@example.com",
                          subject="Card", dry_run=True)

    def test_rejects_empty_recipient_or_subject(self):
        with self.assertRaises(DeliverError):
            deliver_email(self.markdown, to="", subject="Card",
                          outbox_dir=self.dir / "outbox")
        with self.assertRaises(DeliverError):
            deliver_email(self.markdown, to="owner@example.com", subject="",
                          outbox_dir=self.dir / "outbox")


class DeliverEmailLiveTests(TempDirTest, _EmailFixtures):
    """dry_run=False 的路径：缺配置报错；有配置时用 mock 掉 SMTP，不联网。"""

    def test_missing_credentials_raises_without_connecting(self):
        with mock.patch.dict(os.environ,
                             {EMAIL_USER_ENV: "", EMAIL_PASSWORD_ENV: ""}):
            with mock.patch("core.deliver.smtplib.SMTP_SSL") as smtp:
                with self.assertRaises(DeliverError):
                    deliver_email(self.markdown, to="owner@example.com",
                                  subject="Card", dry_run=False)
                smtp.assert_not_called()

    def test_partial_credentials_raises(self):
        with mock.patch.dict(os.environ,
                             {EMAIL_USER_ENV: "bot@example.com",
                              EMAIL_PASSWORD_ENV: ""}):
            with mock.patch("core.deliver.smtplib.SMTP_SSL") as smtp:
                with self.assertRaises(DeliverError):
                    deliver_email(self.markdown, to="owner@example.com",
                                  subject="Card", dry_run=False)
                smtp.assert_not_called()

    def test_live_send_uses_configured_smtp(self):
        env = {EMAIL_USER_ENV: "bot@example.com",
               EMAIL_PASSWORD_ENV: "app-password"}
        with mock.patch.dict(os.environ, env):
            with mock.patch("core.deliver.smtplib.SMTP_SSL") as smtp_cls:
                smtp = smtp_cls.return_value.__enter__.return_value
                result = deliver_email(self.markdown, to="owner@example.com",
                                       subject="Card", dry_run=False)
        self.assertTrue(result["sent"])
        self.assertFalse(result["dry_run"])
        self.assertEqual("", result["error"])
        smtp_cls.assert_called_once_with(SMTP_HOST, SMTP_PORT, timeout=30)
        smtp.login.assert_called_once_with("bot@example.com", "app-password")
        smtp.send_message.assert_called_once()

    def test_smtp_failure_is_reported_not_raised(self):
        env = {EMAIL_USER_ENV: "bot@example.com",
               EMAIL_PASSWORD_ENV: "app-password"}
        with mock.patch.dict(os.environ, env):
            with mock.patch("core.deliver.smtplib.SMTP_SSL") as smtp_cls:
                smtp = smtp_cls.return_value.__enter__.return_value
                smtp.login.side_effect = OSError("connection refused")
                result = deliver_email(self.markdown, to="owner@example.com",
                                       subject="Card", dry_run=False)
        self.assertFalse(result["sent"])
        self.assertIn("OSError", result["error"])
        self.assertIn("connection refused", result["error"])


class DeliverCardTests(TempDirTest, _EmailFixtures):
    """deliver_card = write_card +（to 非空时）deliver_email。"""

    def test_writes_card_and_dry_run_eml_into_run_outbox(self):
        result = deliver_card(self.dir, "g1", "week", "W1", self.markdown,
                              title="W1 周结果单", to="owner@example.com",
                              subject="Weekly W1")
        markdown_path = self.dir / "grow" / "g1" / "cards" / "week-W1.md"
        html_path = self.dir / "grow" / "g1" / "cards" / "week-W1.html"
        self.assertEqual(str(markdown_path), result["markdown"])
        self.assertEqual(str(html_path), result["html"])
        self.assertTrue(markdown_path.is_file())
        self.assertTrue(html_path.is_file())
        self.assertFalse(result["sent"])
        self.assertTrue(result["dry_run"])
        self.assertEqual("owner@example.com", result["to"])
        eml = Path(result["path"])
        self.assertEqual(self.dir / "grow" / "g1" / "outbox", eml.parent)
        self.assertTrue(eml.is_file())
        self.assertIn("Subject: Weekly W1",
                      eml.read_text(encoding="utf-8"))
        self.assertIsNotNone(result["delivery"])
        self.assertEqual(result["path"], result["delivery"]["path"])

    def test_without_recipient_skips_delivery(self):
        result = deliver_card(self.dir, "g1", "day", "D1", self.markdown)
        self.assertEqual("", result["to"])
        self.assertIsNone(result["delivery"])
        self.assertIsNone(result["path"])
        self.assertFalse(result["sent"])
        self.assertFalse((self.dir / "grow" / "g1" / "outbox").exists())

    def test_custom_outbox_dir(self):
        outbox = self.dir / "elsewhere" / "outbox"
        result = deliver_card(self.dir, "g1", "week", "W1", self.markdown,
                              to="owner@example.com", outbox_dir=outbox)
        self.assertEqual(outbox, Path(result["path"]).parent)

    def test_subject_falls_back_to_title(self):
        result = deliver_card(self.dir, "g1", "week", "W1", self.markdown,
                              title="W1 周结果单", to="owner@example.com")
        self.assertEqual("W1 周结果单", result["subject"])

    def test_live_without_credentials_raises_after_card_written(self):
        with mock.patch.dict(os.environ,
                             {EMAIL_USER_ENV: "", EMAIL_PASSWORD_ENV: ""}):
            with mock.patch("core.deliver.smtplib.SMTP_SSL") as smtp:
                with self.assertRaises(DeliverError):
                    deliver_card(self.dir, "g1", "week", "W1", self.markdown,
                                 to="owner@example.com", live=True)
                smtp.assert_not_called()
        self.assertTrue((self.dir / "grow" / "g1" / "cards" / "week-W1.md").is_file())


if __name__ == "__main__":
    unittest.main()
