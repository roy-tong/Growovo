#!/usr/bin/env python3
"""结果单落盘与邮件投递：Markdown → 自包含 HTML、卡片落盘、dry-run 邮件。

口径见 docs/grow-v2-实现契约.md §3：

- ``render_html`` 零第三方依赖，只认标题（#/##/###）、无序列表（-）、表格（|）、
  粗体（**x**）、段落和围栏代码块（```）；其余文本一律 HTML 转义。输出是一份
  自带全部 CSS 的完整 HTML 文档，不含任何外部资源（无 link/script/图片/字体 URL）。
- ``write_card`` 把结果单写到 ``data/grow/<run_id>/cards/<kind>-<label>.md`` 与
  ``.html``，返回两个文件路径。
- ``deliver_email`` 默认 ``dry_run=True``：只把 UTF-8 的 ``.eml`` 写进 outbox，
  绝不建立 SMTP 连接。只有显式 ``dry_run=False`` 且配好 MKT_EMAIL_USER /
  MKT_EMAIL_APP_PASSWORD 才连 smtp.gmail.com:465；缺配置是确定性错误，抛
  ``DeliverError``，网络/协议失败则不吞、作为 ``error`` 字段返回。
- ``deliver_card`` 组合两者：先落盘，``to`` 非空时再按 dry-run 语义投递。

纪律：模块内不硬编码仓库 ``data/`` 目录，``data_dir`` / ``outbox_dir`` 一律由
调用方传入；卡片路径只接受单层安全文件名，拒绝路径穿越。
"""
from __future__ import annotations

import html as _html
import os
import re
import smtplib
from datetime import datetime, timezone
from email import policy as _email_policy
from email.message import EmailMessage
from email.utils import formatdate
from pathlib import Path
from typing import Any, Dict, List, Tuple

__all__ = [
    "DeliverError",
    "render_html",
    "write_card",
    "deliver_email",
    "deliver_card",
    "SMTP_HOST",
    "SMTP_PORT",
    "EMAIL_USER_ENV",
    "EMAIL_PASSWORD_ENV",
]

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 465
EMAIL_USER_ENV = "MKT_EMAIL_USER"
EMAIL_PASSWORD_ENV = "MKT_EMAIL_APP_PASSWORD"
DEFAULT_FROM = "growovo@localhost"
DEFAULT_DOC_TITLE = "Growovo 结果单"


class DeliverError(RuntimeError):
    """结果单落盘/投递的输入、路径或凭据不满足契约。"""


# ---------------------------------------------------------------------------
# 最小 Markdown 渲染
# ---------------------------------------------------------------------------

# 只认三种标题；要求 # 后有空白，避免把 #话题 当标题。
_HEADING_RE = re.compile(r"^(#{1,3})\s+(.*)$")
# 无序列表项：允许缩进，但统一渲染成单层列表。
_UL_RE = re.compile(r"^\s*-\s+(.*)$")
# 围栏代码块：``` 或 ```python；语言串只用于 class，不作为资源加载。
_FENCE_RE = re.compile(r"^```([A-Za-z0-9_+.\-]*)\s*$")
# 粗体：非贪婪，单行内匹配。
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
# 表格分隔行单元格：| --- | :---: | ---: |
_SEP_CELL_RE = re.compile(r"^:?-{3,}:?$")
_HEADING_TRAIL_RE = re.compile(r"\s+#+\s*$")
_HR_MARKS = ("---", "***", "___")

