#!/usr/bin/env python3
"""Content operations: channel rules, briefs, draft audits and a 14-day plan.

This module turns the content rules learned in the 2026-11 sessions into
deterministic, testable capabilities. It does not publish, schedule or send.
A passing audit means "the draft follows the local writing contract", not
"the platform will distribute it" or "the content will grow followers".

Design principles:
- one channel has one rules object; no channel-specific branches in callers;
- audit errors are structural violations; warnings are editorial risks;
- product mentions need a setup, and own products need an author relationship;
- private contact details and meta narration are errors on every channel;
- missing evidence is a warning, never silently treated as verified.
"""
from __future__ import annotations

import json
import re
from datetime import date, timedelta
from difflib import SequenceMatcher
from core.editorial_review import CRITERIA
from . import platform_strategy
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

SCHEMA_VERSION = "1.0"

POSITIONING: Dict[str, Any] = {
    "reader": "硬件产品经理、电商运营、产品/内容从业者；不要求会写代码",
    "statement": "AI 工具使用说明，不是 AI 新闻，也不是产品广告",
    "questions": [
        "它替你做的是哪一步工作",
        "放进真实任务，怎样开始验证；首次自测安排不等于完成时长承诺",
        "哪一步它做不到",
    ],
    "structure": [
        "场景或冲突",
        "旧办法的代价",
        "它改哪一步（专业词括号解释）",
        "按实际任务安排验证动作，不强凑三步",
        "边界",
        "今天能做的一步",
        "关注/评论等渠道动作",
    ],
    "language": "能用大白话就不用术语；第一次出现的术语括号解释；不生造比喻词",
}

CHANNELS: Dict[str, Dict[str, Any]] = {
    "xiaohongshu": {
        "label": "小红书",
        "tone": "短段落、口语、讲具体过程，不写产品广告",
        "structure": [
            "一个具体场景或冲突",
            "旧办法的代价",
            "它改哪一步（术语括号解释）",
            "按实际任务安排验证动作，不强凑三步",
            "边界",
            "你今天就可以先试一下",
            "关注并私信（A 送产品 / B 送方法 / C 约协助）",
        ],
        "cta": {"A": "关注我一波，私信我，发免费产品", "B": "关注并私信我，发完整方法",
                "C": "私信说清环境和卡点，约首次协助"},
        "metrics": ["7/14 天净增粉丝", "新增关注", "取关", "归因关注", "首个真实任务"],
        "images": "3—6 张；封面给实物或前后对比，不只放文字",
        "reuse": "一篇一个读者任务；同题换场景、证据或反例，不换标题重发",
    },
    "wechat": {
        "label": "公众号",
        "tone": "叙事推进：一个人、一件事、一个判断怎么被改掉",
        "structure": [
            "一个具体人物/事件开场",
            "冲突与代价",
            "技术或方法机制",
            "证据与反例",
            "适用边界",
            "读者可执行的一步",
            "评论区或关注承接",
        ],
        "cta": {"A": "关注后私信，发免费产品", "B": "关注后私信，发完整方法",
                "C": "私信说清任务，约首次协助"},
        "metrics": ["阅读", "分享", "主动提问", "方法复用", "关注"],
        "images": "一图讲清一个机制或对比；不堆截图",
        "reuse": "从小红书底稿展开，补叙事和取舍；不直接搬运短文",
    },
    "blog": {
        "label": "博客",
        "cadence": {"per_week": 1},
        "length": {"min": 800, "max": 4200},
        "title_max": 64,
        "title_note": "标题写清可复现的对象和结果，便于搜索",
        "tone": "面向专业读者的完整文章：先解释问题和判断，再把复现细节放附录",
        "structure": [
            "问题与适用读者",
            "读者问题与核心结论",
            "证据、机制、取舍和应用",
            "复现附录：环境、版本、命令与输出",
            "失败与边界",
            "来源与更新记录",
        ],
        "cta": {"A": "文末给公开任务包/仓库入口", "B": "文末给模板或复现清单",
                "C": "文末留任务描述入口"},
        "metrics": ["搜索进入", "引用/转载", "复现反馈", "模板使用", "更新记录"],
        "images": "命令输出、对比表、结构图；不用营销海报",
        "reuse": "展开专业读者的判断、证据与取舍；来源和复现置于必要附录",
    },
    "zhihu": {
        "label": "知乎",
        "tone": "先给判断，再给过程和证据；第一人称实测，不用「首先其次」",
        "structure": [
            "结论先行（一句）",
            "我的具体场景",
            "做法与数据",
            "哪里不成立（边界）",
            "读者怎么复用",
        ],
        "cta": {"A": "主页有完整清单", "B": "评论区留下你的场景",
                "C": "私信说清任务，约首次协助"},
        "metrics": ["阅读", "收藏", "咨询", "关注"],
        "images": "至少 2 张：结构图或结果对比；不堆截图",
        "reuse": "同题换问题场景重写；不搬运旧答",
    },
    "douyin": {
        "label": "抖音",
        "tone": "前 3 秒给结果或冲突；口语、短句、大字幕",
        "structure": [
            "3 秒钩子：结果或反差",
            "演示（一步一个画面）",
            "结果与代价",
            "一句行动",
        ],
        "cta": {"A": "关注看系列", "B": "评论区要清单", "C": "主页有完整流程"},
        "metrics": ["完播", "点赞", "评论", "主页点击"],
        "images": "视频竖屏 9:16；不用静态图代替演示",
        "reuse": "同题换开头钩子和演示顺序；不重复发同一条",
    },
}

