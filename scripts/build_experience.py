#!/usr/bin/env python3
"""把语料变成经验条目：抽句 → 聚类 → 按独立来源计数。

为什么不能人工一条条写：用户经验需要样本量，样本量上来之后人写不过来。
但也不能让机器直接「生成经验」——它只会把同一句话换个说法。所以这里的口径是：

    机器只做两件确定的事：找出「带动作的句子」，统计「多少篇独立文章说过同一件事」。
    人只做一件判断：这些话能不能当结论。

输出的每条都带 support（几篇提到）与 sources（哪几篇），support=1 的标为线索。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

BASE = Path(__file__).resolve().parents[1]
CORPUS = BASE / "docs" / "playbooks" / "经验库" / "corpus"
OUT = BASE / "docs" / "playbooks" / "经验库" / "experience.extracted.jsonl"

# 主题词表：按主题归并，比整句相似度稳得多（同一件事十种说法）
TOPICS = {
    "通用": [("定位", ("定位", "人设", "赛道")), ("选题", ("选题", "话题", "内容方向")),
             ("标题", ("标题",)), ("封面", ("封面", "首图")), ("发布节奏", ("频率", "日更", "周更", "发布时间")),
             ("互动", ("评论", "回复", "互动")), ("涨粉", ("涨粉", "新增粉丝", "粉丝增长")),
             ("变现", ("变现", "商单", "接广告", "带货")), ("限流", ("限流", "掉流量", "没有推荐")),
             ("违规", ("违规", "封号", "处罚", "审核不通过")), ("导流", ("导流", "私域", "微信")),
             ("账号搭建", ("养号", "起号", "新号", "账号权重"))],
    "xiaohongshu": [("搜索收录", ("搜索", "收录", "搜不到")), ("蒲公英", ("蒲公英", "报备"))],
    "zhihu": [("回答排序", ("排序", "权重", "赞同")), ("机构号", ("机构号",)),
              ("关键词库", ("关键词",)), ("热点借势", ("热点", "借势"))],
    "wechat": [("搜一搜", ("搜一搜", "微信搜索", "SEO")), ("打开率", ("打开率", "标题党", "阅读量")),
               ("转载互推", ("转载", "互推", "白名单"))],
    "douyin": [("完播", ("完播", "前3秒", "黄金三秒")), ("流量池", ("流量池", "推荐机制")),
               ("竖屏", ("竖屏", "9:16")), ("话题词", ("话题", "话题词"))],
    "bilibili": [("三连", ("三连", "投币", "收藏")), ("封面", ("封面",)), ("时长", ("时长", "几分钟"))],
    "zhishixingqiu": [("续费", ("续费", "复购", "留存")), ("交付", ("交付", "更新频率", "答疑"))],
    "weibo": [("热搜", ("热搜", "热榜")), ("话题", ("话题",))],
    "juejin": [("首页推荐", ("首页", "推荐", "上榜")), ("原创", ("原创", "搬运", "洗稿"))],
    "jike": [("圈子", ("圈子", "社区氛围"))],
}

ACTION_MARKERS = ("建议", "一定", "不要", "别", "关键", "核心", "技巧", "方法", "第一步",
                  "注意", "必须", "最好", "可以", "需要", "记住", "重点", "秘诀", "坑")
SPLIT = re.compile(r"[。！？；!?;\n]+")

# 公众号文章的固定尾巴（关注、领资料、课程报名）会跨文章重复出现，
# 不排掉就会把「广告模板」当成「用户共识」。
BOILERPLATE = ("公众号", "关注", "领取", "回复关键词", "扫码", "本文约", "原创",
               "阅读需要", "资料包", "训练营", "课程", "报名", "点击上方", "星标",
               "评论区交流", "转发", "点个在看", "分享给")


def norm(s: str) -> str:
    s = re.sub(r"[\s“”\"'‘’（）()【】\[\]<>《》]", "", s)
    s = re.sub(r"[0-9]+", "#", s)
    return s


def bigrams(s: str) -> set:
    return {s[i:i + 2] for i in range(len(s) - 1)} or {s}


def jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def parse_article(path: Path) -> Dict:
    raw = path.read_text(encoding="utf-8")
    head, _, body = raw.partition("\n\n")
    title = (re.search(r"TITLE:\s*(.+)", head) or [None, path.stem])[1]
    url = (re.search(r"URL:\s*(\S+)", head) or [None, ""])[1]
    pub = (re.search(r"PUBLISHED:\s*(\S+)", head) or [None, "None"])[1]
    published = None
    if pub and pub not in ("None", "null"):
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", pub):
            published = pub                      # 采集器直接写 ISO 日期
            from datetime import date as _date
            if _date.fromisoformat(published) > _date.today():
                published = _date(_date.fromisoformat(published).year - 1,
                                  _date.fromisoformat(published).month,
                                  _date.fromisoformat(published).day).isoformat()
        else:
            try:
                published = datetime.fromtimestamp(int(pub), tz=timezone.utc).date().isoformat()
            except (ValueError, OSError):
                published = None
    def flag(name: str) -> bool:
        m = re.search(rf"{name}:\s*(\S+)", head, re.I)
        return bool(m and m.group(1).strip().lower() in ("1", "true", "yes"))

    engine = (re.search(r"ASR_ENGINE:\s*(\S+)", head, re.I) or [None, ""])[1]
    return {"title": title.strip(), "url": url, "published": published, "body": body,
            "asr": flag("ASR"), "asr_engine": engine.strip(),
            "human_reviewed": flag("HUMAN_REVIEWED")}


def evidence_fields(articles: List[Dict], representative: Dict, knowledge_type: str) -> Dict:
    """Attach provenance without manufacturing review, rights or independence.

    Public availability is not a reuse licence. Machine extraction is never human
    review, so extracted rows remain candidates until a person supplies the rest.
    """
    urls = sorted({a.get("url", "").strip() for a in articles if a.get("url", "").strip()})
    engines = sorted({a.get("asr_engine") for a in articles
                      if a.get("asr") and a.get("asr_engine")})
    return {
        "knowledge_type": knowledge_type,
        "canonical_url": representative.get("url", ""),
        "rights": "unknown",
        "relevance": "unreviewed",
        "independent_sources": urls,
        "human_reviewed": False,
        "asr": any(a.get("asr") for a in articles),
        "asr_engines": engines,
    }


# 视频转写的文本没有标点、词间有空格：先按长度切块，否则整段进不来。
PLATFORM_HINTS = {
    "xiaohongshu": ("小红书",), "douyin": ("抖音",), "wechat": ("公众号", "搜一搜"),
    "zhihu": ("知乎",), "weibo": ("微博",), "bilibili": ("B站", "bilibili", "up主", "UP主"),
    "zhishixingqiu": ("知识星球",), "juejin": ("掘金",), "jike": ("即刻",),
}


def route_platform(pid: str, body: str) -> str:
    """内容经常讲的是另一个平台（B站讲小红书、小红书讲抖音）。

    按关键词归位到「真正被讲的那个平台」——但只有明显压过本平台时才改判，
    否则一篇讲小红书运营的笔记会被误判成讲 B 站。
    """
    hits = {k: sum(body.count(w) for w in ws) for k, ws in PLATFORM_HINTS.items()}
    best = max(hits, key=hits.get)
    own = hits.get(pid, 0)
    if best != pid and hits[best] >= 4 and hits[best] > own * 1.5:
        return best
    return pid


def chunk_plain(body: str, size: int = 70) -> List[str]:
    """没有标点的转写文本，按空格边界切成 40–90 字的块。"""
    words = body.split()
    out, cur = [], ""
    for w in words:
        if len(cur) + len(w) + 1 > size:
            if 25 <= len(cur):
                out.append(cur)
            cur = w
        else:
            cur = (cur + " " + w) if cur else w
    if 25 <= len(cur):
        out.append(cur)
    return out


def candidates(body: str) -> List[str]:
    out = []
    body = body.replace(" ", "") if body.count(" ") > len(body) / 8 else body
    if body.count("。") + body.count("，") < 5:
        body = "\n".join(chunk_plain(body))
    for s in SPLIT.split(body):
        s = s.strip()
        if not (12 <= len(s) <= 110):
            continue
        if not any(m in s for m in ACTION_MARKERS):
            continue
        if any(b in s for b in BOILERPLATE):
            continue
        if len(set(s)) < 8:
            continue
        out.append(s)
    return out


def topic_claims(pid: str, min_support: int = 2) -> List[Dict]:
    """按主题归并：同一个主题下有多少篇独立文章给了可执行建议。"""
    d = CORPUS / pid
    if not d.exists():
        return []
    topics = TOPICS["通用"] + TOPICS.get(pid, [])
    articles = [parse_article(f) for f in sorted(d.glob("*.txt"))]
    out = []
    for name, kws in topics:
        hits, hit_articles, titles, pub_max, routed = [], [], set(), None, pid
        for a in articles:
            routed = route_platform(pid, a["body"]) if len(articles) else pid
            picked = []
            for s in candidates(a["body"]):
                if any(k in s for k in kws):
                    picked.append(s)
            if picked:
                hits.append(min(picked, key=len))
                hit_articles.append(a)
                titles.add(a["title"])
                if a["published"] and (pub_max is None or a["published"] > pub_max):
                    pub_max = a["published"]
        if len(titles) < min_support:
            continue
        # 代表句：最短的那条，通常信息密度最高
        rep = sorted(hits, key=len)[0]
        routed = route_platform(pid, " ".join(hits))
        rep_bg = bigrams(norm(rep))
        # support 必须是「同一句话在几篇文章里出现」，不是「几篇谈到这个话题」。
        # 话题覆盖率另存 topic_articles，别混进 claim 的可信度。
        same_articles, same_titles = [], set()
        for a in articles:
            if any(jaccard(rep_bg, bigrams(norm(x))) >= 0.45 for x in candidates(a["body"])):
                same_articles.append(a)
                same_titles.add(a["title"])
        # A title is not proof of independence. Only distinct canonical URLs count.
        support = len({a["url"] for a in same_articles if a.get("url")})
        rep_article = hit_articles[hits.index(rep)]
        if support >= min_support:
            out.append({"platform": routed, "claim": f"{name}：{rep}", "action": rep,
                        "kind": "verbatim", "support": support, "topic_articles": len(titles),
                        "sources": sorted(same_titles)[:3],
                        "published": pub_max, "origin": f"corpus/topic:{name}",
                        **evidence_fields(same_articles, rep_article, "reproducible_method")})
        elif len(titles) >= 5:
            # 逐字复现很少见，但「很多篇文章都在谈这个主题」本身就是可用的信息：
            # 说明这是这个平台的关键变量。它不证明某个做法有效，只证明这个主题重要。
            out.append({"platform": routed, "claim": f"{name}：{rep}", "action": rep,
                        "kind": "topic", "support": 1, "topic_articles": len(titles),
                        "sources": sorted(titles)[:3],
                        "published": pub_max, "origin": f"corpus/topic:{name}",
                        **evidence_fields([rep_article], rep_article, "single_expert_experience")})
    out.sort(key=lambda r: -r["support"])
    return out


def cluster_for(pid: str, min_support: int = 2) -> List[Dict]:
    d = CORPUS / pid
    if not d.exists():
        return []
    articles = []
    for f in sorted(d.glob("*.txt")):
        a = parse_article(f)
        a["cands"] = candidates(a["body"])
        if a["cands"]:
            articles.append(a)

    clusters: List[Dict] = []
    for a in articles:
        for c in a["cands"]:
            n = norm(c)
            if len(n) < 10:
                continue
            bg = bigrams(n)
            placed = False
            for cl in clusters:
                if jaccard(bg, cl["bg"]) >= 0.34:
                    cl["members"].append(c)
                    cl["articles"].append(a)
                    if a["url"]:
                        cl["keys"].add(a["url"])
                    cl["titles"].add(a["title"])
                    if a["published"] and (cl["published"] is None or a["published"] > cl["published"]):
                        cl["published"] = a["published"]
                    placed = True
                    break
            if not placed:
                clusters.append({"bg": bg, "members": [c], "articles": [a],
                                 "keys": {a["url"]} if a["url"] else set(),
                                 "titles": {a["title"]}, "published": a["published"]})

    out = []
    for cl in clusters:
        support = len(cl["keys"])
        if support < min_support:
            continue
        rep = sorted(cl["members"], key=len)[0]
        rep_article = cl["articles"][cl["members"].index(rep)]
        out.append({
            "platform": route_platform(pid, " ".join(cl["members"])),
            "claim": rep,
            "action": rep,
            "kind": "verbatim",
            "support": support,
            "sources": sorted(cl["titles"])[:3],
            "published": cl["published"],
            "origin": "corpus",
            **evidence_fields(cl["articles"], rep_article, "reproducible_method"),
        })
    out.sort(key=lambda r: (-r["support"], -(len(r["published"]) if r["published"] else 0)))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--platform", default="")
    ap.add_argument("--min-support", type=int, default=2)
    args = ap.parse_args()
    pids = [args.platform] if args.platform else sorted(p.name for p in CORPUS.iterdir() if p.is_dir())
    rows = []
    for pid in pids:
        got = topic_claims(pid, args.min_support) + cluster_for(pid, args.min_support)
        rows.extend(got)
        arts = len(list((CORPUS / pid).glob("*.txt")))
        print(f"[{pid}] 文章 {arts} 篇 → 经验 {len(got)} 条（support>={args.min_support}）")
    OUT.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
    print(f"已写入 {OUT}（{len(rows)} 条）")


if __name__ == "__main__":
    main()