# 自包含样式：没有 @import、没有 url()、没有任何外部地址。
_STYLE = """
:root { --fg: #1f2328; --muted: #656d76; --line: #d0d7de;
        --bg: #ffffff; --soft: #f6f8fa; }
* { box-sizing: border-box; }
body { margin: 0; padding: 40px 20px 56px; background: var(--bg); color: var(--fg);
       font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC",
                    "Hiragino Sans GB", "Microsoft YaHei", sans-serif;
       font-size: 16px; line-height: 1.75; }
main, .page-header { max-width: 760px; margin: 0 auto; }
h1, h2, h3 { line-height: 1.35; margin: 1.6em 0 0.6em; color: var(--fg); }
h1 { font-size: 1.7em; border-bottom: 1px solid var(--line); padding-bottom: 0.35em; }
h2 { font-size: 1.35em; }
h3 { font-size: 1.12em; }
p { margin: 0.7em 0; }
ul { margin: 0.7em 0; padding-left: 1.4em; }
li { margin: 0.3em 0; }
strong { font-weight: 600; }
table { border-collapse: collapse; width: 100%; margin: 1em 0; font-size: 0.95em; }
th, td { border: 1px solid var(--line); padding: 0.45em 0.7em;
         text-align: left; vertical-align: top; }
th { background: var(--soft); font-weight: 600; }
pre { margin: 1em 0; padding: 14px 16px; overflow-x: auto; line-height: 1.55;
      background: var(--soft); border: 1px solid var(--line); border-radius: 6px; }
code { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas,
                    "Liberation Mono", monospace; font-size: 0.92em; }
p code, li code, td code, th code { padding: 0.12em 0.35em; border-radius: 4px;
                                     background: var(--soft); }
hr { border: 0; border-top: 1px solid var(--line); margin: 1.6em 0; }
@media (max-width: 560px) {
  body { padding: 24px 14px 40px; }
  table { font-size: 0.9em; }
}
"""


def _escape(text: Any) -> str:
    """HTML 转义：& < > " ' 全部变成实体，任何用户文本都不会变成标签。"""
    return _html.escape(str(text if text is not None else ""), quote=True)


def _normalize(markdown: Any) -> str:
    """统一换行与 BOM，渲染只看 \n 一种行尾。"""
    text = str(markdown if markdown is not None else "")
    if text.startswith("\ufeff"):
        text = text[1:]
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _inline(text: str) -> str:
    """行内渲染：先整段转义，再把 **x** 换成 <strong>；顺序不能反。"""
    escaped = _escape(text)
    return _BOLD_RE.sub(r"<strong>\1</strong>", escaped)


def _split_row(line: str) -> List[str]:
    """把 | a | b | 拆成 ["a", "b"]；首尾竖线可有可无。"""
    text = line.strip()
    if text.startswith("|"):
        text = text[1:]
    if text.endswith("|"):
        text = text[:-1]
    return [cell.strip() for cell in text.split("|")]


def _looks_like_table_row(line: str) -> bool:
    """| 开头且 | 结尾、至少两格，才当作表格行。"""
    text = line.strip()
    if not (text.startswith("|") and text.endswith("|")):
        return False
    return len(_split_row(text)) >= 2


def _is_separator_row(line: str) -> bool:
    """判断 | --- | :---: | ---: | 这条对齐分隔行。"""
    cells = _split_row(line)
    return bool(cells) and all(_SEP_CELL_RE.match(cell) for cell in cells)


def _alignment(cell: str) -> str:
    """从分隔单元格解析对齐方式；解析不出就不加 style。"""
    if cell.startswith(":") and cell.endswith(":"):
        return "center"
    if cell.endswith(":"):
        return "right"
    if cell.startswith(":"):
        return "left"
    return ""


def _align_attr(alignment: str) -> str:
    return ' style="text-align:%s"' % alignment if alignment else ""


def _render_code_block(code: str, language: str) -> str:
    """围栏代码块：内容只转义、不做行内渲染，原样保留换行与缩进。"""
    css_class = ""
    if language:
        css_class = ' class="language-%s"' % _escape(language)
    return "<pre><code%s>%s</code></pre>" % (css_class, _escape(code))


def _render_table(rows: List[str]) -> str:
    """渲染连续 |...| 行：第一行做表头，第二行若是分隔行则读取对齐并跳过。"""
    header = _split_row(rows[0])
    body_rows = rows[1:]
    alignments = [""] * len(header)
    if body_rows and _is_separator_row(body_rows[0]):
        separator = _split_row(body_rows[0])
        alignments = [
            _alignment(separator[i]) if i < len(separator) else ""
            for i in range(len(header))
        ]
        body_rows = body_rows[1:]
    head_cells = []
    for index, cell in enumerate(header):
        attr = _align_attr(alignments[index] if index < len(alignments) else "")
        head_cells.append("<th%s>%s</th>" % (attr, _inline(cell)))
    parts = ["<table>", "<thead>", "<tr>%s</tr>" % "".join(head_cells),
             "</thead>"]
    if body_rows:
        parts.append("<tbody>")
        for row in body_rows:
            cells = _split_row(row)
            body_cells = []
            for index, cell in enumerate(cells):
                attr = _align_attr(
                    alignments[index] if index < len(alignments) else "")
                body_cells.append("<td%s>%s</td>" % (attr, _inline(cell)))
            parts.append("<tr>%s</tr>" % "".join(body_cells))
        parts.append("</tbody>")
    parts.append("</table>")
    return "\n".join(parts)


