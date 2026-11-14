#!/usr/bin/env python3
"""信任节点目标库：把「先打谁」从策略原则变成可执行的清单与评分。

研究 II（2026-11-14）的核心结论：信任随粉丝量与商单密度衰减——
  1-5 万粉互动率 2-4%，是 50 万+ 粉的 2-4 倍；百万粉以上中位数 0.33%；
  72% 用户更信任 10 万粉以下创作者；未接商单的 KOC 信任溢价最高。
所以目标选择的本质是「买在信任价格最低点」。

本模块把它变成代码：
  score_candidate()   按信任价格给候选账号打分（0-100），并给出理由
  TargetList          目标清单对象（data/targets/<id>.json）：
                      candidate → contacted → engaged → amplified → partner
                      阶段只进不退（engaged/amplified 是 koc-first 战术的判定输入）

只读写本地文件，不抓取任何平台数据——候选数据由运营 agent 从平台读入。
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional
from .grow_lock import locked, run_lock

SCHEMA = "growovo.target-list/1"

# KOC 信任甜区（研究 II）：粉丝 1 千-5 万、商单痕迹少于 3 条
KOC_FOLLOWER_WINDOW = (1_000, 50_000)
SPONSORED_SOFT_CAP = 3

# 粉丝档位 → (信任系数, 档名)。系数编码「信任随粉丝量衰减」：
# 甜区 1.0；5 万-50 万仍有垂直信任但开始商业化（0.55）；50 万+ 信任被稀释（0.22）。
FOLLOWER_TIERS = [
    ((0, 1_000), 0.35, "tiny"),
    ((1_000, 50_000), 1.0, "koc_window"),
    ((50_000, 500_000), 0.55, "trusted_mid"),
    ((500_000, 10 ** 12), 0.22, "mega_low_trust"),
]

# 各档互动率基准（研究 II：nano 1.78-4% vs mega 0.33%），用于算「相对同档的互动溢价」
ER_BASELINE = {
    "tiny": 0.035, "koc_window": 0.025, "trusted_mid": 0.012, "mega_low_trust": 0.0033,
}

STAGES = ("candidate", "contacted", "engaged", "amplified", "partner")

WEEK_DAYS = 7


class TargetingError(ValueError):
    pass


def _today() -> str:
    return date.today().isoformat()


def tier_of(followers: int) -> str:
    for (lo, hi), _, name in FOLLOWER_TIERS:
        if lo <= followers < hi:
            return name
    return "mega_low_trust"


def score_candidate(c: Dict[str, Any]) -> Dict[str, Any]:
    """按「信任价格」给一个候选账号打分（0-100），附人可读的理由。

    候选字段：platform、handle、followers（必填）、engagement_rate（0-1，可选）、
    sponsored_recent（近 90 天商单数，可选）、niche_match（布尔，可选）。
    """
    followers = c.get("followers")
    if type(followers) is not int or not 0 < followers < 10 ** 12:
        raise TargetingError("followers 必须是正整数（从平台读入后再来）")
    er = c.get("engagement_rate")
    if er is not None:
        if not isinstance(er, (int, float)) or not 0 <= float(er) <= 1:
            raise TargetingError("engagement_rate 应为 0-1 之间的小数（如 0.032）")
    sponsored = c.get("sponsored_recent")
    if sponsored is not None and (type(sponsored) is not int or sponsored < 0):
        raise TargetingError("sponsored_recent 应为非负整数")

    tier = tier_of(followers)
    reasons: List[str] = []

    # ① 信任档位系数（买在信任价格最低点）
    tier_factor = next(f for (lo, hi), f, _ in FOLLOWER_TIERS if lo <= followers < hi)
    if tier == "koc_window":
        reasons.append(f"粉丝 {followers:,} 在 KOC 甜区（1 千-5 万），信任价格最低")
    elif tier == "trusted_mid":
        reasons.append(f"粉丝 {followers:,} 偏大：垂直信任仍在但开始折价")
    elif tier == "mega_low_trust":
        reasons.append(f"粉丝 {followers:,} 属大号：互动率中位数仅约 0.33%，信任被商业化稀释")
    else:
        reasons.append(f"粉丝 {followers:,} 过小：信任高但声量不足")

    # ② 相对同档基准的互动溢价（同档里互动越好越值得先打）
    er_factor = 1.0
    if er is not None:
        baseline = ER_BASELINE[tier]
        ratio = float(er) / baseline if baseline else 0.0
        er_factor = max(0.5, min(2.0, ratio))
        if ratio >= 1.2:
            reasons.append(f"互动率 {float(er):.1%} 高于同档基准 {baseline:.1%}")
        elif ratio < 0.6:
            reasons.append(f"互动率 {float(er):.1%} 低于同档基准 {baseline:.1%}，数据可疑或冷号")

    # ③ 商业化折价（商单痕迹越少信任溢价越高，>=3 条明显折价）
    com_factor = max(0.2, 1.0 - 0.2 * sponsored) if sponsored is not None else 1.0
    if sponsored is None:
        reasons.append("商单数据未知：不推断为零，评分仅作待核实排序")
    elif sponsored == 0:
        reasons.append("近 90 天无商单痕迹：信任窗口期（最便宜）")
    elif sponsored >= SPONSORED_SOFT_CAP:
        reasons.append(f"近 90 天商单 {sponsored} 条：推荐真实感已折价")

    # ④ 领域匹配
    niche = c.get("niche_match")
    niche_factor = 1.2 if niche else (0.5 if niche is False else 1.0)
    if niche is True:
        reasons.append("领域匹配：其粉丝就是目标人群")
    elif niche is False:
        reasons.append("领域不匹配：声量再大也带不来精准受众")

    raw = 100 * tier_factor * er_factor * com_factor * niche_factor
    score = int(round(max(0.0, min(100.0, raw))))
    return {"score": score, "tier": tier, "reasons": reasons,
            "evidence_missing": [k for k in ("engagement_rate", "sponsored_recent", "niche_match")
                                 if c.get(k) is None],
            "in_koc_window": KOC_FOLLOWER_WINDOW[0] <= followers < KOC_FOLLOWER_WINDOW[1]}


class TargetList:
    def __init__(self, path):
        self.path = Path(path)

    # ---------- 存取 ----------

    def load(self) -> Dict[str, Any]:
        if not self.path.exists():
            raise FileNotFoundError(f"没有这个目标清单：{self.path}")
        return json.loads(self.path.read_text(encoding="utf-8"))

    def save(self, data: Dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode="w", dir=self.path.parent,
                                         delete=False, encoding="utf-8") as fh:
            temporary = Path(fh.name)
            try:
                fh.write(json.dumps(data, ensure_ascii=False, indent=1) + "\n")
                fh.flush()
                os.fsync(fh.fileno())
                os.replace(temporary, self.path)
            finally:
                temporary.unlink(missing_ok=True)

    # ---------- 生命周期 ----------

    @classmethod
    def start(cls, path, list_id: str, *, niche: str = "", owner: str = "",
              created: Optional[str] = None) -> Dict[str, Any]:
        data = {
            "schema": SCHEMA,
            "id": list_id,
            "niche": niche,
            "owner": owner,
            "created": created or _today(),
            "targets": [],
            "history": [],
        }
        with run_lock(path):
            if Path(path).exists():
                raise TargetingError("目标清单已存在；不会覆盖已有触达记录")
            cls(path).save(data)
        return data

    @locked
    def add(self, platform: str, handle: str, followers: int, *,
            engagement_rate: Optional[float] = None,
            sponsored_recent: Optional[int] = None,
            niche_match: Optional[bool] = None,
            note: str = "", on: Optional[str] = None) -> Dict[str, Any]:
        """加入候选：自动打分去重；同 platform+handle 已存在则拒绝（防重复触达）。"""
        on = on or _today()
        data = self.load()
        platform = platform.strip().lower()
        handle = handle.strip().lstrip('@').lower()
        if not platform or not handle:
            raise TargetingError("platform 和 handle 不能为空")
        date.fromisoformat(on)
        key = f"{platform}:{handle}"
        if any(t["key"].lower() == key for t in data["targets"]):
            raise TargetingError(f"{key} 已在清单里：目标去重是防重复触达的硬约束")
        scored = score_candidate({"followers": followers,
                                  "engagement_rate": engagement_rate,
                                  "sponsored_recent": sponsored_recent,
                                  "niche_match": niche_match})
        target = {
            "key": key, "platform": platform, "handle": handle.lstrip("@"),
            "followers": followers,
            "engagement_rate": engagement_rate,
            "sponsored_recent": sponsored_recent,
            "niche_match": niche_match,
            "note": note,
            "score": scored["score"], "tier": scored["tier"],
            "reasons": scored["reasons"], "in_koc_window": scored["in_koc_window"],
            "stage": "candidate", "added_on": on,
            "stage_history": [{"stage": "candidate", "on": on, "note": ""}],
        }
        data["targets"].append(target)
        self.save(data)
        return target

    @locked
    def advance(self, key: str, stage: str, *, note: str = "",
                on: Optional[str] = None) -> Dict[str, Any]:
        """阶段只进不退：candidate→contacted→engaged→amplified→partner。"""
        if stage not in STAGES:
            raise TargetingError(f"未知阶段 {stage!r}；可选：{' → '.join(STAGES)}")
        on = on or _today()
        date.fromisoformat(on)
        if not note.strip():
            raise TargetingError("阶段推进需要 --note 记录实际证据；入队不算触达，发出不算互动")
        data = self.load()
        key = key.strip().lower().lstrip("@").replace(":@", ":")
        matches = [t for t in data["targets"]
                   if t["key"].lower() == key or t["key"].lower().endswith(f":{key}")]
        if len(matches) > 1:
            raise TargetingError("多个平台有同名账号；请用 platform:handle 指定目标")
        target = matches[0] if matches else None
        if target is None:
            raise TargetingError(f"清单里没有 {key}")
        current = STAGES.index(target["stage"])
        wanted = STAGES.index(stage)
        if wanted <= current:
            raise TargetingError(
                f"{key} 已在 {target['stage']}，不能退到 {stage}（阶段只进不退）")
        if wanted > current + 1:
            raise TargetingError(
                f"{key} 不能从 {target['stage']} 跳到 {stage}（逐级推进，一步一证据）")
        target["stage"] = stage
        target["stage_history"].append({"stage": stage, "on": on, "note": note})
        self.save(data)
        return target

    # ---------- 读数 ----------

    def next_batch(self, n: int = 5) -> List[Dict[str, Any]]:
        """下一批该接触谁：candidate 里按信任价格从高到低。"""
        data = self.load()
        rows = [t for t in data["targets"] if t["stage"] == "candidate"]
        rows.sort(key=lambda t: (-t["score"], t["key"]))
        return rows[:max(0, n)]

    def summary(self, on: Optional[str] = None) -> Dict[str, Any]:
        on = on or _today()
        data = self.load()
        by_stage = {s: 0 for s in STAGES}
        for t in data["targets"]:
            by_stage[t["stage"]] += 1
        week_start = (date.fromisoformat(on) - timedelta(days=WEEK_DAYS - 1)).isoformat()
        def reached(t, stage, since=""):
            return any(h["stage"] == stage and since <= h["on"] <= on
                       and str(h.get("note") or "").strip() for h in t["stage_history"])
        engaged_week = sum(reached(t, "engaged", week_start) for t in data["targets"])
        amplified_total = sum(reached(t, "amplified") for t in data["targets"])
        koc_window = sum(1 for t in data["targets"] if t.get("in_koc_window"))
        scores = [t["score"] for t in data["targets"]]
        return {
            "list": data["id"], "niche": data.get("niche", ""),
            "total": len(data["targets"]), "by_stage": by_stage,
            "koc_window": koc_window,
            "engaged_last_7d": engaged_week,
            "amplified_total": amplified_total,
            "koc_engaged_last_7d": sum(reached(t, "engaged", week_start)
                                      for t in data["targets"] if t.get("in_koc_window")),
            "koc_amplified_total": sum(reached(t, "amplified")
                                       for t in data["targets"] if t.get("in_koc_window")),
            "avg_score": int(round(sum(scores) / len(scores))) if scores else 0,
        }

    def render_summary(self, on: Optional[str] = None) -> str:
        s = self.summary(on)
        lines = [
            f"目标清单 {s['list']}{'（' + s['niche'] + '）' if s['niche'] else ''}："
            f"共 {s['total']} 个，KOC 甜区 {s['koc_window']} 个，均分 {s['avg_score']}",
            "  阶段：" + "｜".join(f"{stage} {n}" for stage, n in s["by_stage"].items()),
            f"  近 7 天互动 {s['engaged_last_7d']} 个｜累计再传播 {s['amplified_total']} 个"
            "（koc-first 战术的判定输入）",
        ]
        for t in self.next_batch(3):
            lines.append(f"  下一步 → {t['key']}（{t['score']} 分·{t['tier']}）：{t['reasons'][0] if t['reasons'] else ''}")
        return "\n".join(lines)
