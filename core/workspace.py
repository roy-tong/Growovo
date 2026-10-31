#!/usr/bin/env python3
"""工作区解析 —— 一个产品一个 data 目录（多产品隔离的最小实现）。

背景：Growovo 内核原先默认 `<repo>/data`，同时跑 askovo / verovo / Growovo
自己的实验与事件时，漏斗与归因会混在一起（实测 data/experiments 里并存
verovo-* 与 p0-d14-gate）。本模块给"一个产品一个账本"提供唯一解析入口。

优先级：显式 --workspace > 环境变量 MKT_HOME > <repo>/data（向后兼容）。
相对路径按当前工作目录解析；不创建目录，创建由 ensure() 负责。
"""
import os
from pathlib import Path

ENV = "MKT_HOME"

# data/ 下的标准子目录（schemas.md 的数据三件套 + 实验/留证/状态）
SUBDIRS = ("plans", "queues", "outcomes", "experiments", "state", "shots")


def resolve(workspace=None, *, base=None, env=None):
    """返回绝对路径的 data 目录。不创建、不校验存在性。"""
    env = os.environ if env is None else env
    raw = (workspace or env.get(ENV) or "").strip()
    if raw:
        p = Path(raw).expanduser()
        if not p.is_absolute():
            p = Path.cwd() / p
        return p.resolve()
    root = Path(base) if base else Path(__file__).resolve().parents[1]
    return (root / "data").resolve()


def ensure(data_dir):
    """建齐标准子目录，返回 data 目录。幂等。"""
    d = Path(data_dir)
    for sub in SUBDIRS:
        (d / sub).mkdir(parents=True, exist_ok=True)
    return d


def describe(data_dir):
    """给 init/doctor 用的一行描述：来源是显式、环境变量还是默认。"""
    d = Path(data_dir)
    if os.environ.get(ENV) and Path(os.environ[ENV]).expanduser().resolve() == d:
        return f"{d}（来自 {ENV}）"
    return f"{d}"