def _starts_block(lines: List[str], index: int) -> bool:
    """段落续行判断：遇到空行或任一独立块开头就断开。"""
    line = lines[index]
    stripped = line.strip()
    if not stripped:
        return True
    if _FENCE_RE.match(stripped) or _HEADING_RE.match(stripped):
        return True
    if _UL_RE.match(line) or stripped in _HR_MARKS:
        return True
    if _looks_like_table_row(line) and index + 1 < len(lines):
        return _looks_like_table_row(lines[index + 1])
    return False


def _first_heading_text(markdown: str) -> str:
    """取第一个标题的文字，作为 <title> 的兜底。"""
    for line in _normalize(markdown).split("\n"):
        match = _HEADING_RE.match(line.strip())
        if match:
            return _BOLD_RE.sub(r"\1", match.group(2)).strip()
    return ""


def _render_blocks(markdown: str) -> str:
    """按行切块渲染：代码块、标题、表格、列表、分隔线、段落。"""
    lines = _normalize(markdown).split("\n")
    blocks: List[str] = []
    index = 0
    total = len(lines)
    while index < total:
        line = lines[index]
        stripped = line.strip()
        if not stripped:
            index += 1
            continue

        # 1) 围栏代码块：从 ``` 到下一个 ```，缺收尾也照常输出。
        fence = _FENCE_RE.match(stripped)
        if fence:
            language = fence.group(1)
            index += 1
            code_lines: List[str] = []
            while index < total and not _FENCE_RE.match(lines[index].strip()):
                code_lines.append(lines[index])
                index += 1
            if index < total:  # 吃掉收尾的 ```
                index += 1
            blocks.append(_render_code_block("\n".join(code_lines), language))
            continue

        # 2) 标题 # / ## / ###。
        heading = _HEADING_RE.match(stripped)
        if heading:
            level = len(heading.group(1))
            heading_text = _HEADING_TRAIL_RE.sub("", heading.group(2)).strip()
            blocks.append("<h%d>%s</h%d>" % (level, _inline(heading_text), level))
            index += 1
            continue

        # 3) 表格：至少两行 |...| 才算，单行退回段落。
        if _looks_like_table_row(line):
            rows: List[str] = []
            probe = index
            while probe < total and _looks_like_table_row(lines[probe]):
                rows.append(lines[probe].strip())
                probe += 1
            if len(rows) >= 2:
                blocks.append(_render_table(rows))
                index = probe
                continue

        # 4) 无序列表：连续的 - 项合并成一个 <ul>。
        if _UL_RE.match(line):
            items: List[str] = []
            while index < total:
                item = _UL_RE.match(lines[index])
                if not item:
                    break
                items.append(item.group(1))
                index += 1
            blocks.append("<ul>\n%s\n</ul>" % "\n".join(
                "<li>%s</li>" % _inline(item) for item in items))
            continue

        # 5) 分隔线。
        if stripped in _HR_MARKS:
            blocks.append("<hr>")
            index += 1
            continue

        # 6) 普通段落：读到空行或下一个块开头为止。
        paragraph = [stripped]
        index += 1
        while index < total and not _starts_block(lines, index):
            paragraph.append(lines[index].strip())
            index += 1
        blocks.append("<p>%s</p>" % "\n".join(
            _inline(sentence) for sentence in paragraph))
    return "\n".join(blocks)


