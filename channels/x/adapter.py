#!/usr/bin/env python3
"""X 渠道适配器 —— vendor x-ops-automation（PROVEN），架构文档 §6.1。

vendor 脚本原样执行（自带节流/回读校验/防错人/防重发），本层做三件事：
  1. 统一 mkt CLI 语义（写操作默认 dry-run，--go 才实跑）
  2. campaign 维度记账
  3. 把 vendor 的结果 JSON / DM 日志回写全局账本（跨渠道幂等）

vendor 脚本自身的 dry-run 语义不统一（publish/reply/xdm 默认实跑、--dry-run 干跑），
本层在未给 --go 时一律给 vendor 追加 --dry-run，保证 mkt 层语义一致。
"""
import hashlib
import json
import os
import subprocess
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path

VENDOR = Path(__file__).resolve().parent / "vendor"

# action -> (vendor 脚本, 是否写操作, 实跑时是否需要 --go 之外的参数)
ACTIONS = {
    "publish": ("publish_x.py", True),      # 线程发布：--file <queue.json> --thread 可选
    "reply": ("reply_x.py", True),
    "message": ("xdm.py", True),            # DM 队列（读 vendor 默认 outreach 目录）
    "engage": (None, True),                 # follow/like 按子参数分派
    "search": ("search_x.py", False),       # 只读
    "inbox": ("scan_inbox.py", False),      # 只读（Gmail，同 CDP 会话）
}


def _latest_result(patterns, before=None):
    """找 vendor logs/ 里 before 时间点之后生成的最新结果文件。"""
    logs = VENDOR / "logs"
    if not logs.exists():
        return None
    cands = [p for pat in patterns for p in logs.glob(pat)]
    if before is not None:
        cands = [p for p in cands if p.stat().st_mtime >= before - 1]
    return max(cands, key=lambda p: p.stat().st_mtime) if cands else None


def _vendor_targets(action, data):
    """从 vendor 结果提取 ledger target。"""
    out = []
    if action == "message":
        for d in data if isinstance(data, list) else [data]:
            if isinstance(d, dict) and d.get("handle"):
                out.append(("@" + d["handle"], d.get("result", "?")))
    elif action == "publish":
        recs = data.get("records", []) if isinstance(data, dict) else []
        for r in recs:
            idx = r.get("index", "?")
            res = "committed" if r.get("posted") else ("dry-run" if r.get("dry_run") else "failed")
            out.append((f"tweet:{idx}", res))
    return out


