#!/usr/bin/env python3
"""账号身份证明（G-01 / G-01b）—— 登录态 ≠ 授权账号身份，真实发送前必须绑定。

session_gate.check_login 只证明「调试浏览器里有一个已登录的界面」，不证明
登录的就是授权账号；execution_policy.allows 校验的 platform_user_id 由调用
方传入，是审批参数而非观测证据。本模块在真实发送路径上补第二道门禁：

- AccountIdentityProof：一次实际平台身份观测（稳定 user_id、handle、观测
  时间、浏览器上下文绑定、来源引用）；
- enforce_identity()：把 routine policy 中该账号的 account_identities 与
  当前观测身份精确比对；身份未知 / 与授权不一致 / 观测过期 / 跨渠道
  一律 fail closed（IdentityBlocked），绝不带病发送；
- 旧授权收敛（G-01b，PRD AC-H）：policy 仍然可读，但没声明身份绑定的旧授权
  在真实外部写前直接拒绝（C-01 `AUTH_REQUIRED`），并给出迁移命令；dry-run
  只报告阻塞项，不改变库内演示/离线路径。
- observe()/read_proof_from_browser()：默认 CDP 读取器在
  `core.cdp_identity`（默认关闭、只连回环、只认稳定数字 ID）；离线测试用
  set_reader() 注入探测器。

纪律：
- 引擎不自我授权；proof 只来自探测，绝不接受 Agent / queue 条目 / MCP
  参数自报的 user_id 充当身份证据。
- 观测即审计：每次门禁决策（allowed/blocked）追加到
  data/identity/events.jsonl；每次 CDP 探测（成功或归类失败）追加到
  data/identity/probes.jsonl，绝不把 cookie/token 写进台账。
- 迁移是人的决定：`migrate_identity` 必须署名（by），默认 dry-run。
"""
import json
import logging
import os
import math
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from . import execution_policy

LOGGER = logging.getLogger(__name__)

# 身份证明的寿命与登录探测同量级：过期必须重探，不得跨调用沿用。
PROOF_MAX_AGE_SECONDS = 300.0

# 首批只对 x 的真实写路径强制（email 的 sender 身份走 SMTP 配置校验，
# 独立于本门禁，见 CHANGELOG 的 G-01 边界说明）。
IDENTITY_CHANNELS = frozenset({"x"})

# C-01 注册码：机器语义，不是自然语言同义词（core/grow_runbook.py 注册表）。
AUTH_REQUIRED = "AUTH_REQUIRED"
POLICY_DENIED = "POLICY_DENIED"

_REASON_C01 = {
    "authorization_not_identity_bound": AUTH_REQUIRED,
    "authorization_missing_identity": AUTH_REQUIRED,
    "identity_unknown": AUTH_REQUIRED,
    "identity_stale": AUTH_REQUIRED,
    "identity_channel_mismatch": AUTH_REQUIRED,
    "identity_mismatch": POLICY_DENIED,
}


class IdentityBlocked(RuntimeError):
    """身份门禁拒绝：reason_code + C-01 码供调用方/账本分类；被拒不得重试真实发送。"""

    def __init__(self, reason_code: str, detail: str = "", *,
                 c01_code: str = AUTH_REQUIRED):
        self.reason_code = reason_code
        self.detail = detail
        self.c01_code = c01_code
        text = f"{reason_code} ({c01_code})"
        super().__init__(f"{text}: {detail}" if detail else text)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class AccountIdentityProof:
    """一次实际平台身份观测。user_id 必须是平台稳定 ID，handle 仅辅助展示。"""

    def __init__(self, *, channel: str, user_id: str, handle: str = "",
                 observed_at: str = "", browser_context: str = "",
                 source_ref: str = "", status: str = "observed"):
        if not str(user_id or "").strip():
            raise ValueError(
                "AccountIdentityProof 需要 user_id；身份未知请用 None proof 表达")
        self.channel = str(channel or "").strip().lower()
        self.user_id = str(user_id).strip()
        self.handle = str(handle or "").strip()
        self.observed_at = observed_at or _now_iso()
        self.browser_context = str(browser_context or "").strip()
        self.source_ref = str(source_ref or "").strip()
        self.status = status

    def age_seconds(self, *, now: Optional[float] = None) -> float:
        """距观测的秒数；观测时间解析失败视为无限旧（必须重探）。"""
        clock = time.time() if now is None else now
        try:
            ts = datetime.fromisoformat(self.observed_at.replace("Z", "+00:00"))
        except ValueError:
            return float("inf")
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return max(0.0, clock - ts.timestamp())

    def to_dict(self) -> Dict[str, Any]:
        return {"channel": self.channel, "user_id": self.user_id,
                "handle": self.handle, "observed_at": self.observed_at,
                "browser_context": self.browser_context,
                "source_ref": self.source_ref, "status": self.status}

    @classmethod
    def from_dict(cls, data) -> Optional["AccountIdentityProof"]:
        if not isinstance(data, dict) or not str(data.get("user_id") or "").strip():
            return None
        try:
            return cls(channel=str(data.get("channel") or ""),
                       user_id=str(data.get("user_id") or ""),
                       handle=str(data.get("handle") or ""),
                       observed_at=str(data.get("observed_at") or ""),
                       browser_context=str(data.get("browser_context") or ""),
                       source_ref=str(data.get("source_ref") or ""),
                       status=str(data.get("status") or "observed"))
        except ValueError:
            return None