# 平台硬参数只有一份：节奏、字数、标题、变现与门禁来自 core/platform_strategy.py。
# 这里只保留写作契约（语气、结构、CTA、指标），不重复写数字。
for _pid in ("xiaohongshu", "wechat", "zhihu", "douyin"):
    _spec = platform_strategy.get(_pid)
    _lim = platform_strategy.limits(_pid)
    _channel = CHANNELS[_pid]
    _channel["length"] = {"min": int(_lim.get("body_min") or 0),
                          "max": int(_lim.get("body_max") or 0)}
    _channel["title_max"] = int(_lim.get("title_max") or 0)
    _channel["title_note"] = str(_lim.get("title_note") or "")
    if _lim.get("title_ideal"):
        _channel["title_ideal"] = str(_lim["title_ideal"])
    _channel["cadence"] = platform_strategy.cadence(_pid)
    _channel["gates"] = platform_strategy.gates(_pid, scope="content")
    _channel["execution_gates"] = platform_strategy.gates(_pid, scope="execution")
    _channel["strategy_checklist"] = platform_strategy.checklist(_pid)
    _channel["overrides"] = platform_strategy.overrides(_pid)
    for _key in ("images_min", "images_max", "hashtag_max"):
        if _lim.get(_key) is not None:
            _channel[_key] = int(_lim[_key])
    if _pid == "wechat":
        _ad = _spec.get("ad_slots") or {}
        _channel["monetization"] = {
            "ad_slot_floor_chars": int(_ad.get("floor_chars") or 600),
            "ad_slot_full_chars": int(_ad.get("full_chars") or 1000),
            "note": "广告前后各需 300 字：600 字=文中 1+文末 1，千字以上=文中 2+文末 1；"
                    "图片挤占广告位；自己点广告或引导读者点流量主=限流到永久封禁",
        }

PRODUCTS: Dict[str, Dict[str, Any]] = {
    "Askovo": {"own": True, "what": "需求与用户研究工具，把评论、访谈和资料整理成支持/反对/缺口"},
    "Growovo": {"own": True, "what": "内容与运营流程工具，把素材、发布、回填排成流程"},
    "Verovo": {"own": True, "what": "AI 任务费用与交付核对工具"},
    "Needle": {"own": False, "what": "端侧工具调用开源项目"},
    "HyperFrames": {"own": False, "what": "用网页代码描述并渲染视频的开源项目"},
    "QMD": {"own": False, "what": "本地 Markdown 资料检索开源项目"},
    "PhotoCraft": {"own": False, "what": "提供图层、PSD 与 Agent 接口的开源修图项目"},
    "fugleramme": {"own": False, "what": "听鸟叫、识别鸟种并显示插画的硬件项目"},
    "marketingskills": {"own": False, "what": "把营销方法整理成技能的开源项目"},
}

OWN_CUES = ("我自己在做", "我最近做了", "我做的", "我开发")
OPEN_CUES = ("开源项目", "开源", "公开代码")
EVIDENCE_CUES = ("官方文档", "README", "版本", "实测", "合成", "未验证", "没测",
                 "只读了", "示例", "复现", "命令", "输出")
ACTION_CUES = ("今天就能做", "你今天就可以", "先试一下", "可以先", "先做", "下一步")
META_PATTERNS = (
    re.compile(r"这篇只回答一件事"),
    re.compile(r"本文只回答"),
    re.compile(r"这篇文章只回答"),
)
LABEL_PATTERNS = (
    re.compile(r"文末CTA[：:]"),
    re.compile(r"A领取CTA"),
    re.compile(r"CTA草稿[：:]"),
)
CONTACT_PATTERN = re.compile(r"(微信|加V|加v|vx|VX|二维码|公众号[：:])")
ABSOLUTE_HARD = ("全网首发", "行业震惊", "100%", "保证", "稳赚", "零风险", "必赚", "永久有效")
ABSOLUTE_SOFT = ("最好", "第一", "顶级", "绝对")
COINED = ("赋能", "闭环", "抓手", "颗粒度", "打法", "破圈", "势能", "组合拳", "发射")

# 平台门禁的文本检测器：key 必须与 platform_strategy.DETECTORS 一一对应。
INDUCE_FOLLOW = (
    re.compile(r"关注[^。\n]{0,12}(回复|发送|输入|私信|领取|获取|免费|资料|模板|礼包)"),
    re.compile(r"(点赞|收藏|四连)[^。\n]{0,10}(领取|获取|送|拿图|自取)"),
)
INDUCE_SHARE = (
    re.compile(r"集赞"),
    re.compile(r"分享[^。\n]{0,8}(解锁|抽奖|领)"),
    re.compile(r"转发[^。\n]{0,8}(抽奖|领)"),
    re.compile(r"邀请\s*\d+\s*人"),
)
CLICKBAIT = ("震惊", "速看", "必看", "惊呆", "绝了", "不看后悔", "爆款密码")
ANXIETY = ("再不", "就晚了", "会毁掉", "废掉", "落后于", "同龄人已经", "不学就")
AD_BEGGING = re.compile(r"(点|戳)[^。\n]{0,4}(一下|击)?[^。\n]{0,4}广告|广告[^。\n]{0,4}支持")
EXTERNAL_LINK = re.compile(r"https?://[^\s，。；）)]+")
HASHTAG = re.compile(r"#[^\s#，。！？；：]{1,30}")