def render_html(markdown: str, title: str = "") -> str:
    """把最小 Markdown 渲染成自包含 HTML 文档；无任何外部资源。

    只认标题（#/##/###）、无序列表（-）、表格（|）、粗体（**x**）、段落与
    围栏代码块（```）；其余文本全部转义（``<script>`` 只会显示成文本）。
    ``title`` 写进 ``<title>``；若正文没有 H1 且传了 title，再在页首补一个
    可见标题。返回完整 ``<!DOCTYPE html>`` 文档，CSS 全部内联。
    """
    markdown_text = _normalize(markdown)
    body = _render_blocks(markdown_text)
    heading_text = _first_heading_text(markdown_text)
    given_title = str(title or "").strip()
    doc_title = given_title or heading_text or DEFAULT_DOC_TITLE
    header = ""
    if given_title and "<h1>" not in body:
        header = ('<header class="page-header">\n<h1 class="page-title">%s</h1>\n'
                  '</header>\n') % _escape(given_title)
    return (
        "<!DOCTYPE html>\n"
        '<html lang="zh-CN">\n'
        "<head>\n"
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        "<title>%s</title>\n"
        "<style>%s</style>\n"
        "</head>\n"
        "<body>\n"
        "%s<main>\n%s\n</main>\n"
        "</body>\n"
        "</html>\n"
    ) % (_escape(doc_title), _STYLE, header, body)


# ---------------------------------------------------------------------------
# 结果单落盘
# ---------------------------------------------------------------------------

def _safe_component(value: Any, name: str) -> str:
    """校验路径组件：非空、单层、不含分隔符，杜绝 ../ 走出 data 目录。"""
    text = str(value if value is not None else "").strip()
    if not text:
        raise DeliverError("%s 不能为空" % name)
    if text in (".", "..") or "/" in text or "\\" in text or "\x00" in text:
        raise DeliverError("%s 只能是单层文件名，收到：%r" % (name, text))
    return text


def write_card(data_dir, run_id: str, kind: str, label: str, markdown: str,
               title: str = "") -> Dict[str, str]:
    """把一份结果单落盘为 .md 与 .html，返回两个文件的路径字符串。

    路径固定为 ``<data_dir>/grow/<run_id>/cards/<kind>-<label>.md`` 与同名
    ``.html``；目录不存在时自动创建。``title`` 只影响 HTML 的标题。
    """
    run = _safe_component(run_id, "run_id")
    card_kind = _safe_component(kind, "kind")
    card_label = _safe_component(label, "label")
    markdown_text = str(markdown if markdown is not None else "")
    cards_dir = Path(data_dir) / "grow" / run / "cards"
    cards_dir.mkdir(parents=True, exist_ok=True)
    stem = "%s-%s" % (card_kind, card_label)
    markdown_path = cards_dir / (stem + ".md")
    html_path = cards_dir / (stem + ".html")
    markdown_path.write_text(markdown_text, encoding="utf-8")
    html_path.write_text(render_html(markdown_text, title=title), encoding="utf-8")
    return {"markdown": str(markdown_path), "html": str(html_path)}


# ---------------------------------------------------------------------------
# 邮件投递
# ---------------------------------------------------------------------------

def _build_message(markdown: str, *, to: str, subject: str,
                   from_addr: str) -> EmailMessage:
    """构造 multipart/alternative 邮件：纯文本放 Markdown 原文，HTML 放渲染结果。"""
    message = EmailMessage(policy=_email_policy.default)
    message["From"] = from_addr
    message["To"] = to
    message["Subject"] = subject
    message["Date"] = formatdate(localtime=True)
    message.set_content(str(markdown if markdown is not None else ""),
                        subtype="plain", charset="utf-8")
    message.add_alternative(render_html(markdown, title=subject),
                            subtype="html", charset="utf-8")
    return message


def _eml_filename(subject: str) -> str:
    """outbox 文件名：UTC 时间戳 + 主题 slug；文件名只留 ASCII。"""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", subject).strip("-._")[:48]
    return "%s-%s.eml" % (stamp, slug or "card")


def _smtp_credentials() -> Tuple[str, str]:
    """从环境变量读 SMTP 凭据；缺任一就抛 DeliverError，绝不带空凭据联网。"""
    user = os.environ.get(EMAIL_USER_ENV, "").strip()
    password = os.environ.get(EMAIL_PASSWORD_ENV, "").strip()
    if not user or not password:
        raise DeliverError(
            "缺少 SMTP 配置：请设置环境变量 %s 与 %s（Gmail App Password）"
            % (EMAIL_USER_ENV, EMAIL_PASSWORD_ENV))
    return user, password


