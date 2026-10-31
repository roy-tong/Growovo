#!/usr/bin/env python3
"""追踪链接 —— 让"信号"能自己走回 campaign（记账自动化的入口）。

问题：outcome 事件靠人手填 channel/source_ref，填错或忘填，漏斗就断在"访问"
这一层。本模块给每个渠道动作生成带 utm_* 的链接，并把 campaign→链接 的映射
落盘（data/links.jsonl），后续导入访问/注册事件时可直接按 utm_campaign 归位。

纪律：
- 只加 utm_*，不改路径与已有非 utm 参数（保留原链接语义）；
- 同一个 (campaign, channel, url, content) 幂等，不重复落盘；
- 不生成短链、不经过第三方跳转（本地优先，数据不出机器）。
"""
import hashlib
import json
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

MEDIUM = {
    "email": "email",
    "x": "social",
    "reddit": "social",
    "hn": "social",
    "linkedin": "social",
    "github": "referral",
}


def build_url(url, *, campaign, channel, content="", medium="", term=""):
    parts = urlsplit(url)
    if not parts.scheme or not parts.netloc:
        raise ValueError(f"链接必须是绝对 URL: {url!r}")
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
             if not k.startswith("utm_")]
    query += [
        ("utm_source", channel),
        ("utm_medium", medium or MEDIUM.get(channel, "owned")),
        ("utm_campaign", campaign),
    ]
    if content:
        query.append(("utm_content", content))
    if term:
        query.append(("utm_term", term))
    return urlunsplit((parts.scheme, parts.netloc, parts.path,
                       urlencode(query), parts.fragment))


def links_path(data_dir):
    return Path(data_dir) / "links.jsonl"


def list_links(data_dir, campaign=None):
    p = links_path(data_dir)
    if not p.is_file():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if campaign is None or rec.get("campaign") == campaign:
            out.append(rec)
    return out


def make_link(data_dir, *, url, campaign, channel, content="", medium="", term=""):
    """生成并落盘。幂等：同键返回已有记录（duplicate=True）。"""
    if not str(campaign or "").strip():
        raise ValueError("campaign 不能为空（链接必须能归属）")
    tagged = build_url(url, campaign=campaign, channel=channel,
                       content=content, medium=medium, term=term)
    key = "|".join((campaign, channel, url, content))
    link_id = hashlib.sha1(key.encode("utf-8")).hexdigest()[:10]
    for rec in list_links(data_dir):
        if rec.get("id") == link_id:
            return dict(rec, duplicate=True)
    rec = {
        "id": link_id,
        "ts": datetime.now().isoformat(timespec="seconds"),
        "campaign": campaign,
        "channel": channel,
        "content": content,
        "url": url,
        "tagged_url": tagged,
    }
    p = links_path(data_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return dict(rec, duplicate=False)