def _detect_contact(body: str, title: str, rules: Dict[str, Any]) -> List[str]:
    return [m.group(0) for m in CONTACT_PATTERN.finditer(body)]


def _detect_link(body: str, title: str, rules: Dict[str, Any]) -> List[str]:
    return EXTERNAL_LINK.findall(body)


def _detect_induce_follow(body: str, title: str, rules: Dict[str, Any]) -> List[str]:
    out: List[str] = []
    for pattern in INDUCE_FOLLOW:
        out += [m.group(0) for m in pattern.finditer(body)]
    return out


def _detect_induce_share(body: str, title: str, rules: Dict[str, Any]) -> List[str]:
    out: List[str] = []
    for pattern in INDUCE_SHARE:
        out += [m.group(0) for m in pattern.finditer(body)]
    return out


def _detect_absolute(body: str, title: str, rules: Dict[str, Any]) -> List[str]:
    hit = _claim_hit(body, ABSOLUTE_HARD)
    return [hit] if hit else []


def _detect_clickbait(body: str, title: str, rules: Dict[str, Any]) -> List[str]:
    return [word for word in CLICKBAIT if word in (title or "") or word in body]


def _detect_anxiety(body: str, title: str, rules: Dict[str, Any]) -> List[str]:
    return [word for word in ANXIETY if word in body]


def _detect_ad_begging(body: str, title: str, rules: Dict[str, Any]) -> List[str]:
    return [m.group(0) for m in AD_BEGGING.finditer(body)]


def _detect_images(body: str, title: str, rules: Dict[str, Any]) -> List[str]:
    count = len(re.findall(r"!\[", body))
    low = int((rules.get("images_min") or 0))
    high = int((rules.get("images_max") or 0))
    if low and count < low:
        return [f"正文 {count} 张图，低于建议的 {low} 张"]
    if high and count > high:
        return [f"正文 {count} 张图，超过建议的 {high} 张"]
    return []


def _detect_hashtags(body: str, title: str, rules: Dict[str, Any]) -> List[str]:
    high = int(rules.get("hashtag_max") or 0)
    count = len(HASHTAG.findall(body))
    if high and count > high:
        return [f"话题标签 {count} 个，超过建议的 {high} 个"]
    return []


DETECTOR_IMPL = {
    "contact_info": _detect_contact,
    "external_link": _detect_link,
    "induce_follow": _detect_induce_follow,
    "induce_share": _detect_induce_share,
    "absolute_claim": _detect_absolute,
    "clickbait": _detect_clickbait,
    "anxiety_sell": _detect_anxiety,
    "ad_begging": _detect_ad_begging,
    "image_count": _detect_images,
    "hashtag_count": _detect_hashtags,
}

# 通用检查已经覆盖的检测器：平台门禁只补充策略说明，不重复报同一处。
_GATE_ALIASES = {"contact_info": ("private_contact",)}
# 关键词类检测器只报第一条，避免同一句话被拆成多条提醒。
_SINGLE_HIT = {"absolute_claim", "clickbait", "anxiety_sell", "ad_begging",
               "image_count", "hashtag_count"}

JARGON = (
    "工具调用", "参数", "渲染", "Token", "PSD", "Agent", "API", "MCP", "CLI",
    "Skill", "检索", "端侧", "合成示例", "验收", "批量", "自动化", "适配", "LLM",
)


def _clean(text: str) -> str:
    return re.sub(r"\n{3,}", "\n\n", (text or "").replace("\r\n", "\n")).strip()


def _body(text: str) -> str:
    """Remove the non-published editorial block and source footer."""
    text = _clean(text)
    text = re.sub(r"(?m)^> 编辑定位（不发布）：.*(?:\n|$)", "", text)
    text = re.split(r"\n---\n\n来源与边界：", text)[0]
    # A single draft's H1 is the title, not body prose; it must not count as
    # the first product mention or inflate the length check.
    text = re.sub(r"\A#\s+[^\n]+\n+", "", text)
    return text.strip()


def _sections(text: str) -> List[Tuple[str, str]]:
    """Split a multi-post document into (title, body) sections."""
    raw = _clean(text)
    matches = list(re.finditer(r"(?m)^## (D2-\d+)｜([^\n]+)\n", raw))
    if not matches:
        return []
    out = []
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(raw)
        body = raw[m.end():end]
        # A non-D2 section (e.g. the Growovo review footer) is not part of the post.
        cut = re.search(r"(?m)^## (?!D2-\d+｜)", body)
        if cut:
            body = body[:cut.start()]
        body = _body(body)
        out.append((m.group(1), m.group(2).strip(), body))
    return out


