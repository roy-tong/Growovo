#!/usr/bin/env python3
"""留证（架构文档 §5）——截图目录约定 + 结构化结果落盘 + 带时间戳日志。"""
import json
from datetime import datetime
from pathlib import Path


def log(msg, level="INFO"):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {level}: {msg}", flush=True)


class Evidence:
    def __init__(self, base_dir):
        self.base = Path(base_dir)

    def shot_path(self, *parts):
        """data/shots/<campaign>/<name>.png；调用方负责真正截图。"""
        p = self.base.joinpath(*parts)
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    def write_json(self, name, obj):
        self.base.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        p = self.base / f"{name}-{stamp}.json"
        p.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
        return p