# ---------- 状态与审计（append-only） ----------

def _identity_dir(data_dir) -> Path:
    return Path(data_dir) / "identity"


def _events_path(data_dir) -> Path:
    return _identity_dir(data_dir) / "events.jsonl"


def _probes_path(data_dir) -> Path:
    return _identity_dir(data_dir) / "probes.jsonl"


def _migrations_path(data_dir) -> Path:
    return _identity_dir(data_dir) / "migrations.jsonl"


def _state_path(data_dir, channel: str) -> Path:
    return _identity_dir(data_dir) / f"{str(channel).strip().lower()}.json"


def load_last_proof(data_dir, channel: str) -> Optional[AccountIdentityProof]:
    """最近一次被门禁接受的身份观测（跨运行审计对照用）。"""
    try:
        return AccountIdentityProof.from_dict(
            json.loads(_state_path(data_dir, channel).read_text(encoding="utf-8")))
    except (OSError, ValueError, json.JSONDecodeError):
        return None


def _append_jsonl(path: Path, payload) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except OSError:
        pass  # 审计失败不阻断门禁本身；真实环境的写入失败由 doctor 另行暴露


def _record_event(data_dir, *, decision: str, channel: str, account: str = "",
                  reason: str = "", proof: Optional[AccountIdentityProof] = None,
                  now_iso: str = "") -> None:
    event = {"at": now_iso or _now_iso(), "decision": decision,
             "channel": str(channel or "").strip().lower(),
             "account": str(account or ""), "reason": reason or None,
             "proof": proof.to_dict() if proof is not None else None}
    _append_jsonl(_events_path(data_dir), event)
    if proof is not None and decision == "allowed":
        state = _state_path(data_dir, channel)
        try:
            state.parent.mkdir(parents=True, exist_ok=True)
            state.write_text(json.dumps(proof.to_dict(), ensure_ascii=False,
                                        indent=2) + "\n", encoding="utf-8")
        except OSError:
            pass


# ---------- 探测（可注入；默认真实 CDP，默认关闭 → fail closed） ----------

_READER: Optional[Callable[[str], Optional[AccountIdentityProof]]] = None
_LAST_PROBE: Dict[str, Any] = {}


def set_reader(fn: Optional[Callable[[str], Optional[AccountIdentityProof]]]) -> None:
    """注入身份探测器（离线测试/替代实现）；None 恢复默认。"""
    global _READER
    _READER = fn


def reset_reader() -> None:
    set_reader(None)


def last_probe() -> Dict[str, Any]:
    """最近一次探测的结构化结果（分类留痕，只读）。"""
    return dict(_LAST_PROBE)


def _remember_probe(channel: str, result) -> None:
    global _LAST_PROBE
    _LAST_PROBE = {"at": _now_iso(), "channel": channel,
                   "reason": result.reason, "detail": result.detail,
                   "endpoint": result.endpoint, "target_id": result.target_id,
                   "source": result.source,
                   "ok": result.proof is not None}


def _record_probe(data_dir, channel: str, result) -> None:
    _append_jsonl(_probes_path(data_dir), {
        "at": _LAST_PROBE.get("at") or _now_iso(), "channel": channel,
        "ok": result.proof is not None, "reason": result.reason,
        "detail": result.detail, "endpoint": result.endpoint,
        "target_id": result.target_id, "source": result.source,
        "proof": result.proof.to_dict() if result.proof is not None else None,
    })