def _title_from_text(text: str) -> str:
    m = re.search(r"(?m)^#\s+([^\n]+)", text or "")
    return m.group(1).strip() if m else ""


def _chars(text: str) -> int:
    return len(re.sub(r"\s", "", text or ""))


def _first(text: str, name: str) -> int:
    m = re.search(re.escape(name), text)
    return m.start() if m else -1


def _mentions(text: str, name: str) -> int:
    return len(re.findall(re.escape(name), text))


def _cta_type(text: str, channel: str) -> Optional[str]:
    if channel == "xiaohongshu":
        # 2026-11 采集：笔记/评论区引导私信、留联系方式属高频违规。
        # 允许不依赖私信的站内承接：关注 + 评论区/收藏。
        if "私信" not in text and "关注" in text and ("评论区" in text or "收藏" in text):
            return "B-safe"
        if "关注我一波" in text and "私信" in text:
            return "A"
        if "关注并私信" in text:
            return "B"
        if "私信" in text and ("环境" in text or "卡点" in text):
            return "C"
        return None
    if channel == "wechat":
        return "B" if ("关注" in text or "评论区" in text) else None
    if channel == "blog":
        return "B" if ("仓库" in text or "模板" in text or "更新" in text or "评论区" in text) else None
    if channel == "zhihu":
        if "主页" in text and ("清单" in text or "完整" in text):
            return "A"
        if "评论区" in text:
            return "B"
        if "私信" in text and "任务" in text:
            return "C"
        return None
    if channel == "douyin":
        if "关注" in text:
            return "A"
        if "评论区" in text:
            return "B"
        if "主页" in text:
            return "C"
        return None
    return None


def _setup_ok(text: str, name: str, own: bool, at: int) -> bool:
    window = text[max(0, at - 90): at + 50]
    if own:
        return any(c in window for c in OWN_CUES)
    return any(c in window for c in OPEN_CUES)


def _jargon_findings(text: str) -> List[Dict[str, str]]:
    findings = []
    for term in JARGON:
        at = _first(text, term)
        if at < 0:
            continue
        tail = text[at + len(term): at + len(term) + 45]
        if any(x in tail for x in ("（", "(", "就是", "即", "指的是", "意思是")):
            continue
        findings.append({"term": term, "excerpt": text[max(0, at - 20): at + len(term) + 25]})
    return findings


def _claim_hit(text: str, words: Iterable[str]) -> str:
    for word in words:
        for m in re.finditer(re.escape(word), text):
            before = text[max(0, m.start() - 6): m.start()]
            after = text[m.end(): m.end() + 3]
            if any(x in before for x in ("不", "没", "无", "非", "未")):
                continue
            if word == "第一" and after[:1] in ("版", "次", "步", "反", "眼", "天", "时间", "反应", "件", "篇", "个", "条", "位", "名", "种", "屏", "句", "组", "轮", "项", "类", "章", "节"):
                continue
            if word == "绝对" and not any(x in after for x in ("不", "是", "能", "会", "没有")):
                continue
            return word
    return ""


def _add(target: List[Dict[str, str]], code: str, message: str, excerpt: str = "") -> None:
    target.append({"code": code, "message": message, "excerpt": excerpt[:120]})


def _apply_gates(channel: str, rules: Dict[str, Any], body: str, title: str,
                 errors: List[Dict[str, str]], warnings: List[Dict[str, str]],
                 infos: List[Dict[str, str]]) -> None:
    """跑平台策略门禁：只跑有检测器的；已有同类发现时不重复报。"""
    buckets = {"error": errors, "warning": warnings, "info": infos}
    existing = {item["code"] for item in errors + warnings + infos}
    for gate in rules.get("gates") or []:
        detector = gate.get("detector")
        if not detector or detector not in DETECTOR_IMPL:
            continue
        aliases = _GATE_ALIASES.get(detector, ())
        if gate.get("code") in existing or any(alias in existing for alias in aliases):
            continue
        hits = DETECTOR_IMPL[detector](body, title, rules)
        if not hits:
            continue
        message = gate.get("statement", "")
        if gate.get("fix"):
            message += " → " + gate["fix"]
        bucket = buckets.get(gate.get("level"), warnings)
        unique = list(dict.fromkeys(str(x) for x in hits))
        if detector in _SINGLE_HIT:
            unique = unique[:1]
        for hit in unique[:3]:
            _add(bucket, gate["code"], message, hit)
        existing.add(gate["code"])


