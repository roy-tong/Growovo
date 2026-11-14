#!/usr/bin/env python3
"""闭环运维：心跳、体检、定时安装。

没有心跳的「全自动」等于没人知道它停了。本模块只做三件事：
  1. 记录/判断最后一次 tick 是否新鲜；
  2. 一次体检把所有会阻断「无人值守」的问题列出来，并给修法；
  3. 生成 launchd / crontab 配置（默认只写文件，不擅自加载）。
"""
from __future__ import annotations

import json
import plistlib
import subprocess
import shlex
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

INTERVAL_DEFAULT = 30
HEARTBEAT_MULTIPLIER = 2


class OpsError(RuntimeError):
    pass


def _parse_time(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.astimezone()


def stamp_heartbeat(run: Dict[str, Any], *, at: str,
                    interval_minutes: int = INTERVAL_DEFAULT,
                    ok: bool = True) -> Dict[str, Any]:
    """把一次 tick 的时间写进 run（原地更新并返回 heartbeat）。"""
    heartbeat = {"last_tick": str(at),
                 "interval_minutes": int(interval_minutes or INTERVAL_DEFAULT),
                 "ok": bool(ok)}
    run["heartbeat"] = heartbeat
    return heartbeat


def heartbeat_state(run: Dict[str, Any], *, now: Optional[datetime] = None
                    ) -> Dict[str, Any]:
    """判断心跳是否新鲜；没有记录视为过期。"""
    heartbeat = run.get("heartbeat") or {}
    interval = int(heartbeat.get("interval_minutes") or INTERVAL_DEFAULT)
    last = _parse_time(heartbeat.get("last_tick"))
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    if last is None:
        return {"last_tick": None, "age_minutes": None,
                "interval_minutes": interval, "ok": False, "stale": True}
    age = max(0.0, (current - last).total_seconds() / 60.0)
    stale = age > interval * HEARTBEAT_MULTIPLIER or (last - current).total_seconds() > 300
    return {"last_tick": heartbeat.get("last_tick"),
            "age_minutes": round(age, 1),
            "interval_minutes": interval,
            "ok": bool(heartbeat.get("ok", True)) and not stale,
            "stale": stale}


def _check(name: str, ok: bool, level: str, detail: str,
           fix: str = "") -> Dict[str, Any]:
    return {"name": name, "ok": bool(ok), "level": level,
            "detail": detail, "fix": fix}


def _queue_files(data_dir: Path, run_id: str) -> List[Path]:
    directory = Path(data_dir) / "queues"
    if not directory.is_dir():
        return []
    return sorted(p for p in directory.glob(f"{run_id}-*.json") if p.is_file())


def doctor(data_dir, run: Dict[str, Any], *,
           now: Optional[datetime] = None,
           login_probe=None) -> Dict[str, Any]:
    """一次体检：返回 {"ok", "checks"}；只有 blocker 会让 ok=False。"""
    data_dir = Path(data_dir)
    run_id = str(run.get("id") or "")
    checks: List[Dict[str, Any]] = []

    schema = str(run.get("schema") or "")
    checks.append(_check(
        "run", schema == "growovo.grow-run/2", "blocker" if not schema else "info",
        f"schema={schema or '缺失'}，status={run.get('status')}，go={run.get('go')}",
        "老版本闭环请重新 mkt grow launch"))

    if (run.get("automation") or {}).get("enabled"):
        from .grow_agent import executable
        ready = bool(executable())
        checks.append(_check("content_agent", ready, "blocker" if not ready else "info",
                             "Codex CLI 可用；登录与调用需实际生成验证" if ready else "找不到 Codex CLI",
                             "设置 GROWOVO_CODEX_BIN 并完成 codex login"))
        production = (run.get("automation") or {}).get("last_result") or {}
        if production.get("error") or production.get("rejected"):
            checks.append(_check("content_supply", False, "warn",
                                 str(production.get("error") or production.get("rejected"))[:350],
                                 "检查候选来源、Codex 登录及审稿拒绝原因"))
    beat = heartbeat_state(run, now=now)
    if beat["last_tick"] is None:
        checks.append(_check("heartbeat", False, "warn",
                             "还没有 tick 记录", "mkt grow tick 或先 mkt grow install"))
    elif beat["stale"]:
        checks.append(_check("heartbeat", False, "warn",
                             f"上次 tick 在 {beat['last_tick']}，已 {beat['age_minutes']} 分钟",
                             "检查定时任务是否在跑：mkt grow install"))
    else:
        checks.append(_check("heartbeat", True, "info",
                             f"上次 tick {beat['age_minutes']} 分钟前"))

    from .execution_policy import allows
    missing = []
    for item in run.get("actions") or []:
        channel, action = str(item).split(":", 1)
        if not allows(data_dir, channel, action, "", ""):
            missing.append(item)
    checks.append(_check(
        "policy", not missing, "blocker" if missing else "info",
        ("授权缺失：" + "、".join(missing)) if missing else
        f"常驻授权覆盖 {len(run.get('actions') or [])} 个动作",
        'mkt policy enable --by "$USER" --actions ' + ",".join(sorted(missing))
        + " --days 30" if missing else ""))

    # 账号=浏览器登录态：闭环涉及的 CDP 渠道逐个探测（core/session_gate.py）。
    # 未登录是 blocker（读数与写动作都依赖它）；调试 Chrome 不在只是 warn。
    from .session_gate import CDP_UNAVAILABLE, LOGGED_IN, LOGGED_OUT, PROBES, check_login
    login_channel = next((c for c in (run.get("channels") or []) if c in PROBES), "")
    if login_channel:
        probe_login = login_probe or check_login
        try:
            state = probe_login(login_channel) or {}
            status = state.get("status")
        except Exception:  # noqa: BLE001 探测异常降级为未检，不让体检崩
            status = CDP_UNAVAILABLE
        if status == LOGGED_OUT:
            checks.append(_check("login", False, "blocker",
                                 f"{login_channel} 浏览器登录态缺失",
                                 "在调试 Chrome 里登录该站点后重跑 mkt grow doctor"))
        elif status == CDP_UNAVAILABLE:
            checks.append(_check("login", False, "warn",
                                 f"{login_channel} 登录态未检（调试 Chrome 未连接）",
                                 "运行 ./launch.command 启动调试 Chrome"))
        elif status == LOGGED_IN:
            checks.append(_check("login", True, "info",
                                 f"{login_channel} 浏览器登录态正常"))
        else:
            checks.append(_check("login", False, "warn",
                                 f"{login_channel} 登录态未确认", "检查页面是否仍在加载或出现验证提示"))

    days = (run.get("telemetry") or {}).get("days") or {}
    read_days = sum(1 for entry in days.values()
                    if (entry.get("readings") or {}).get("followers") is not None)
    elapsed = len(days)
    readings = (run.get("measurement") or {}).get("readers") or []
    if not readings:
        checks.append(_check("readings", True, "info", "未配置读数（目标不含平台指标）"))
    elif read_days == 0 and elapsed:
        checks.append(_check("readings", False, "warn",
                             f"{elapsed} 天里 0 天读到数",
                             "检查 --handle 与 CDP：mkt x stats --handle <账号>"))
    else:
        checks.append(_check("readings", True, "info",
                             f"{read_days}/{max(1, elapsed)} 天有读数"))

    queues = _queue_files(data_dir, run_id)
    bad_queues = []
    for path in queues:
        try:
            queue = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            bad_queues.append(f"{path.name}: {exc}")
            continue
        from .queue import lint_queue
        errs = lint_queue(queue, path=path)
        if errs:
            bad_queues.append(f"{path.name}: {'; '.join(errs[:3])}")
    checks.append(_check("queues", not bad_queues,
                         "blocker" if bad_queues else "info",
                         "; ".join(bad_queues) if bad_queues else
                         f"{len(queues)} 个队列文件通过编辑期检查",
                         "mkt queue lint <file> 逐个修"))

    run_dir = data_dir / "grow" / run_id
    plist = run_dir / f"com.growovo.{run_id}.plist"
    loop_script = run_dir / "run-loop.command"
    tick_log = run_dir / "tick.log"
    log_tail = ""
    if tick_log.is_file():
        log_tail = tick_log.read_text(encoding="utf-8", errors="replace")[-3000:]
    if "Operation not permitted" in log_tail or "Full Disk Access" in log_tail:
        checks.append(_check(
            "scheduler", False, "warn",
            "定时任务被 macOS 文档目录保护拦住（python 无完全磁盘访问权限）",
            "给 python3 完全磁盘访问权限后重装，或双击 run-loop.command 前台常驻"))
    elif plist.is_file():
        checks.append(_check("scheduler", True, "info",
                             f"已生成定时任务 {plist.name}"))
    elif loop_script.is_file():
        checks.append(_check("scheduler", True, "info",
                             "前台常驻脚本可用（run-loop.command）"))
    else:
        checks.append(_check("scheduler", False, "warn",
                             "还没有定时任务，闭环只能手动 tick",
                             f"mkt grow install {run_id}"))

    try:
        from . import grow_schedule as gs
        from .ledger import Ledger
        from .grow_v2 import GrowRunV2, _belongs
        ledger = Ledger(data_dir / "ledger.jsonl")
        committed_targets = set()
        done_today = 0
        for rec in ledger.all():
            if rec.get("result") == "committed" and _belongs(
                    str(rec.get("campaign") or ""), run_id):
                committed_targets.add(str(rec.get("target") or ""))
                if str(rec.get("ts") or "")[:10] == (now or datetime.now(timezone.utc)).date().isoformat():
                    done_today += 1
        started = str(run.get("started") or "")
        today = (now or datetime.now(timezone.utc)).date().isoformat()
        week_start = gs.week_start_of(today, started) if started else today
        daily = gs.split_week(int((run.get("quota") or {}).get("actions_per_week") or 0),
                              week_start,
                              (run.get("quota") or {}).get("action_weights"))
        daily_quota = max(1, sum(daily.get(today, {}).values()) or 1)
        state = gs.supply_status(
            [{"path": str(p), "queue": json.loads(p.read_text(encoding="utf-8"))}
             for p in queues if _safe_json(p)], today=today, days=int((run.get("supply") or {}).get("min_cover_days") or 3),
            daily_quota=daily_quota, committed_targets=committed_targets,
            done_today=done_today)
        checks.append(_check("supply", int(state.get("gap") or 0) == 0, "warn",
                             state.get("note", ""),
                             "按 briefs.md 生产内容并 mkt grow stage 入队"))
    except Exception as exc:  # noqa: BLE001 供给检查失败不阻断体检
        checks.append(_check("supply", True, "info", f"供给检查跳过：{exc}"))

    account_id = str((run.get("guards") or {}).get("account_id") or "")
    if not account_id:
        checks.append(_check("account", True, "info", "未绑定账号（不校验健康度）"))
    else:
        try:
            from .accounts import AccountRegistry
            account = AccountRegistry().get(account_id)
            status = str(account.get("status") or "")
            score = float((account.get("health") or {}).get("score") or 0)
            ok = status == "eligible" and score >= 60
            checks.append(_check("account", ok, "blocker" if not ok else "info",
                                 f"{account_id}: status={status}, health={score}",
                                 "先把账号修到 eligible（mkt account 系列命令）"))
        except Exception as exc:  # noqa: BLE001 读不到账号只提醒
            checks.append(_check("account", False, "warn",
                                 f"读不到账号 {account_id}：{exc}",
                                 "mkt account add 登记或去掉 guards.account_id"))

    ok = not any(not check["ok"] and check["level"] == "blocker" for check in checks)
    return {"ok": ok, "checks": checks}


def _safe_json(path: Path) -> bool:
    try:
        json.loads(path.read_text(encoding="utf-8"))
        return True
    except (json.JSONDecodeError, OSError):
        return False


def launchd_plist(*, label: str, python: str, mkt_path: str, run_id: str,
                  interval_minutes: int, log_path: str, cwd: str, workspace: str = "") -> str:
    """生成合法 launchd plist 文本。"""
    payload = {
        "Label": str(label),
        "ProgramArguments": [str(python), str(mkt_path), "grow", "tick",
                             str(run_id), "--deliver"],
        "WorkingDirectory": str(cwd),
        "StartInterval": int(interval_minutes or INTERVAL_DEFAULT) * 60,
        "RunAtLoad": True,
        "StandardOutPath": str(log_path),
        "StandardErrorPath": str(log_path),
    }
    if workspace:
        payload["EnvironmentVariables"] = {"MKT_HOME": str(Path(workspace).resolve())}
    return plistlib.dumps(payload).decode("utf-8")


def install_launchd(*, out_path, plist: str, load: bool = False) -> Dict[str, Any]:
    """写 plist；load=True 才执行 launchctl load（不会自动开始跑测试）。"""
    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(plist, encoding="utf-8")
    command = f"launchctl load -w {path}"
    result: Dict[str, Any] = {"path": str(path), "command": command,
                              "loaded": False}
    if load:
        proc = subprocess.run(["launchctl", "load", "-w", str(path)],
                              capture_output=True, text=True)
        result["loaded"] = proc.returncode == 0
        result["returncode"] = proc.returncode
        if proc.returncode != 0:
            result["error"] = (proc.stderr or proc.stdout or "").strip()[:300]
    return result


def crontab_line(*, python: str, mkt_path: str, run_id: str,
                 interval_minutes: int, log_path: str, cwd: str = "", workspace: str = "") -> str:
    """生成一行可复制的 crontab（每 N 分钟；非整除时退回整点）。"""
    interval = int(interval_minutes or INTERVAL_DEFAULT)
    if interval in (1, 2, 3, 4, 5, 6, 10, 12, 15, 20, 30):
        schedule = f"*/{interval} * * * *"
    else:
        schedule = "0 * * * *"
    quote = lambda value: shlex.quote(str(value)).replace("%", r"\%")
    prefix = f"cd {quote(cwd)} && " if cwd else ""
    if workspace:
        prefix += f"MKT_HOME={quote(Path(workspace).resolve())} "
    return (f"{schedule} {prefix}{quote(python)} {quote(mkt_path)} grow tick {quote(run_id)} "
            f">> {quote(log_path)} 2>&1")