def read_proof_from_browser(channel: str, *, data_dir=None,
                            env=None) -> Optional[AccountIdentityProof]:
    """默认 CDP 身份读取：只认稳定数字 ID；关闭/不可达/只有 handle → None。

    失败分类留给 observe()/enforce_identity() 与 data/identity/probes.jsonl；
    日志只写分类与端点，不写 cookie/token。
    """
    from . import cdp_identity
    result = cdp_identity.probe(str(channel or "").strip().lower(), env=env)
    _remember_probe(str(channel or "").strip().lower(), result)
    if data_dir is not None:
        _record_probe(data_dir, str(channel or "").strip().lower(), result)
    if result.proof is not None:
        return result.proof
    LOGGER.warning("CDP 身份探测失败（%s）：%s", result.reason, result.detail)
    return None


def observe(channel: str, *, data_dir=None, env=None) -> Optional[AccountIdentityProof]:
    """读取当前浏览器实际身份；探测器缺失/异常一律按身份未知处理。"""
    key = str(channel or "").strip().lower()
    reader = _READER
    try:
        if reader is not None:
            proof = reader(key)
            _remember_probe(key, _InjectedProbe(proof))
            return proof
        return read_proof_from_browser(key, data_dir=data_dir, env=env)
    except Exception as exc:  # noqa: BLE001 探测失败=身份未知，不冒充、不放行
        global _LAST_PROBE
        _LAST_PROBE = {"at": _now_iso(), "channel": key,
                       "reason": "reader_exception", "detail": str(exc)[:200],
                       "endpoint": "", "target_id": "", "source": "",
                       "ok": False}
        LOGGER.warning("身份探测器异常，按身份未知处理：%s", str(exc)[:200])
        return None


class _InjectedProbe:
    """把注入 reader 的结果包装成 ProbeResult 形状，只用于留痕。"""

    def __init__(self, proof):
        self.reason = "ok" if proof is not None else "reader_returned_none"
        self.detail = "injected reader"
        self.endpoint = "injected"
        self.target_id = ""
        self.source = proof.source_ref if proof is not None else ""
        self.proof = proof


# ---------- 门禁 ----------

def load_policy(data_dir):
    """安全读取 routine policy；读取失败按「无 policy」处理（allows 另行拦截）。"""
    try:
        return execution_policy.get_policy(data_dir)
    except (OSError, ValueError):
        return None


def policy_identity_records(policy, account: str = ""):
    return ((policy or {}).get("account_identities") or {}).get(account) or []


def policy_state(policy, account: str = "") -> Dict[str, Any]:
    """该 policy 对该账号的身份绑定状态（决定门禁是否强制/如何拒绝）。"""
    account = str(account or "")
    if not isinstance(policy, dict):
        return {"mode": "absent", "active": False, "account": account,
                "require_identity": False, "allow_accounts": [],
                "records": [], "policy": None}
    if not execution_policy.is_active(policy):
        return {"mode": "invalid", "active": False, "account": account,
                "require_identity": False, "allow_accounts": [],
                "records": [], "policy": policy}
    allow_accounts = [str(item) for item in (policy.get("allow_accounts") or [])
                      if isinstance(item, str)]
    records = policy_identity_records(policy, account)
    bound = bool(policy.get("require_identity")) or bool(allow_accounts) or bool(records)
    observed_mode = str(policy.get("identity_mode") or "") == "cdp_observed"
    mode = "bound" if bound else ("observed" if observed_mode else "unbound")
    return {"mode": mode, "active": True,
            "account": account,
            "require_identity": bool(policy.get("require_identity")),
            "allow_accounts": allow_accounts, "records": list(records),
            "observe_identity": observed_mode,
            "policy": policy}


def identity_bound(policy, account: str = "") -> bool:
    """该 policy 是否声明了账号身份绑定（bound 但缺记录时仍需重新授权）。"""
    return policy_state(policy, account)["mode"] == "bound"


def _workspace_flag(data_dir) -> str:
    try:
        return "--workspace %s" % Path(data_dir)
    except (TypeError, ValueError):
        return ""


def migration_commands(data_dir, account: str = "") -> List[str]:
    """给阻塞项用：先预览，再带署名重新批准并绑定身份。"""
    base = "mkt policy migrate-identity"
    flag = _workspace_flag(data_dir)
    if flag:
        base += " " + flag
    binding = ("--identity %s=x:<平台稳定数字ID>" % account if account
               else "--identity x:<平台稳定数字ID>")
    return [base, "%s --apply --by <你的名字> %s" % (base, binding)]