def audit_text(channel: str, text: str, *, title: str = "", strict: bool = False,
               check_title: bool = True) -> Dict[str, Any]:
    """Audit one draft. Errors are structural; warnings are editorial risks."""
    channel = (channel or "").strip().lower()
    if channel not in CHANNELS:
        raise ValueError(f"未知渠道：{channel}；可选 {', '.join(CHANNELS)}")
    rules = CHANNELS[channel]
    body = _body(text)
    title = title or _title_from_text(text)
    errors: List[Dict[str, str]] = []
    warnings: List[Dict[str, str]] = []
    infos: List[Dict[str, str]] = []

    if not body:
        _add(errors, "empty", "正文为空")
        return {"channel": channel, "title": title, "ok": False, "errors": errors,
                "warnings": warnings, "length": 0, "rules": rules, "strict": strict}

    for pat in META_PATTERNS:
        m = pat.search(body)
        if m:
            _add(errors, "meta_narration", "正文出现“这篇只回答一件事”这类元叙述；目的留在编辑定位里",
                 body[max(0, m.start() - 20): m.end() + 30])
            break

    for pat in LABEL_PATTERNS:
        m = pat.search(body)
        if m:
            _add(errors, "cta_label", "正文残留 CTA 标签；改成自然结尾",
                 body[max(0, m.start() - 20): m.end() + 30])
            break

    if CONTACT_PATTERN.search(body):
        _add(errors if channel in ("xiaohongshu", "zhihu") else warnings, "private_contact",
             "出现微信/加V/二维码等站外联系方式", CONTACT_PATTERN.search(body).group(0))

    if check_title:
        if not title:
            _add(errors, "no_title", "缺少标题")
        else:
            limit = int(rules.get("title_max") or 0)
            if limit and len(title) > limit:
                _add(errors if channel == "xiaohongshu" else warnings, "title_too_long",
                     f"标题 {len(title)} 字，超过 {limit} 字（{rules.get('title_note', '')}）", title)

    length = _chars(body)
    lo, hi = rules["length"]["min"], rules["length"]["max"]
    if hi and (length < lo or length > hi):
        _add(warnings, "length", f"正文 {length} 字，建议 {lo}—{hi} 字")

    if channel == "wechat":
        monet = rules.get("monetization", {})
        ad_floor = int(monet.get("ad_slot_floor_chars", 600))
        ad_full = int(monet.get("ad_slot_full_chars", 1000))
        if length < ad_floor:
            _add(warnings, "ad_slot_floor",
                 f"正文 {length} 字：文中广告要求广告前后各 300 字，{ad_floor} 字以下没有文中广告位；"
                 f"{ad_full} 字以上解锁文中 2+文末 1")
        img_count = len(re.findall(r"!\[", text))
        if img_count > 2:
            _add(warnings, "ad_slot_images",
                 f"图片 {img_count} 张：图片截断文字计数、挤占广告位；建议 ≤1 张且放首段后")
        ideal = str(rules.get("title_ideal") or "")
        m = re.match(r"^(\d+)-(\d+)$", ideal)
        if check_title and title and m:
            if not (int(m.group(1)) <= len(title) <= int(m.group(2))):
                _add(infos, "title_ideal",
                     f"标题 {len(title)} 字，建议 {ideal} 字（真诚、清晰、戳需求）", title)

    _apply_gates(channel, rules, body, title, errors, warnings, infos)

    cta = _cta_type(body, channel)
    if not cta:
        _add(errors if channel == "xiaohongshu" else warnings, "no_cta",
             "没有识别到渠道 CTA；结尾要有一个明确动作", body[-80:])

    for name, spec in PRODUCTS.items():
        at = _first(body, name)
        if at < 0:
            continue
        if not _setup_ok(body, name, spec["own"], at):
            _add(errors if spec["own"] else warnings, "product_setup",
                 f"{name} 第一次出现前没有说明“它是什么/为什么提”",
                 body[max(0, at - 40): at + 40])
        count = _mentions(body, name)
        if count == 1 and at > len(body) * 0.6 and not _setup_ok(body, name, spec["own"], at):
            _add(warnings, "product_drive_by",
                 f"{name} 只在结尾附近出现一次，读者会觉得突然", body[max(0, at - 30): at + 50])

    hard_hit = _claim_hit(body, ABSOLUTE_HARD)
    if hard_hit:
        _add(errors, "absolute_claim", f"出现没有证据的绝对化表述：{hard_hit}", hard_hit)
    soft_hit = _claim_hit(body, ABSOLUTE_SOFT)
    if soft_hit:
        _add(warnings, "absolute_soft", f"出现可能被判为绝对化的词：{soft_hit}", soft_hit)

    if not any(c in body for c in EVIDENCE_CUES):
        _add(infos, "no_evidence", "没有看到来源/版本/实测/合成/边界等证据标记")

    if channel == "xiaohongshu" and not any(c in body for c in ACTION_CUES):
        _add(infos, "no_action", "没有给读者一个今天就能做的动作")

    coined = [x for x in COINED if x in body]
    if coined:
        _add(warnings, "coined_term", "出现需要解释的行业黑话：" + "、".join(coined), "、".join(coined))

    jargon = _jargon_findings(body)

    if channel in ("xiaohongshu", "wechat"):
        codes = sorted(set(re.findall(r"(?<![A-Za-z0-9])E[0-5][+-]?(?![A-Za-z0-9])", body)))
        if codes:
            _add(errors, "evidence_code",
                 "公开稿出现内部证据编码：" + "、".join(codes) + "；改成读者能懂的行为描述",
                 "、".join(codes))
        internal = sorted(set(re.findall(r"\b(?:strict_master|corpus_role|sampling_frame)\b", body)))
        if internal:
            _add(errors, "internal_term",
                 "公开稿出现内部字段名：" + "、".join(internal) + "；只留在方法稿",
                 "、".join(internal))

    if strict:
        errors = errors + [dict(w, code="strict_" + w["code"]) for w in warnings]
        warnings = []

    return {
        "channel": channel,
        "title": title,
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "info": {"jargon": jargon, "notes": infos},
        "length": length,
        "cta": cta,
        "rules": rules,
        "strict": strict,
        "audit_scope": "structural_only",
        "editorial_review": "pending",
        "publish_ready": False,
    }