def _log_to_ledger(action, campaign, ledger, before_lines, log=None, approval_mode=None,
                   op_ref=None, before_time=None):
    """把 vendor 运行结果回写全局账本（审计 + 跨渠道幂等视图）。

    op_ref：本次执行意图的稳定引用，每条记录 ref=op_ref::target，
    使 indeterminate 记录可被 ledger.resolve 人工裁决（G-2.1）。
    before_time：只认本次运行开始之后生成的 vendor 结果文件——历史成功
    日志不得被误认成本次结果（G-2 回执时间隔离）。
    返回 (wrote, recorded_targets, has_unknown)。
    """
    wrote = 0
    recorded_targets = set()
    has_unknown = False
    detail = "adhoc=true" if approval_mode == "adhoc" else ""

    def _ref(target):
        return "%s::%s" % (op_ref, target) if op_ref else ""

    if action == "message":
        # xdm 追加日志：xdm-log.jsonl（outreach 目录）；只记本次运行新增的行
        logf = VENDOR / "xdm-log.jsonl"
        real = Path.home() / ".x-ops-automation" / "outreach" / "xdm-log.jsonl"
        logf = logf if logf.exists() else (real if real.exists() else None)
        if logf:
            lines = logf.read_text(encoding="utf-8").splitlines()
            for line in lines[before_lines.get("xdm", 0):]:
                try:
                    d = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if d.get("handle"):
                    result = {"dry-run-draft": "dry-run", "sent": "committed"}.get(
                        d.get("result"), d.get("result", "?"))
                    target = "@" + d["handle"]
                    ledger.append(channel="x", transport="cdp", action="message",
                                  campaign=campaign, target=target,
                                  result=result, ref=_ref(target), detail=detail)
                    recorded_targets.add(target)
                    has_unknown = has_unknown or result == "indeterminate"
                    wrote += 1
    else:
        pat = {"publish": "publish-*.json", "reply": "reply-*.json",
               "engage": ["follow-*.json", "like-*.json"]}.get(action)
        pats = pat if isinstance(pat, list) else [pat] if pat else []
        f = _latest_result(pats, before=before_time)
        if f:
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                data = {}
            if action == "publish":
                for target, res in _vendor_targets(action, data):
                    ledger.append(channel="x", transport="cdp", action=action,
                                  campaign=campaign, target=target, result=res,
                                  ref=_ref(target), evidence=str(f), detail=detail)
                    recorded_targets.add(target)
                    has_unknown = has_unknown or res == "indeterminate"
                    wrote += 1
            elif action == "reply":
                rec = data.get("record") if isinstance(data, dict) else {}
                if isinstance(rec, dict):
                    if rec.get("posted"):
                        result = "committed"
                    elif rec.get("dry_run"):
                        result = "dry-run"
                    elif rec.get("submitted"):
                        result = "indeterminate"
                    else:
                        result = "failed"
                    target = rec.get("url") or "?"
                    ledger.append(channel="x", transport="cdp", action=action,
                                  campaign=campaign, target=target,
                                  result=result, ref=_ref(target),
                                  evidence=str(f), detail=detail)
                    recorded_targets.add(target)
                    has_unknown = has_unknown or result == "indeterminate"
                    wrote += 1
            elif action == "engage":
                recs = data.get("records", data if isinstance(data, list) else [])
                for r in recs if isinstance(recs, list) else []:
                    handle = str(r.get("handle") or "").strip()
                    if handle:
                        target = handle if handle.startswith("@") else "@" + handle
                    else:
                        target = str(r.get("url") or "?")
                    ok = r.get("followed", r.get("liked", False))
                    ledger.append(channel="x", transport="cdp", action="engage",
                                  campaign=campaign, target=target,
                                  result="committed" if ok else "failed",
                                  ref=_ref(target), detail=detail)
                    recorded_targets.add(target)
                    wrote += 1
    if log and wrote:
        log(f"已回写全局账本 {wrote} 条")
    return wrote, recorded_targets, has_unknown


def _xdm_log_lines():
    logf = VENDOR / "xdm-log.jsonl"
    real = Path.home() / ".x-ops-automation" / "outreach" / "xdm-log.jsonl"
    logf = logf if logf.exists() else (real if real.exists() else None)
    if not logf:
        return 0
    return len(logf.read_text(encoding="utf-8").splitlines())


# 命令参数里可识别的目标形态；message/publish 的目标在 vendor 侧队列文件里，
# 这里看不到，属于第二层 Reserve（执行事务）的覆盖范围。
_TARGET_PREFIXES = ("@", "http://", "https://", "tweet:", "item:")


def _preflight_targets(extra_args):
    return [str(a).strip() for a in extra_args
            if str(a).strip().startswith(_TARGET_PREFIXES)]


def _preflight_ledger(ledger, extra_args):
    """幂等 + 不重试名单前置（与 WritePipeline 的 Gate 同一账本答案）。

    X 不走统一写管道，但这不妨碍发送前先问账本同一个问题：发过没有。
    already 命中（任何 campaign）或目标在 no_retry 名单（indeterminate/
    no-dm/bounced/suppressed）时拒绝实跑——vendor 内部防重发不是本层依据。
    """
    targets = _preflight_targets(extra_args)
    for target in targets:
        if ledger.already(channel="x", target=target):
            raise SystemExit(
                f"幂等拦截：x 已成功发送给 {target}（任何 campaign）；拒绝重复发送")
    no_retry = {str(t).lstrip("@") for t in ledger.no_retry("x") if t}
    for target in targets:
        if target.lstrip("@") in no_retry:
            raise SystemExit(
                f"不重试名单拦截（indeterminate/no-dm/bounced/suppressed）: {target}")