def _block_message(summary: str, data_dir, account: str = "") -> str:
    lines = [summary, "下一步（先预览缺什么，再重新批准并绑定身份）："]
    lines.extend("  " + command for command in migration_commands(data_dir, account))
    lines.append("  稳定数字 ID 取平台自己的 rest_id/user id；读取开关：export MKT_CDP_IDENTITY=1")
    return "\n".join(lines)


def identity_status(data_dir=None, *, env=None) -> Dict[str, Any]:
    """只读：身份读取器是否开启、最近一次探测结果与下一步。

    给 `mkt policy identity` 与人工排查用；不发送、不写任何东西。
    """
    from . import cdp_identity
    env_map = os.environ if env is None else env
    enabled = bool(cdp_identity.enabled(env_map))
    probe = last_probe()
    if not enabled:
        probe = {"reason": cdp_identity.DISABLED,
                 "detail": "MKT_CDP_IDENTITY 未开启：身份未知，真实发送须 fail closed",
                 "endpoint": ""}
    observed = None
    if enabled:
        proof = observe("x", data_dir=data_dir, env=env_map)
        observed = ({"channel": proof.channel, "user_id": proof.user_id,
                     "handle": proof.handle, "source_ref": proof.source_ref}
                    if proof is not None else None)
        probe = last_probe()
    hint = ("默认 X 任务使用 browser-attach；仅显式 legacy CDP 需要设置 MKT_CDP_IDENTITY=1 "
            "并打开 X 页面"
            if not enabled else
            "默认 X 任务先运行 growth live-preflight --x，使用 browser-attach 恢复；"
            "legacy verify_credentials 403 不能单独证明未登录，只能按身份未知拒绝并继续分类")
    return {"enabled": enabled, "observed": observed,
            "probe": {"reason": (probe or {}).get("reason"),
                      "detail": (probe or {}).get("detail"),
                      "endpoint": (probe or {}).get("endpoint")},
            "next_required_action": hint, "hint": "MKT_CDP_IDENTITY"}


def identity_blockers(data_dir, account: str = "", *, policy=None,
                      policy_loaded: bool = False) -> List[Dict[str, Any]]:
    """dry-run / doctor 用：列出会让真实外部写被拒的授权阻塞项。"""
    if policy is None and not policy_loaded:
        policy = load_policy(data_dir)
    state = policy_state(policy, account)
    if state["mode"] == "unbound":
        reason = "authorization_not_identity_bound"
        detail = ("%s：旧授权没有声明授权的是哪个具体平台账号（缺 require_identity / "
                  "allow_accounts / account_identities）：真实发送按 AC-H 拒绝" % reason)
    elif state["mode"] == "bound" and not state["records"]:
        reason = "authorization_missing_identity"
        detail = ("%s：授权只含账号名，没有该账号的平台身份记录：请用 identities "
                  "重新授权" % reason)
    else:
        return []
    commands = migration_commands(data_dir, account)
    return [{"code": AUTH_REQUIRED, "reason": reason, "detail": detail,
             "account": str(account or ""), "next": commands[1],
             "commands": commands}]