def audit_file(channel: str, text: str, *, strict: bool = False,
               titles: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """Audit every `## D2-XX｜标题` section, or the whole text if none.

    Section headings are editorial titles; they are only checked when a
    ``titles`` map is supplied (for example the short publish titles from 13).
    """
    sections = _sections(text)
    if not sections:
        doc = audit_text(channel, text, strict=strict)
        return {"channel": channel, "ok": doc["ok"], "documents": [doc], "batch_warnings": []}
    docs = []
    for did, title, body in sections:
        publish_title = (titles or {}).get(did, "")
        docs.append(audit_text(channel, body, title=publish_title or title,
                               strict=strict, check_title=bool(publish_title)))
    # Editorial similarity is a review signal, not proof of duplicate publishing.
    # Ignore the repeated delivery CTA: it is not the article's new information.
    batch_warnings = []
    seen = set()
    normalized = []
    for did, _, body in sections:
        if did in seen:
            batch_warnings.append({"code": "duplicate_id", "ids": [did],
                                   "message": f"{did} 重复出现；先核对源稿，不能覆盖同编号"})
        seen.add(did)
        paras = [p for p in body.split("\n\n") if not re.search(r"关注.*私信|首次协助", p)]
        normalized.append((did, re.sub(r"\W+", "", "".join(paras))))
    for i, (left_id, left) in enumerate(normalized):
        for right_id, right in normalized[i + 1:]:
            if min(len(left), len(right)) < 100:
                continue
            ratio = SequenceMatcher(None, left, right, autojunk=False).ratio()
            if ratio >= 0.60:
                batch_warnings.append({"code": "similar_posts", "ids": [left_id, right_id],
                    "similarity": round(ratio, 3),
                    "message": f"{left_id} 与 {right_id} 正文接近；核对是否新增任务、证据或反例"})
    return {"channel": channel, "ok": all(d["ok"] for d in docs) and not (strict and batch_warnings),
            "documents": docs, "batch_warnings": batch_warnings}


def brief(channel: str, topic: str, *, audience: str = "", product: str = "",
          evidence: str = "", cta: str = "B", title: str = "") -> Dict[str, Any]:
    """Return a channel-specific content brief, not a generated article."""
    channel = (channel or "").strip().lower()
    if channel not in CHANNELS:
        raise ValueError(f"未知渠道：{channel}；可选 {', '.join(CHANNELS)}")
    rules = CHANNELS[channel]
    if cta not in rules["cta"]:
        raise ValueError(f"{channel} 不支持 CTA {cta}；可选 {', '.join(rules['cta'])}")
    return {
        "schema_version": SCHEMA_VERSION,
        "channel": channel,
        "label": rules["label"],
        "topic": topic,
        "audience": audience,
        "product": product,
        "evidence": evidence,
        "title": {"draft": title, "max": rules["title_max"], "note": rules["title_note"]},
        "length": rules["length"],
        "structure": rules["structure"],
        "tone": rules["tone"],
        "cta": {"type": cta, "text": rules["cta"][cta]},
        "metrics": rules["metrics"],
        "images": rules["images"],
        "reuse": rules["reuse"],
        "positioning": POSITIONING,
        "editorial_review": {"required": True, "command": "mkt content review", "criteria": CRITERIA},
        "checklist": [
            "开头点名读者的具体行为或场景，不先讲道理",
            "正文不写“这篇只回答一件事”这类元叙述",
            "专业词第一次出现用括号解释",
            "项目/产品第一次出现先说它是什么、为什么提",
            "作者做的产品标明“我自己在做”",
            "证据支持读者要做的判断；不为凑信息密度强塞数字",
            "写清哪里做不到、什么还没有验证",
            "结尾给一个渠道原生动作",
        ] + list(rules.get("strategy_checklist") or []),
    }


def plan(*, start: str = "2026-11-07", days: int = 14,
         channels: Sequence[str] = ("xiaohongshu", "wechat", "blog"),
         cadence: str = "evidence") -> Dict[str, Any]:
    """Build a multi-channel schedule skeleton from the platform strategy layer.

    ``cadence='evidence'`` 用平台策略层的证据档周节奏：小红书 3 篇/周、
    公众号 2 篇/周、知乎 2 篇/周、抖音 3 条/周、博客 1 篇/周。
    ``cadence='daily-two'`` 复现用户指定的小红书每天两篇战术；它不是平台
    默认节奏，执行条件与效果都未验证。

    这是编辑排班，不是平台定时；所有行都是 planned，未发布、未回执。
    """
    if days < 1:
        raise ValueError("days 至少为 1")
    mode = str(cadence or "evidence").strip().lower()
    if mode not in ("evidence", "daily-two"):
        raise ValueError("cadence 只能是 evidence 或 daily-two")
    allowed = set(CHANNELS)
    picked = [c for c in channels if c in allowed]
    if not picked:
        raise ValueError("至少选择一个渠道：" + "/".join(CHANNELS))
    if mode == "daily-two" and "xiaohongshu" not in picked:
        mode = "evidence"
    start_date = date.fromisoformat(start)
    xhs_themes = ["跨圈层项目", "自有研究", "产品场景", "比较/答疑"]
    wechat_themes = ["硬件主线", "视频工具", "交付验收", "复盘"]
    zhihu_themes = ["关键词选题", "实测复盘"]
    douyin_themes = ["15 秒演示", "对比实验", "失败复盘"]
    blog_themes = ["方法/复现", "流程"]
    rows = []

    def _slot(channel: str, prefix: str) -> str:
        return f"{prefix}-{len([r for r in rows if r['channel'] == channel]) + 1:02d}"

    for offset in range(days):
        d = start_date + timedelta(days=offset)
        if "xiaohongshu" in picked:
            times = ("12:15", "20:30") if mode == "daily-two" else ("12:15",)
            if mode == "daily-two" or offset % 7 in (0, 2, 4):
                for i, hhmm in enumerate(times):
                    k = offset * len(times) + i
                    rows.append({
                        "date": d.isoformat(), "time": hhmm, "channel": "xiaohongshu",
                        "slot": _slot("xiaohongshu", "XHS"),
                        "theme": xhs_themes[k % 4],
                        "length": f"{CHANNELS['xiaohongshu']['length']['min']}—"
                                  f"{CHANNELS['xiaohongshu']['length']['max']} 字",
                        "cta": ("B", "A", "B", "C", "B", "A", "B", "C",
                                "B", "A", "B", "C", "A", "C")[k % 14], "status": "planned",
                    })
        if "wechat" in picked and offset % 7 in (1, 5):
            rows.append({
                "date": d.isoformat(), "time": "20:00", "channel": "wechat",
                "slot": _slot("wechat", "GZH"),
                "theme": wechat_themes[(offset // 7 * 2 + (0 if offset % 7 == 1 else 1)) % 4],
                "length": f"{CHANNELS['wechat']['length']['min']}—{CHANNELS['wechat']['length']['max']} 字",
                "cta": "B", "status": "planned",
            })
        if "zhihu" in picked and offset % 7 in (0, 3):
            rows.append({
                "date": d.isoformat(), "time": "21:00", "channel": "zhihu",
                "slot": _slot("zhihu", "ZH"),
                "theme": zhihu_themes[(offset // 7 * 2 + (0 if offset % 7 == 0 else 1)) % 2],
                "length": f"{CHANNELS['zhihu']['length']['min']}—{CHANNELS['zhihu']['length']['max']} 字",
                "cta": "B", "status": "planned",
            })
        if "douyin" in picked and offset % 7 in (1, 4, 6):
            rows.append({
                "date": d.isoformat(), "time": "19:00", "channel": "douyin",
                "slot": _slot("douyin", "DY"),
                "theme": douyin_themes[len([r for r in rows if r['channel'] == 'douyin']) % 3],
                "length": "15—30 秒", "cta": "A", "status": "planned",
            })
        if "blog" in picked and offset % 7 == 3:
            rows.append({
                "date": d.isoformat(), "time": "09:00", "channel": "blog",
                "slot": _slot("blog", "BLOG"),
                "theme": blog_themes[(offset // 7) % 2],
                "length": f"{CHANNELS['blog']['length']['min']}—{CHANNELS['blog']['length']['max']} 字",
                "cta": "B", "status": "planned",
            })
    counts: Dict[str, int] = {}
    for r in rows:
        counts[r["channel"]] = counts.get(r["channel"], 0) + 1
    note = ("编辑建议，按证据档周节奏排班；不是平台定时，未发布、未回执。"
            if mode == "evidence" else
            "编辑建议，小红书按「每天两篇」战术档排班（用户指定，非平台默认，效果未验证）；"
            "不是平台定时，未发布、未回执。")
    return {
        "schema_version": SCHEMA_VERSION,
        "start": start_date.isoformat(),
        "days": days,
        "cadence": mode,
        "channels": picked,
        "counts": counts,
        "rows": rows,
        "note": note,
    }


def _cadence_text(cadence: Any) -> str:
    if not isinstance(cadence, dict):
        return str(cadence)
    low, high = cadence.get("per_week_min"), cadence.get("per_week_max")
    if low and high and low != high:
        text = f"每周 {low}–{high} 篇"
    elif high:
        text = f"每周约 {high} 篇"
    else:
        text = "未定"
    if cadence.get("note"):
        text += f"；{cadence['note']}"
    return text


def render_rules(channel: str) -> str:
    channel = channel.lower()
    if channel not in CHANNELS:
        raise ValueError(f"未知渠道：{channel}")
    r = CHANNELS[channel]
    lines = [f"# {r['label']}内容规则", ""]
    lines.append(f"- 频率：{_cadence_text(r.get('cadence'))}")
    if r["length"].get("max"):
        lines.append(f"- 长度：{r['length']['min']}—{r['length']['max']} 字")
    else:
        lines.append("- 长度：视频/短文，无正文字数硬要求；重点在前 3 秒")
    if r.get("title_max"):
        lines.append(f"- 标题：最多 {r['title_max']} 字；{r['title_note']}")
    lines.append(f"- 语气：{r['tone']}")
    lines.append(f"- 结构：{' → '.join(r['structure'])}")
    lines.append(f"- 配图：{r['images']}")
    lines.append(f"- 复用：{r['reuse']}")
    lines.append(f"- 指标：{'；'.join(r['metrics'])}")
    lines += ["", "## CTA", ""]
    for k, v in r["cta"].items():
        lines.append(f"- {k}：{v}")
    if channel == "xiaohongshu":
        lines.append("- B-safe：不带私信，用「关注我，评论区说说你的场景」；"
                     "平台规则把私信导流列为高频违规，审稿同样接受这种站内承接。")
    overrides = r.get("overrides") or []
    if overrides:
        lines += ["", "## 战术覆盖（不是平台默认）", ""]
        for ov in overrides:
            lines.append(f"- {ov['id']}：每天 {ov.get('per_day')} 篇 / 每周 {ov.get('per_week')} 篇"
                         f" — {ov.get('note', '')}（{ov.get('guide', '')}）")
    gates = r.get("gates") or []
    if gates:
        lines += ["", "## 平台门禁（审稿会拦）", ""]
        for gate in gates:
            how = "自动" if gate.get("detector") else "人工清单"
            lines.append(f"- [{gate['level']}] {gate['statement']} → {gate.get('fix', '')}"
                         f"（{how}；{gate.get('source', '')}）")
    checklist = r.get("strategy_checklist") or []
    if checklist:
        lines += ["", "## 发布前人工检查", ""]
        lines += [f"- [ ] {item}" for item in checklist]
    return "\n".join(lines)


def render_audit(result: Dict[str, Any]) -> str:
    lines = []
    if "documents" in result:
        lines.append(f"# 审稿结果 · {result['channel']}")
        lines.append("")
        lines.append(f"结构检查：{'通过' if result['ok'] else '有阻断项'}；整篇审稿需另做 content review")
        for warning in result.get("batch_warnings", []):
            lines.append(f"- 整组提醒 [{warning['code']}]：{warning['message']}")
        for doc in result["documents"]:
            lines.append("")
            lines.append(f"## {doc['title'] or '（无标题）'}")
            lines.append(f"- 结果：{'通过' if doc['ok'] else '不通过'}；正文 {doc['length']} 字")
            for e in doc["errors"]:
                lines.append(f"- 错误 [{e['code']}]：{e['message']}")
            for w in doc["warnings"]:
                lines.append(f"- 提醒 [{w['code']}]：{w['message']}")
            for note in doc.get("info", {}).get("notes", []):
                lines.append(f"- 提示 [{note['code']}]：{note['message']}")
        return "\n".join(lines)
    lines.append(f"# 审稿结果 · {result['channel']} · {result['title'] or '（无标题）'}")
    lines.append("")
    lines.append(f"结论：{'通过' if result['ok'] else '不通过'}；正文 {result['length']} 字；CTA {result.get('cta') or '未识别'}")
    for e in result["errors"]:
        lines.append(f"- 错误 [{e['code']}]：{e['message']}")
    for w in result["warnings"]:
        lines.append(f"- 提醒 [{w['code']}]：{w['message']}")
    for note in result.get("info", {}).get("notes", []):
        lines.append(f"- 提示 [{note['code']}]：{note['message']}")
    return "\n".join(lines)


def render_brief(b: Dict[str, Any]) -> str:
    lines = [f"# 内容 brief · {b['label']} · {b['topic']}", ""]
    lines.append(f"- 受众：{b['audience'] or '按定位默认读者'}")
    lines.append(f"- 产品/项目：{b['product'] or '不指定'}")
    lines.append(f"- 证据：{b['evidence'] or '待补'}")
    lines.append(f"- 标题：{b['title']['draft'] or '待写'}（最多 {b['title']['max']} 字）")
    lines.append(f"- 长度：{b['length']['min']}—{b['length']['max']} 字")
    lines.append(f"- CTA：{b['cta']['type']} · {b['cta']['text']}")
    lines += ["", "## 结构", ""]
    lines += [f"{i}. {x}" for i, x in enumerate(b["structure"], 1)]
    lines += ["", "## 发布前检查", ""]
    lines += [f"- [ ] {x}" for x in b["checklist"]]
    return "\n".join(lines)


def render_plan(p: Dict[str, Any]) -> str:
    lines = [f"# {p['days']} 天多渠道排期", ""]
    lines.append(f"开始：{p['start']}；渠道：{', '.join(p['channels'])}")
    lines.append(f"数量：{p['counts']}")
    lines.append("")
    lines.append("| 日期 | 时间 | 渠道 | 槽位 | 主题 | 长度 | CTA |")
    lines.append("|---|---|---|---|---|---|---|")
    for r in p["rows"]:
        lines.append(f"| {r['date']} | {r['time']} | {r['channel']} | {r['slot']} | {r['theme']} | {r['length']} | {r['cta']} |")
    lines += ["", f"> {p['note']}"]
    return "\n".join(lines)


def dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2)
