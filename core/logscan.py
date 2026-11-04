#!/usr/bin/env python3
"""日志扫描：把散落在本地目录里的运营记录，变成带出处的事实表。

复盘做不深的根因不是分析能力，是**没人愿意读几千个文件**。本模块替人读：
递归扫描目录 → 识别可读文件 → 逐行提取「渠道 + 指标 + 数字」→ 每条事实保留
文件名与行号，任何结论都能回溯到原文。

设计约束：
- 只读，不改任何文件；
- 单文件读取有上限（防止 485MB 的语料库拖死扫描）；
- 提取保守：数字必须同时靠近「渠道词」和「单位词」才收，宁可漏不可错；
- 冲突不裁决：同一个（渠道, 指标）出现多个数字时全部保留，由分析层决定怎么用。
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

TEXT_EXT = {".md", ".txt", ".json", ".jsonl", ".csv", ".log", ".yaml", ".yml"}
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build",
             ".pytest_cache", "egg-info", ".DS_Store"}
MAX_BYTES = 256 * 1024          # 单文件最多读 256KB
MAX_FILES = 20000               # 扫描文件数上限

# 渠道词典：命中任一别名即算提到该渠道。
from .platforms import SCAN_CHANNELS

# 平台识别词表统一来自 core/platforms.py：新平台只改那一处。
CHANNELS: Dict[str, Tuple[str, ...]] = dict(SCAN_CHANNELS)

# 指标词典：数字附近出现这些词，归到对应类别。
METRICS: Dict[str, Tuple[str, ...]] = {
    "response": ("回应", "回复", "回信", "回评", "答复", "submitted", "replied"),
    "outcome": ("star", "Star", "付费", "注册", "合并", "采纳", "客户", "成交", "付款", "申请"),
    "failure": ("事故", "退信", "删除", "删帖", "封号", "限流", "失败", "零回复", "0 回", "无回应",
                "kill", "dead", "拒"),
    "action": ("封", "条", "次", "个", "颗", "单", "篇", "次请求"),
}

NUM = re.compile(r"(?<![\d.])(\d[\d,]*(?:\.\d+)?)\s*"
                 r"(封|条|次|个|颗|单|篇|人|位)?")
DATE = re.compile(r"(20\d{2})[-/年](\d{1,2})[-/月](\d{1,2})")


def _norm_date(text: str) -> Optional[str]:
    m = DATE.search(text)
    if not m:
        return None
    y, mo, d = (int(x) for x in m.groups())
    if not (1 <= mo <= 12 and 1 <= d <= 31):
        return None
    return f"{y:04d}-{mo:02d}-{d:02d}"


def classify(path: Path) -> str:
    """文件分类：日志 / 记录 / 计划 / 复盘 / 其他。"""
    name = path.name.lower()
    if name.endswith((".json", ".jsonl", ".csv")):
        return "data"
    if name.endswith(".log"):
        return "log"
    if any(k in name for k in ("复盘", "retro", "总结", "review")):
        return "retro"
    if any(k in name for k in ("日志", "log", "记录", "归因", "台账", "ledger")):
        return "log"
    if any(k in name for k in ("计划", "plan", "清单", "方案", "策略", "todo")):
        return "plan"
    return "note"


def read_head(path: Path, limit: int = MAX_BYTES) -> str:
    try:
        with path.open("rb") as f:
            raw = f.read(limit)
        return raw.decode("utf-8", "ignore")
    except OSError:
        return ""


def walk(root: Path, include_ext: Iterable[str] = TEXT_EXT,
         exclude: Iterable[str] = SKIP_DIRS, max_files: int = MAX_FILES) -> List[Path]:
    include = {e.lower() for e in include_ext}
    excl = set(exclude)
    out: List[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in excl and not d.startswith(".")]
        for fn in filenames:
            if fn.startswith("."):
                continue
            p = Path(dirpath) / fn
            if p.suffix.lower() not in include:
                continue
            out.append(p)
            if len(out) >= max_files:
                return out
    return out


# 数值合理性上限：单个运营动作的量级不会过万。超过这个数的一律当噪声
# （文件大小、时间戳、ID、哈希）。
MAX_VALUE = 100000
# 数字周围多远内出现渠道词才算这个渠道的事实。
NEAR = 60
# 明显不是数量的后缀。
NOISE_SUFFIX = ("KB", "MB", "GB", "TB", "kb", "mb", "字节", "px", "ms", "秒", "%")


def extract_claims(path: Path, rel: str) -> List[Dict[str, Any]]:
    """从一个文件里提取 (渠道, 指标, 数字) 事实，带行号。

    三条防噪声规则（上一版被文件大小和日期污染过）：
      1. 数字必须离渠道词足够近，不是整行任意数字都算；
      2. 超过一万的数值直接丢——那不是动作数，是字节数或 ID；
      3. action 类必须带中文计量单位，避免把年份、编号当成动作量。
    """
    text = read_head(path)
    if not text:
        return []
    claims: List[Dict[str, Any]] = []
    for lineno, line in enumerate(text.splitlines(), 1):
        if len(line) > 4000:
            continue
        hits: List[tuple] = []
        for cid, names in CHANNELS.items():
            for name in names:
                pos = line.find(name)
                while pos >= 0:
                    hits.append((cid, pos))
                    pos = line.find(name, pos + 1)
        if not hits:
            continue
        date = _norm_date(line)
        for m in NUM.finditer(line):
            raw, unit = m.group(1), m.group(2) or ""
            tail = line[m.end():m.end() + 3]
            if any(tail.startswith(x) for x in NOISE_SUFFIX):
                continue
            digits = raw.replace(",", "").replace(".", "")
            if len(digits) == 8 and digits.startswith("20"):
                continue          # YYYYMMDD
            if m.start() > 0 and line[m.start() - 1] in "#vV$":
                continue          # PR 号 / 版本号 / 金额符号
            try:
                value = float(raw.replace(",", ""))
            except ValueError:
                continue
            if value <= 0 or value > MAX_VALUE:
                continue
            if not unit and 2020 <= value <= 2035:
                continue          # 不带单位的年份
            near = [cid for cid, pos in hits if abs(pos - m.start()) <= NEAR]
            if not near:
                continue
            lo, hi = max(0, m.start() - 40), min(len(line), m.end() + 40)
            ctx = line[lo:hi]
            metric = _classify_metric(ctx, unit)
            if metric is None:
                continue
            if metric == "action" and not unit:
                continue          # 无单位的数字多半是年份/编号
            for ch in near:
                claims.append({
                    "file": rel, "line": lineno, "channel": ch,
                    "metric": metric, "value": value, "unit": unit,
                    "date": date, "context": ctx.strip()[:160],
                    "sentence": line.strip()[:240],
                })
    return claims


def _classify_metric(ctx: str, unit: str) -> Optional[str]:
    for metric in ("response", "failure", "outcome"):
        if any(k in ctx for k in METRICS[metric]):
            return metric
    if unit or any(k in ctx for k in METRICS["action"]):
        return "action"
    return None


def inventory(root: Path) -> Dict[str, Any]:
    files = walk(root)
    kinds: Dict[str, int] = {}
    total = 0
    for p in files:
        k = classify(p)
        kinds[k] = kinds.get(k, 0) + 1
        try:
            total += p.stat().st_size
        except OSError:
            pass
    return {"root": str(root), "files": len(files), "bytes": total, "kinds": kinds}


DEFAULT_EXCLUDE_PATHS = ("历史副本", "代码仓库", "临时工作区", "output", "node_modules")


def scan(root, out_path, max_files: int = MAX_FILES,
         skip_kinds: Iterable[str] = ("data",),
         exclude_paths: Iterable[str] = DEFAULT_EXCLUDE_PATHS) -> Dict[str, Any]:
    """全量扫描并落盘：inventory + claims。

    默认跳过 data 类（json/jsonl/csv 原始语料）：那里面的数字是数据本身，
    不是运营动作；把它们当事实会污染整个复盘。
    """
    root = Path(root)
    skip = set(skip_kinds)
    bans = tuple(exclude_paths or ())
    files = [f for f in walk(root, max_files=max_files)
             if classify(f) not in skip
             and not any(b in str(f.relative_to(root)) for b in bans)]
    inv = inventory(root)
    claims: List[Dict[str, Any]] = []
    per_file: List[Dict[str, Any]] = []
    for p in files:
        rel = str(p.relative_to(root))
        c = extract_claims(p, rel)
        if c:
            per_file.append({"file": rel, "kind": classify(p), "claims": len(c)})
            claims.extend(c)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps({"inventory": inv, "claims": claims,
                                    "files_with_claims": per_file},
                                   ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return {"inventory": inv, "claims": len(claims), "files_with_claims": len(per_file),
            "saved": str(out_path)}


def load(path) -> Dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def scan_memory(root, max_files: int = MAX_FILES,
                skip_kinds: Iterable[str] = ("data",),
                exclude_paths: Iterable[str] = DEFAULT_EXCLUDE_PATHS) -> Dict[str, Any]:
    """只读扫描：不落盘，供 MCP 之类的工具调用。"""
    root = Path(root)
    skip = set(skip_kinds)
    bans = tuple(exclude_paths or ())
    files = [f for f in walk(root, max_files=max_files)
             if classify(f) not in skip
             and not any(b in str(f.relative_to(root)) for b in bans)]
    claims: List[Dict[str, Any]] = []
    for f in files:
        claims.extend(extract_claims(f, str(f.relative_to(root))))
    return {"inventory": inventory(root), "claims": claims}