def enforce_identity(data_dir, channel, account, proof, *, policy=None,
                     max_age_seconds: float = PROOF_MAX_AGE_SECONDS,
                     now: Optional[float] = None) -> Dict[str, Any]:
    """真实发送前的身份门禁：通过返回证据 dict；拒绝抛 IdentityBlocked。

    - 无 policy / policy 无效：不由本门禁裁决（审批门禁另行 fail closed）；
    - 旧授权（有效但未声明身份绑定）：AUTH_REQUIRED，要求重新批准并绑定身份；
    - 绑定身份：必须观测到与授权记录精确一致的稳定 user_id。
    """
    channel = str(channel or "").strip().lower()
    account = str(account or "")
    if channel not in IDENTITY_CHANNELS:
        return {"enforced": False, "reason": "channel_not_covered"}
    if policy is None:
        policy = load_policy(data_dir)
    state = policy_state(policy, account)
    if state["mode"] in ("absent", "invalid"):
        return {"enforced": False, "reason": "policy_not_identity_bound"}

    def _blocked(reason: str, detail: str) -> IdentityBlocked:
        c01 = _REASON_C01.get(reason, AUTH_REQUIRED)
        _record_event(data_dir, decision="blocked", channel=channel,
                      account=account, reason=reason, proof=proof)
        return IdentityBlocked(reason, detail, c01_code=c01)

    if state["mode"] == "observed":
        # CDP-observed mode: any logged-in account is allowed, but the operation
        # must carry the observed account. No match against a pre-bound identity.
        if proof is None:
            raise _blocked("identity_unknown",
                           "CDP 观测模式无法读取当前浏览器账号：默认任务先用 browser-attach 受管页预检；"
                           "legacy verify_credentials 403 不能单独证明未登录，需按页面和响应继续分类")
        if proof.channel != channel:
            raise _blocked("identity_channel_mismatch",
                           f"proof 渠道 {proof.channel!r} 与执行渠道 {channel!r} 不符")
        if proof.age_seconds(now=now) > max_age_seconds:
            raise _blocked("identity_stale",
                           f"身份观测已过期（>{int(max_age_seconds)}s）：必须重新探测")
        _record_event(data_dir, decision="allowed", channel=channel,
                      account=account, proof=proof)
        return {"enforced": True, "mode": "cdp_observed",
                "authorized_account": account,
                "observed_platform_identity": proof.user_id,
                "identity_proof_ref": proof.source_ref
                or f"{proof.channel}:{proof.observed_at}",
                "handle": proof.handle,
                "observed_at": proof.observed_at,
                "browser_context": proof.browser_context}
    if state["mode"] == "unbound":
        raise _blocked("authorization_not_identity_bound", _block_message(
            "旧授权没有绑定实际平台账号身份：真实发送已拒绝", data_dir, account))
    records = state["records"]
    if not records:
        raise _blocked("authorization_missing_identity", _block_message(
            "授权只含账号名、没有平台身份记录：真实发送已拒绝", data_dir, account))
    if proof is None:
        probe = last_probe()
        suffix = ""
        if probe.get("reason"):
            suffix = "（探测分类：%s；%s）" % (
                probe.get("reason"), str(probe.get("detail") or "")[:200])
        raise _blocked("identity_unknown",
                       "无法观测实际平台账号身份（探测器关闭/失败/未接线）：拒绝真实发送"
                       + suffix)
    if proof.channel != channel:
        raise _blocked("identity_channel_mismatch",
                       f"proof 渠道 {proof.channel!r} 与发送渠道 {channel!r} 不符")
    if proof.age_seconds(now=now) > max_age_seconds:
        raise _blocked("identity_stale",
                       f"身份观测已过期（>{int(max_age_seconds)}s）：必须重新探测")
    if not any(str(r.get("platform") or "").strip().lower() == proof.channel
               and str(r.get("user_id") or "").strip() == proof.user_id
               for r in records if isinstance(r, dict)):
        raise _blocked("identity_mismatch",
                       f"浏览器实际账号 {proof.user_id!r} 与授权身份不符：拒绝发送")
    _record_event(data_dir, decision="allowed", channel=channel,
                  account=account, proof=proof)
    return {"enforced": True, "authorized_account": account,
            "observed_platform_identity": proof.user_id,
            "identity_proof_ref": proof.source_ref
            or f"{proof.channel}:{proof.observed_at}",
            "handle": proof.handle,
            "observed_at": proof.observed_at,
            "browser_context": proof.browser_context}


# ---------- 旧授权迁移（G-01b / PRD AC-H） ----------

def _queue_bindings(data_dir, accounts, allow_accounts) -> List[Dict[str, Any]]:
    """扫描队列目录，列出在授权范围内的 x 队列（迁移报告用，只读）。"""
    out = []
    queues_dir = Path(data_dir) / "queues"
    if not queues_dir.is_dir():
        return out
    wanted = set(accounts)
    for path in sorted(queues_dir.glob("*.json")):
        try:
            queue = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(queue, dict):
            continue
        channel = str(queue.get("channel") or "").strip().lower()
        if channel not in IDENTITY_CHANNELS:
            continue
        account = str(queue.get("account") or "")
        if allow_accounts:
            if account not in allowed_scope(allow_accounts):
                continue
        elif account not in ("", "default"):
            continue
        if wanted and account and account not in wanted:
            continue
        out.append({"queue": path.name,
                    "campaign": str(queue.get("campaign") or path.stem),
                    "account": account, "action": str(queue.get("action") or "")})
    return out


