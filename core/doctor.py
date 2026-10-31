#!/usr/bin/env python3
"""环境体检（W1 清单 B4）——一条命令分清「坏环境」与「坏内核」。

分级：FAIL = 内核/运行时问题（必修，退出码 1）；WARN = 仅影响对应渠道/能力；
INFO 并入前两级的说明文字。CDP 与凭据都是可选项：不配置只影响对应渠道，
不阻碍 doctor、demo、lint、账本查询等离线能力。
"""
import importlib
import json
import os
import subprocess
import sys
import urllib.request
from pathlib import Path

KERNEL_MODULES = [
    "core.ledger", "core.gates", "core.throttle", "core.pipeline",
    "core.planlint", "core.safety", "core.contacts", "core.transports",
    "core.demo", "channels",
]


def _probe_cdp(cdp_url):
    """裸 HTTP 探测调试端口（不依赖 playwright）。返回 Browser 名或 None。"""
    try:
        with urllib.request.urlopen(f"{cdp_url.rstrip('/')}/json/version", timeout=2) as r:
            return json.loads(r.read().decode("utf-8")).get("Browser")
    except Exception:
        return None


def _tail(text, n=2):
    lines = [l for l in (text or "").splitlines() if l.strip()]
    return "；".join(lines[-n:]) if lines else ""


def run_doctor(base, data_dir, selftest=False):
    """base = 仓库根；data_dir = data/ 目录。返回 0=无 FAIL，1=有 FAIL。"""
    from .cdp import CDP_URL
    from .safety import is_paused

    base, data_dir = Path(base), Path(data_dir)
    rows = []  # (level, name, detail)

    def ok(name, detail=""):
        rows.append(("OK", name, detail))

    def warn(name, detail):
        rows.append(("WARN", name, detail))

    def fail(name, detail):
        rows.append(("FAIL", name, detail))

    # 运行时与内核
    v = sys.version.split()[0]
    if sys.version_info >= (3, 9):
        ok("python", v)
    else:
        fail("python", f"{v}，内核要求 ≥3.9")
    bad = []
    for m in KERNEL_MODULES:
        try:
            importlib.import_module(m)
        except Exception as exc:
            bad.append(f"{m}: {str(exc)[:80]}")
    if bad:
        fail("内核导入", "; ".join(bad)[:200])
    else:
        ok("内核导入", f"{len(KERNEL_MODULES)} 个模块全部可导入")

    # 数据契约与数据目录
    if (base / "data" / "schemas.md").exists():
        ok("数据契约", "data/schemas.md（全部队列/账本/计划 schema）")
    else:
        warn("数据契约", "data/schemas.md 缺失")
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        probe = data_dir / ".doctor-probe"
        probe.write_text("", encoding="utf-8")
        probe.unlink()
        ok("数据目录", f"{data_dir}（可写）")
    except OSError as exc:
        fail("数据目录", f"{data_dir} 不可写: {exc}")

    ledger_file = data_dir / "ledger.jsonl"
    if ledger_file.exists():
        n = sum(1 for l in ledger_file.read_text(encoding="utf-8").splitlines() if l.strip())
        ok("账本", f"{ledger_file.name} {n} 条记录")
    else:
        ok("账本", "尚未创建（首次写操作自动生成）")

    # 可选依赖与凭据（缺了只影响对应渠道）
    try:
        import playwright  # noqa: F401
        ok("playwright", "已安装（CDP 渠道可用）")
    except ImportError:
        warn("playwright", "未安装：X/Reddit/LinkedIn/HN/CDP 读不可用（pip install -r requirements.txt）")
    if os.environ.get("MKT_EMAIL_USER") and os.environ.get("MKT_EMAIL_APP_PASSWORD"):
        ok("SMTP 凭据", "MKT_EMAIL_* 已配置（email 写可用）")
    else:
        warn("SMTP 凭据", "MKT_EMAIL_USER/MKT_EMAIL_APP_PASSWORD 未配置：email 写不可用")
    if os.environ.get("MKT_GITHUB_TOKEN"):
        ok("GitHub token", "MKT_GITHUB_TOKEN 已配置（github reply 可用）")
    else:
        warn("GitHub token", "MKT_GITHUB_TOKEN 未配置：github 写不可用（读原语匿名可用，60 次/时）")
    if os.environ.get("MKT_LINKEDIN_AUTO") == "1":
        warn("LinkedIn 全自动", "MKT_LINKEDIN_AUTO=1 已解锁（不建议；默认半自动需终端确认）")
    browser = _probe_cdp(os.environ.get("MKT_CDP_URL", CDP_URL))
    if browser:
        ok("CDP 会话", f"{browser}")
    else:
        warn("CDP 会话", "未响应：CDP 渠道不可用，./launch.command 启动（SMTP/API 渠道不受影响）")

    # 全局暂停态
    if is_paused(data_dir):
        warn("全局暂停", "一切写操作拒跑中（mkt pause --off 恢复）")
    else:
        ok("全局暂停", "OFF")

    if selftest:
        r = subprocess.run([sys.executable, "-m", "unittest", "discover", "tests"],
                           cwd=str(base), capture_output=True, text=True)
        summary = _tail(r.stdout) or (r.stderr or "")[:200]
        ok("离线单测", summary) if r.returncode == 0 else fail("离线单测", summary)

    icon = {"OK": "[ OK ]", "WARN": "[WARN]", "FAIL": "[FAIL]"}
    print(f"mkt doctor — {base}")
    for lvl, name, detail in rows:
        print(f"{icon[lvl]} {name}" + (f"：{detail}" if detail else ""))
    n_fail = sum(1 for r in rows if r[0] == "FAIL")
    n_warn = sum(1 for r in rows if r[0] == "WARN")
    print(f"\n{len(rows)} 项：FAIL {n_fail} / WARN {n_warn} / OK {len(rows) - n_fail - n_warn}")
    if n_fail:
        print("FAIL 必修；WARN 只影响对应渠道，不影响 demo/lint/账本等离线能力。")
    return 1 if n_fail else 0