def _claim_key(action, campaign, targets):
    norm = "|".join([(campaign or "").strip().lower(), action]
                    + sorted(str(t).lstrip("@").lower() for t in targets))
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()[:32]


def _acquire_claim(data_root, action, campaign, targets, op_ref):
    """最小 Reserve（G-2.3，复核 2026-11-04）：对规范化执行意图做原子认领。

    两个 Worker 先查账本都见"未发送"再各自执行同一动作的竞态窗口由此关闭：
    O_CREAT|O_EXCL 原子创建认领文件，只有一个进程能成功，成功者才允许触碰
    vendor。认领在结果全部可判定并落账后自动释放；出现任何未知状态（无回执/
    indeterminate/部分目标无记录）或进程崩溃时保留——同一意图在人工核查前
    不再执行（确认无外部动作可删 claim 文件；有则先裁决账本再清理）。
    publish/message 的目标在 vendor 队列里、本层不可见，其意图认领由第二层
    执行事务补齐；本层覆盖命令行可见目标的全部写动作。
    """
    path = Path(data_root) / "state" / "x-claims" / (
        _claim_key(action, campaign, targets) + ".json")
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    except FileExistsError:
        raise SystemExit(
            f"执行认领失败：同一意图已被认领，或上次执行仍处于未知状态"
            f"（{path}）。并发/重复执行被拒绝；人工核实后再处理该 claim 文件。")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(json.dumps({
                "op_ref": op_ref, "action": action, "campaign": campaign or "",
                "targets": sorted(str(t) for t in targets),
                "claimed_at": datetime.now().isoformat(timespec="seconds"),
            }, ensure_ascii=False))
    except OSError:
        path.unlink(missing_ok=True)
        raise
    return path