def allowed_scope(allow_accounts) -> set:
    """policy 的账号范围：非空为精确名单；空名单只允许未指定账号与 default。"""
    names = {str(item) for item in (allow_accounts or [])}
    return names if names else {"", "default"}


def migration_scan(data_dir, *, policy=None) -> Dict[str, Any]:
    """扫描常驻授权：哪些账号缺身份绑定、对应哪些队列、迁移命令是什么。"""
    try:
        if policy is None:
            policy = execution_policy.get_policy(data_dir)
    except (OSError, ValueError) as exc:
        return {"dry_run": True, "policy_present": True, "policy_valid": False,
                "error": str(exc), "identity_bound": False, "require_identity": False,
                "allow_actions": [], "allow_accounts": [], "accounts": [],
                "blockers": [], "next": "mkt policy show（先修复或重新授权）"}
    if policy is None:
        return {"dry_run": True, "policy_present": False, "policy_valid": False,
                "identity_bound": False, "require_identity": False,
                "allow_actions": [], "allow_accounts": [], "accounts": [],
                "blockers": [],
                "next": "没有常驻授权可迁移；真实写由已人审 plan 或 CLI --adhoc 约束"}
    active = execution_policy.is_active(policy)
    allow_actions = [str(item) for item in (policy.get("allow_actions") or [])]
    allow_accounts = [str(item) for item in (policy.get("allow_accounts") or [])]
    accounts = allow_accounts or [""]
    identity_accounts = sorted((policy.get("account_identities") or {}).keys())
    for name in identity_accounts:
        if name not in accounts:
            accounts.append(name)
    queues = _queue_bindings(data_dir, accounts, allow_accounts)
    rows = []
    for account in accounts:
        records = policy_identity_records(policy, account)
        binding = "bound" if records else (
            "name_only" if allow_accounts else "legacy_default")
        row = {
            "account": account,
            "binding": binding,
            "blocked": not records,
            "identity_records": [
                {"platform": str(record.get("platform") or ""),
                 "user_id": str(record.get("user_id") or ""),
                 "verified_at": str(record.get("verified_at") or "")}
                for record in records if isinstance(record, dict)],
            "queues": [item for item in queues
                       if item["account"] == account
                       or (account in ("", "default")
                           and item["account"] in ("", "default"))],
        }
        if row["blocked"]:
            row["next"] = migration_commands(data_dir, account)[1]
        rows.append(row)
    blocked = [row for row in rows if row["blocked"]]
    identity_bound = bool(rows) and not blocked
    report = {
        "dry_run": True, "policy_present": True, "policy_valid": active,
        "identity_bound": identity_bound,
        "require_identity": bool(policy.get("require_identity")),
        "allow_actions": allow_actions, "allow_accounts": allow_accounts,
        "accounts": rows, "blockers": blocked,
        "authorized_by": str(policy.get("authorized_by") or ""),
        "authorized_at": str(policy.get("authorized_at") or ""),
        "expires_at": policy.get("expires_at"),
    }
    if blocked:
        report["next"] = migration_commands(data_dir, blocked[0]["account"])[1]
        report["hint"] = ("迁移后真实发送才可用；dry-run 不受影响，只会列出阻塞项。"
                          "稳定数字 ID 需从平台读取（export MKT_CDP_IDENTITY=1）。")
    return report


def _parse_identity_specs(identities) -> Dict[str, Dict[str, str]]:
    """把 `account=platform:user_id` / `platform:user_id`（默认账号）解析成映射。"""
    parsed: Dict[str, Dict[str, str]] = {}
    for item in identities or []:
        if isinstance(item, dict):
            account = str(item.get("account") or "")
            platform = str(item.get("platform") or "").strip()
            user_id = str(item.get("user_id") or "").strip()
        else:
            text = str(item or "")
            if "=" in text:
                account, _, binding = text.partition("=")
            else:
                account, binding = "", text
            platform, _, user_id = binding.partition(":")
            platform, user_id = platform.strip(), user_id.strip()
        account = str(account).strip()
        if not platform or not user_id:
            raise ValueError(
                "身份必须写成 account=platform:user_id 或 platform:user_id: %r" % (item,))
        parsed[account] = {"platform": platform, "user_id": user_id}
    return parsed


