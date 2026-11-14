#!/usr/bin/env python3
"""账号身份证明（G-01）—— 登录态 ≠ 授权账号身份，真实发送前必须绑定两者。

session_gate.check_login 只证明「调试浏览器里有一个已登录的界面」，不证明
登录的就是授权账号；execution_policy.allows 校验的 platform_user_id 由调用
方传入，是审批参数而非观测证据。本模块在真实发送路径上补第二道门禁：

- AccountIdentityProof：一次实际平台身份观测（稳定 user_id、handle、观测
  时间、浏览器上下文绑定、来源引用）；
- enforce_identity()：把 routine policy 中该账号的 account_identities 与
  当前观测身份精确比对；身份未知 / 与授权不一致 / 观测过期 / 跨渠道
  一律 fail closed（IdentityBlocked），绝不带病发送；
- observe()/read_proof_from_browser()：读取当前浏览器身份。默认实现保守
  返回 None（身份未知 → 阻断）：真实 CDP 读取联调安排在 2026-11-19 X
  campaign 结束后（期间 CDP 9222 实例与 profiles/ 禁止触碰）；离线测试用
  set_reader() 注入探测器。

纪律：
- 引擎不自我授权；proof 只来自探测，绝不接受 Agent / queue 条目 / MCP
  参数自报的 user_id 充当身份证据。
- 观测即审计：每次门禁决策（allowed/blocked）追加到
  data/identity/events.jsonl，最近观测存 data/identity/<channel>.json。
- 未绑定账号身份的 legacy policy（无 require_identity、无 allow_accounts、
  该账号无 account_identities 记录）不因本门禁改变行为——它们从未声明
  「授权的是哪个具体账号」，应迁移到带 identities 的新授权。
"""
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Optional

# 身份证明的寿命与登录探测同量级：过期必须重探，不得跨调用沿用。
PROOF_MAX_AGE_SECONDS = 300.0

# 首批只对 x 的真实写路径强制（email 的 sender 身份走 SMTP 配置校验，
# 独立于本门禁，见 CHANGELOG 的 G-01 边界说明）。
IDENTITY_CHANNELS = frozenset({"x"})


class IdentityBlocked(RuntimeError):
    """身份门禁拒绝：reason_code 供调用方/账本分类；被拒不得重试真实发送。"""

    def __init__(self, reason_code: str, detail: str = ""):
        super().__init__(f"{reason_code}: {detail}" if detail else reason_code)
        self.reason_code = reason_code
        self.detail = detail


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


def _state_path(data_dir, channel: str) -> Path:
    return _identity_dir(data_dir) / f"{str(channel).strip().lower()}.json"


def load_last_proof(data_dir, channel: str) -> Optional[AccountIdentityProof]:
    """最近一次被门禁接受的身份观测（跨运行审计对照用）。"""
    try:
        return AccountIdentityProof.from_dict(
            json.loads(_state_path(data_dir, channel).read_text(encoding="utf-8")))
    except (OSError, ValueError, json.JSONDecodeError):
        return None


def _record_event(data_dir, *, decision: str, channel: str, account: str = "",
                  reason: str = "", proof: Optional[AccountIdentityProof] = None,
                  now_iso: str = "") -> None:
    event = {"at": now_iso or _now_iso(), "decision": decision,
             "channel": str(channel or "").strip().lower(),
             "account": str(account or ""), "reason": reason or None,
             "proof": proof.to_dict() if proof is not None else None}
    path = _events_path(data_dir)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, ensure_ascii=False) + "\n")
    except OSError:
        pass  # 审计失败不阻断门禁本身；真实环境的写入失败由 doctor 另行暴露
    if proof is not None and decision == "allowed":
        state = _state_path(data_dir, channel)
        try:
            state.write_text(json.dumps(proof.to_dict(), ensure_ascii=False,
                                        indent=2) + "\n", encoding="utf-8")
        except OSError:
            pass


# ---------- 探测（可注入；默认保守 fail closed） ----------

_READER: Optional[Callable[[str], Optional[AccountIdentityProof]]] = None


def set_reader(fn: Optional[Callable[[str], Optional[AccountIdentityProof]]]) -> None:
    """注入身份探测器（离线测试/替代实现）；None 恢复默认。"""
    global _READER
    _READER = fn


def reset_reader() -> None:
    set_reader(None)


def read_proof_from_browser(channel: str) -> Optional[AccountIdentityProof]:
    """默认 CDP 身份读取：当前保守返回 None（身份未知 → fail closed）。

    TODO(G-01 live wiring, 2026-11-19 后)：接入真实读取——连接调试浏览器、
    打开平台本人资料页，从可信 DOM/API 提取稳定 user_id 并绑定
    browserContext/user-data-dir 指纹；拿不到稳定 ID 时返回 None，绝不把
    handle 或界面标记冒充稳定身份。在此之前，绑定身份授权的真实发送会被
    identity_unknown 阻断，这是有意的安全默认。
    """
    return None


def observe(channel: str) -> Optional[AccountIdentityProof]:
    """读取当前浏览器实际身份；探测器缺失/异常一律按身份未知处理。"""
    reader = _READER or read_proof_from_browser
    try:
        return reader(str(channel or "").strip().lower())
    except Exception:  # noqa: BLE001 探测失败=身份未知，不冒充、不放行
        return None


# ---------- 门禁 ----------

def load_policy(data_dir):
    """安全读取 routine policy；读取失败按「无 policy」处理（allows 另行拦截）。"""
    from .execution_policy import get_policy
    try:
        return get_policy(data_dir)
    except (OSError, ValueError):
        return None


def policy_identity_records(policy, account: str = ""):
    return ((policy or {}).get("account_identities") or {}).get(account) or []


def identity_bound(policy, account: str = "") -> bool:
    """该 policy 是否把授权绑定到了具体账号身份（决定门禁是否强制）。"""
    if not isinstance(policy, dict):
        return False
    if policy.get("require_identity"):
        return True
    if policy.get("allow_accounts"):
        return True
    return bool(policy_identity_records(policy, account))


def enforce_identity(data_dir, channel, account, proof, *, policy=None,
                     max_age_seconds: float = PROOF_MAX_AGE_SECONDS,
                     now: Optional[float] = None) -> Dict[str, Any]:
    """真实发送前的身份门禁：通过返回证据 dict；拒绝抛 IdentityBlocked。

    只在 policy 绑定了账号身份时强制（bound 判定见 identity_bound）。
    证据 dict 的 authorized_account / observed_platform_identity /
    identity_proof_ref 会写进发送回执。
    """
    channel = str(channel or "").strip().lower()
    account = str(account or "")
    if policy is None:
        policy = load_policy(data_dir)
    if not identity_bound(policy, account):
        return {"enforced": False, "reason": "policy_not_identity_bound"}

    def _blocked(reason: str, detail: str) -> IdentityBlocked:
        _record_event(data_dir, decision="blocked", channel=channel,
                      account=account, reason=reason, proof=proof)
        return IdentityBlocked(reason, detail)

    records = policy_identity_records(policy, account)
    if not records:
        # 授权本身没有身份记录（只按账号名授权的旧式授权）：优先要求重新
        # 授权——先补授权，再谈观测。这比「身份未知」更可行动。
        raise _blocked("authorization_missing_identity",
                       "授权只含账号名、没有平台身份记录：请用 identities 重新授权后再真实发送")
    if proof is None:
        raise _blocked("identity_unknown",
                       "无法观测实际平台账号身份（探测器缺失/失败/未接线）：拒绝真实发送")
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
            "identity_proof_ref": f"{proof.channel}:{proof.observed_at}",
            "handle": proof.handle}