def deliver_email(markdown: str, to: str, subject: str, dry_run: bool = True,
                  outbox_dir=None) -> Dict[str, Any]:
    """投递一份 Markdown 结果单；默认 dry-run，只写 .eml、不联网。

    ``dry_run=True``（默认）：把 UTF-8 的 .eml 写进 ``outbox_dir``，返回
    ``{"sent": False, "dry_run": True, "path": ...}``。本函数无法从参数推断
    运行 id，因此 dry-run 必须显式传 ``outbox_dir``（``deliver_card`` 会传
    ``data/grow/<run_id>/outbox``），否则抛 ``DeliverError``。

    ``dry_run=False``：读取 MKT_EMAIL_USER / MKT_EMAIL_APP_PASSWORD，连
    smtp.gmail.com:465 发送；缺配置抛 ``DeliverError`` 且不建连接；协议/
    网络失败不吞，返回 ``{"sent": False, "error": ...}``。
    """
    recipient = str(to or "").strip()
    subject_text = str(subject or "").strip()
    if not recipient:
        raise DeliverError("邮件收件人 to 不能为空")
    if not subject_text:
        raise DeliverError("邮件主题 subject 不能为空")
    markdown_text = str(markdown if markdown is not None else "")

    if dry_run:
        if outbox_dir is None:
            raise DeliverError(
                "dry_run 需要传入 outbox_dir（例如 data/grow/<run_id>/outbox）；"
                "deliver_email 无法从参数推断运行 id")
        outbox = Path(outbox_dir)
        outbox.mkdir(parents=True, exist_ok=True)
        from_addr = os.environ.get(EMAIL_USER_ENV, "").strip() or DEFAULT_FROM
        message = _build_message(markdown_text, to=recipient,
                                 subject=subject_text, from_addr=from_addr)
        eml_path = outbox / _eml_filename(subject_text)
        eml_path.write_text(message.as_string(), encoding="utf-8")
        return {"sent": False, "dry_run": True, "path": str(eml_path),
                "to": recipient, "subject": subject_text, "error": ""}

    user, password = _smtp_credentials()
    message = _build_message(markdown_text, to=recipient,
                             subject=subject_text, from_addr=user)
    try:
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=30) as smtp:
            smtp.login(user, password)
            smtp.send_message(message)
    except Exception as exc:  # 网络/协议失败如实回报，不伪装成已发送
        return {"sent": False, "dry_run": False, "path": None,
                "to": recipient, "subject": subject_text,
                "error": "%s: %s" % (type(exc).__name__, exc)}
    return {"sent": True, "dry_run": False, "path": None,
            "to": recipient, "subject": subject_text, "error": ""}


def deliver_card(data_dir, run_id: str, kind: str, label: str, markdown: str,
                 title: str = "", to: str = "", subject: str = "",
                 live: bool = False, outbox_dir=None) -> Dict[str, Any]:
    """组合交付：先把结果单落盘，``to`` 非空时再投递。

    ``live`` 为 False（默认）时走 dry-run，.eml 默认写到
    ``<data_dir>/grow/<run_id>/outbox``；``live=True`` 才真实联网发送（需要
    SMTP 凭据，缺配置抛 ``DeliverError``，卡片已经落盘）。返回合并结果：
    卡片两个路径 + ``delivery`` 明细，并把 ``sent/dry_run/path/to/subject/error``
    平铺到顶层；``to`` 为空时不投递，``path`` 为 None。
    """
    card = write_card(data_dir, run_id, kind, label, markdown, title=title)
    result: Dict[str, Any] = {
        "markdown": card["markdown"],
        "html": card["html"],
        "card": card,
        "delivery": None,
        "sent": False,
        "dry_run": True,
        "path": None,
        "to": "",
        "subject": "",
        "error": "",
    }
    recipient = str(to or "").strip()
    if not recipient:
        return result
    subject_text = (str(subject or "").strip()
                    or str(title or "").strip()
                    or "%s-%s" % (_safe_component(kind, "kind"),
                                  _safe_component(label, "label")))
    target_outbox = outbox_dir
    if target_outbox is None:
        target_outbox = (Path(data_dir) / "grow"
                         / _safe_component(run_id, "run_id") / "outbox")
    delivery = deliver_email(markdown, to=recipient, subject=subject_text,
                             dry_run=not live, outbox_dir=target_outbox)
    result["delivery"] = delivery
    for key in ("sent", "dry_run", "path", "to", "subject", "error"):
        result[key] = delivery[key]
    return result