def run(action, extra_args, *, go=False, adhoc=False, campaign=None, ledger=None, log=print,
        data_dir=None):
    """执行 vendor 脚本并把结果记账。go=False 时写操作自动降级为 dry-run。

    go=True 的人审门禁与其他渠道同规：必须绑定已人审 plan（data/plans/），
    无 plan 管辖需显式 adhoc=True（CLI --adhoc 人工通道）。"""
    if action not in ACTIONS:
        raise SystemExit(f"X 渠道不支持动作: {action}（支持: {sorted(ACTIONS)}）")
    script, is_write = ACTIONS[action]

    if action == "engage":
        # engage 按第一个参数分派 follow/like
        sub = extra_args[0] if extra_args else ""
        script = {"follow": "follow_x.py", "like": "like_x.py"}.get(sub)
        if not script:
            raise SystemExit("engage 需要子动作: follow <@a @b> | like <url...>")
        extra_args = extra_args[1:]

    # 全局暂停闸（core.safety）：写操作一律拒跑；人审门禁（core.planlint）：实跑前强制
    mode = None
    claim_path = None
    op_ref = "x:%s:%s:%s" % ((campaign or "adhoc"), action, uuid.uuid4().hex[:12])
    data_root = Path(data_dir) if data_dir else VENDOR.parents[2] / "data"
    if is_write:
        from core.planlint import enforce_approval
        from core.safety import pause_file
        if pause_file(data_root).exists():
            raise SystemExit("全局暂停中（mkt pause --off 恢复）——X 写操作拒跑")
        from core.gates import load_suppressions
        suppressed = load_suppressions(data_root)
        for raw in extra_args:
            value = str(raw).strip()
            if value in suppressed or value.lstrip("@") in suppressed:
                raise SystemExit(f"目标在抑制名单（DNC/bounced/suppressed）: {value}")
        if go:
            mode = enforce_approval(data_root, campaign or "", adhoc=adhoc)
            if mode == "adhoc":
                log("[x] adhoc 放行（无 plan 管辖，人工通道留痕）")
            # vendor 进程自带节流；内核日预算再守一层。X vendor 的一次 action
            # 可能包含多条记录，这里至少保证预算耗尽时不会启动新动作。
            from core.throttle import Throttle, ThrottleError
            try:
                Throttle(data_root / "state").reserve("x", action, 1)
            except ThrottleError as exc:
                raise SystemExit(f"日预算门禁: {exc}")
            if ledger is not None:
                _preflight_ledger(ledger, extra_args)
                # Reserve：账本答案是"没发过"之后、触碰 vendor 之前，原子认领
                # 同一意图，关闭 check-then-send 竞态窗口（G-2.3）。
                claim_path = _acquire_claim(data_root, action, campaign or "",
                                            _preflight_targets(extra_args), op_ref)

    before_lines = {"xdm": _xdm_log_lines()}
    run_started = time.time()
    cmd = [sys.executable, str(VENDOR / script)] + [str(a) for a in extra_args]
    if is_write and not go:
        cmd.append("--dry-run")
        log(f"[x/{action}] dry-run（加 --go 实跑）")
    if script == "x_search.sh" and go:
        cmd.append("--go")

    proc = subprocess.run(cmd, cwd=str(VENDOR))
    recorded_targets = set()
    has_unknown = False
    if ledger is not None and proc.returncode == 0:
        try:
            _, recorded_targets, has_unknown = _log_to_ledger(
                action, campaign or "", ledger, before_lines, log=log,
                approval_mode=mode, op_ref=op_ref, before_time=run_started)
        except Exception as exc:  # 记账失败不掩盖 vendor 结果
            log(f"账本回写失败（不影响本次执行）: {exc}", "WARN")
            has_unknown = True
    if ledger is not None and go:
        # 部分未知（复核 2026-11-04）：可见目标中没有任何记录的，逐目标补
        # indeterminate——一条成功不得掩盖其余未知。publish/message 的目标在
        # vendor 队列里不可见，由整体未知行兜底。
        visible = {str(t) for t in _preflight_targets(extra_args)}
        norm = lambda t: str(t).lstrip("@").lower()  # noqa: E731
        recorded_norm = {norm(t) for t in recorded_targets}
        missing = sorted(t for t in visible if norm(t) not in recorded_norm) \
            if action in ("engage", "reply") else []
        for target in missing:
            ledger.append(channel="x", transport="cdp", action=action,
                          campaign=campaign or "", target=target,
                          result="indeterminate", ref="%s::%s" % (op_ref, target),
                          detail="go 实跑但该目标无 vendor 结果记录（部分未知）；"
                                 "未知状态禁止自动重发，人工核实后 ledger.resolve")
            has_unknown = True
        if not recorded_targets and not missing:
            # 整体未知：外部动作已发生（或可能已发生）而账本无任何记录 =
            # 下次运行会当作"没发过"再发一次。留下 indeterminate：未知状态
            # 禁止自动重发，人工核实后 ledger.resolve（G-2.1/G-2.2）。
            ledger.append(channel="x", transport="cdp", action=action,
                          campaign=campaign or "",
                          target=sorted(visible)[0] if len(visible) == 1 else "?",
                          result="indeterminate", ref=op_ref,
                          detail="go 实跑但无 vendor 结果可回写（rc=%d，目标不可见"
                                 "或无结果文件）；未知状态禁止自动重发，人工核实"
                                 "后 ledger.resolve" % proc.returncode)
            has_unknown = True
        if log and (missing or not recorded_targets):
            log("[x] go 实跑存在未知状态，已记 indeterminate（禁止自动重发）", "WARN")
        # Reserve 释放：仅当本次全部可判定且已落账；任何未知/崩溃保留认领。
        if claim_path is not None and not has_unknown and recorded_targets \
                and proc.returncode == 0:
            claim_path.unlink(missing_ok=True)
    return proc.returncode