def _identity_specs(policy) -> List[str]:
    out = []
    for account, records in (policy.get("account_identities") or {}).items():
        for record in records or []:
            if not isinstance(record, dict):
                continue
            platform = str(record.get("platform") or "").strip()
            user_id = str(record.get("user_id") or "").strip()
            if not platform or not user_id:
                continue
            out.append("%s=%s:%s" % (account, platform, user_id)
                       if account else "%s:%s" % (platform, user_id))
    return out


def _remaining_days(policy, now=None):
    expires = policy.get("expires_at")
    if not expires:
        return None
    current = datetime.now(timezone.utc)
    try:
        expiry = datetime.fromisoformat(str(expires).replace("Z", "+00:00"))
    except ValueError:
        return 1
    if expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=timezone.utc)
    return max(1, int(math.ceil((expiry - current).total_seconds() / 86400.0)))


def migrate_identity(data_dir, *, by: str = "", identities=None, apply: bool = False,
                     probe: bool = False, days=None, channel: str = "x",
                     now=None) -> Dict[str, Any]:
    """把旧授权迁移成带平台身份绑定的授权；默认 dry-run，apply 必须署名。"""
    scan = migration_scan(data_dir)
    if not apply:
        return scan
    if not str(by or "").strip():
        raise ValueError("迁移必须署名（--by）：重新授权是人的决定")
    if not scan.get("policy_present"):
        raise ValueError("没有可迁移的常驻授权；先 mkt policy enable 重新授权")
    if not scan.get("policy_valid"):
        raise ValueError("常驻授权无效/已过期/已撤销：先 mkt policy enable 重新授权")
    policy = execution_policy.get_policy(data_dir)
    provided = _parse_identity_specs(identities)
    blocked = [row for row in scan["accounts"] if row["blocked"]]
    if not blocked:
        return {"dry_run": False, "applied": False, "reason": "already_bound",
                "accounts": scan["accounts"], "policy": policy}

    missing = [row["account"] for row in blocked if row["account"] not in provided]
    if probe and missing:
        if len(blocked) != 1 or len(missing) != 1:
            raise ValueError(
                "多账号迁移必须显式 --identity：--probe 只用于单账号，避免猜测")
        proof = observe(str(channel or "x").strip().lower(), data_dir=data_dir)
        if proof is None:
            probe_info = last_probe()
            raise ValueError(
                "CDP 身份探测失败（%s）：先 export MKT_CDP_IDENTITY=1 并打开 x.com，"
                "或改用 --identity %s=x:<稳定数字ID>"
                % (probe_info.get("reason") or "unknown",
                   blocked[0]["account"] or "<account>"))
        provided[blocked[0]["account"]] = {"platform": "x", "user_id": proof.user_id}
        missing = []
    if missing:
        details = "；".join(
            "账号 %r 缺身份绑定：%s" % (account or "<默认账号>",
                                       migration_commands(data_dir, account)[1])
            for account in missing)
        raise ValueError("缺身份绑定的账号未覆盖：" + details)

    merged = _identity_specs(policy)
    added = []
    for account in [row["account"] for row in blocked]:
        spec = provided[account]
        merged.append("%s=%s:%s" % (account, spec["platform"], spec["user_id"])
                      if account else "%s:%s" % (spec["platform"], spec["user_id"]))
        added.append({"account": account, "platform": spec["platform"],
                      "user_id": spec["user_id"]})

    new_days = days
    if days is None or (isinstance(days, int) and days < 0):
        new_days = _remaining_days(policy)
    new_policy = execution_policy.authorize(
        data_dir, by=str(by).strip(),
        actions=list(policy.get("allow_actions") or []),
        accounts=(list(policy.get("allow_accounts") or []) or None),
        days=new_days, identities=merged, require_identity=True)
    record = {"at": _now_iso(), "by": str(by).strip(),
              "reason": "G-01b legacy policy re-authorized with platform identity",
              "accounts": added,
              "previous": {"authorized_by": policy.get("authorized_by"),
                           "authorized_at": policy.get("authorized_at"),
                           "require_identity": bool(policy.get("require_identity")),
                           "account_identities": sorted(
                               (policy.get("account_identities") or {}).keys())},
              "migrated_authorized_at": new_policy.get("authorized_at")}
    _append_jsonl(_migrations_path(data_dir), record)
    return {"dry_run": False, "applied": True, "accounts": added,
            "identities_added": added, "policy": new_policy,
            "audit": str(_migrations_path(data_dir))}
