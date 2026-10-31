#!/usr/bin/env python3
"""发送门禁（架构文档 §5）——只放行/拒绝，绝不改写内容。

来源：newoutreach 五门禁（449 空正文事故复盘）+ xdm.py 消息纪律。
"""
import json
import re
from pathlib import Path

# xdm 教训：触达文本禁 em/en dash（AI 味标记，实测拉低回复率）
DEFAULT_FORBIDDEN = ["\u2014", "\u2013"]

# 占位符残留 = 千人千面没做完，坚决不放行（449 事故同族风险）
PLACEHOLDER_RE = re.compile(r"\{\{[^}{]*\}\}|\[NAME\]|\[TODO[^\]]*\]|\[COMPANY\]|<NAME>")

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class GateError(Exception):
    """拒绝原因完整写在 message 里；这是逐条正常结果，不是系统错误。"""


def check_content(text, *, max_len=None, min_len=1, forbidden=DEFAULT_FORBIDDEN, label=""):
    if text is None or not text.strip():
        raise GateError(f"{label}内容为空")
    if len(text) < min_len:
        raise GateError(f"{label}内容短于下限 {min_len}")
    if max_len is not None and len(text) > max_len:
        raise GateError(f"{label}内容 {len(text)} 字符超上限 {max_len}")
    for ch in forbidden:
        if ch in text:
            raise GateError(f"{label}内容含禁用字符（em/en dash）")
    m = PLACEHOLDER_RE.search(text)
    if m:
        raise GateError(f"{label}内容含占位符残留: {m.group(0)!r}")


def check_target(channel, target):
    if not target or not str(target).strip():
        raise GateError("目标为空")
    t = str(target).strip()
    if channel == "email":
        if not EMAIL_RE.match(t):
            raise GateError(f"email 格式不合法: {t}")
    elif channel == "x":
        if not (t.startswith("@") or t.startswith("tweet:") or t.startswith("http")):
            raise GateError(f"X 目标应为 @handle、tweet:url 或推文 URL: {t}")
    elif channel == "reddit":
        if not (t.startswith("u/") or t.startswith("t3_") or t.startswith("http")):
            raise GateError(f"Reddit 目标应为 u/、t3_ 或 URL: {t}")
    elif channel == "linkedin":
        if not t.startswith("https://www.linkedin.com"):
            raise GateError(f"LinkedIn 目标应为 profile URL: {t}")
    elif channel == "github":
        if not re.match(r"^issue:[\w.-]+/[\w.-]+#\d+$", t):
            raise GateError(f"GitHub 目标应为 issue:owner/repo#N: {t}")
    elif channel == "hn":
        if not (t.startswith("item:") or t.startswith("hn:submit:")
                or t.startswith("https://news.ycombinator.com")):
            raise GateError(f"HN 目标应为 item:N、hn:submit:… 或 news.ycombinator.com URL: {t}")
    return t


def load_suppress(path):
    """抑制名单：每行一个目标（email/handle/url），# 注释与空行忽略。"""
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return set()
    return {l.strip() for l in lines if l.strip() and not l.strip().startswith("#")}


def load_contact_suppressions(data_dir):
    """从 contacts.jsonl 读取 DNC / bounced 身份，作为发送门禁的统一抑制源。

    DNC 是联系人库的业务状态；发送门禁不能要求人工再抄一遍 suppress.txt。
    这里把该联系人的全部平台身份都视为抑制目标；文件缺失/坏行按空处理，
    真正的写操作仍会经过账本、暂停和审批门禁。
    """
    path = Path(data_dir) / "contacts.jsonl"
    if not path.is_file():
        return set()
    out = set()
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        state = rec.get("state") or {}
        if not isinstance(state, dict):
            continue
        if not (state.get("dnc") or state.get("bounced")):
            continue
        platforms = rec.get("platforms") or {}
        if isinstance(platforms, dict):
            out.update(str(v).strip() for v in platforms.values() if str(v or "").strip())
        for key in ("email", "handle", "target"):
            if rec.get(key):
                out.add(str(rec[key]).strip())
    return out


def load_suppressions(data_dir):
    """发送门禁的唯一抑制集合：suppress.txt ∪ contacts 中 DNC/bounced 身份。"""
    data = Path(data_dir)
    return load_suppress(data / "suppress.txt") | load_contact_suppressions(data)


class Gate:
    def __init__(self, ledger=None, suppress=None):
        self.ledger = ledger
        self.suppress = set(suppress or ())

    def check(self, *, channel, action, target, campaign=None, content=None,
              max_len=None, dedupe=True):
        """依次过五关；任何一关不过抛 GateError（调用方记 rejected，继续下一条）。"""
        target = check_target(channel, target)
        if target in self.suppress:
            raise GateError(f"目标在抑制名单: {target}")
        if self.ledger is not None:
            if target in self.ledger.no_retry(channel):
                raise GateError(f"目标在渠道不重试名单（indeterminate/no-dm/bounced/suppressed）")
            if dedupe and campaign:
                if self.ledger.already(channel=channel, target=target, campaign=campaign):
                    raise GateError(f"同 campaign 已成功发过（防重发）")
        if content is not None:
            check_content(content, max_len=max_len, label=f"[{target}] ")
        return target
