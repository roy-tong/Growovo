#!/usr/bin/env python3
"""统一启动、监控与安全恢复（G-05）。

三件事，都围绕「新手第一次安全跑起来」：

1. **九步启动向导**（``build_start_plan`` / ``apply_start_plan``）：
   自动体检 → 凭据/登录/权限清单 → 账号证明 → 目标与指标来源 → 首批 3 条
   候选预览 → 风险与配额 → 显式 Arm → 前台/计划任务 → 日报订阅。
   向导不代签政策、不制造身份凭据、不自动 Arm；缺登录/授权就停在
   ``ready_to_arm`` 之前（``draft``），并给出下一条准确命令。

2. **状态解释与告警**（``explain_status`` / ``list_incidents`` /
   ``diagnose``）：按 C-01 返回 next_actor / next_required_action /
   blocked_reason_code / last_updated_at / has_pending_external_effect /
   artifact_refs；每条告警有唯一、稳定的 ``INC-xxxxxxxxxxxx`` 事件 ID 和
   人工恢复建议；八行故障矩阵逐行可判定。

3. **安全恢复**（``resume_after_reconciliation``）：``indeterminate`` 外部
   动作必须先人工核对账本（``mkt ledger resolve``）才能恢复；恢复只改
   run 状态，不重发、不撤销已发生的外部动作。

纪律（与 PRD §4.3、§12.3/12.4、C-01 对齐）：
- 不新建第二套运行状态机：canonical 状态仍是 ``data/grow/<id>.json`` 的
  ``status``（draft/ready_to_arm/active/needs_attention/paused/completed/
  cancelled）；本模块只读它并给出 UX 投影，绝不反写覆盖。
- ``indeterminate`` 是动作层状态：未裁决时禁止自动重试（执行门禁已强制），
  恢复也必须先有 ``Ledger.resolve`` 记录。
- 读数失败不伪造 0；缺数写「取不到」，告警里明确说明。
- 窗口按 RunSpec 的 IANA 时区算（见 core.timezone_windows），不用本机时区。
- 所有入口默认只读或 dry-run；写入（建 run、装调度、改状态）都要显式
  ``--apply`` 或人工命令。
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

#: C-01 错误码注册表：机器语义，不是自然语言同义词。
C01_ERROR_CODES = frozenset({
    "INPUT_REQUIRED", "AUTH_REQUIRED", "REVIEW_REQUIRED", "UNSUPPORTED_SCOPE",
    "POLICY_DENIED", "RATE_LIMITED", "STALE_SOURCE",
    "INDETERMINATE_EXTERNAL_ACTION", "ARTIFACT_PARTIAL", "INTERNAL_ERROR",
})

#: 九步向导（字段顺序即 PRD 顺序）。
START_STEPS: Tuple[Tuple[str, str], ...] = (
    ("doctor", "环境与凭据体检"),
    ("credentials", "缺凭据/登录态/权限清单"),
    ("identity", "账号身份证明"),
    ("goal", "确认目标与指标来源"),
    ("preview", "首批 3 条候选预览"),
    ("risk", "确认风险与配额"),
    ("arm", "显式开闸（必须人工）"),
    ("schedule", "前台或计划任务模式"),
    ("delivery", "日报订阅（默认本地文件）"),
)

STEP_STATES = ("ok", "needs_input", "needs_review", "blocked", "pending_human")

#: 八行故障矩阵（PRD G-05 表格）：处理方式 + 恢复前提 + C-01 错误码。
FAULT_MATRIX: Tuple[Dict[str, str], ...] = (
    {"code": "browser_unavailable",
     "title": "调试浏览器崩溃/连接丢失",
     "handling": "暂停写入，读数标记降级，不伪造 0",
     "recovery": "重启调试 Chrome，重新做账号身份证明后再恢复",
     "error_code": "AUTH_REQUIRED"},
    {"code": "account_or_policy",
     "title": "账号切换/授权过期",
     "handling": "全部真实写停机",
     "recovery": "重新确认授权账号与身份后重新授权（mkt policy enable）",
     "error_code": "AUTH_REQUIRED"},
    {"code": "indeterminate_external_action",
     "title": "点击成功但未知是否发布",
     "handling": "标 indeterminate，保留 target/idempotency，禁止自动重试",
     "recovery": "人工到平台后台核对，用 mkt ledger resolve 记回，再 mkt grow resume --after-reconciliation",
     "error_code": "INDETERMINATE_EXTERNAL_ACTION"},
    {"code": "ledger_or_suppression_corrupt",
     "title": "账本/抑制表损坏",
     "handling": "fail closed，隔离坏数据，不把坏行当成没有",
     "recovery": "修复或恢复账本/抑制表并证明完整性后再恢复",
     "error_code": "INTERNAL_ERROR"},
    {"code": "readings_degraded",
     "title": "网络中断/读数失败",
     "handling": "不伪造 0；按策略停止写或只做不受读数约束的低风险活动",
     "recovery": "恢复读数后核对；读数失败时缺数写取不到，不伪造 0",
     "error_code": "STALE_SOURCE"},
    {"code": "duplicate_instance",
     "title": "运行重复启动",
     "handling": "单实例锁；另实例不执行",
     "recovery": "确认当前实例可被识别；要接管先 mkt grow pause 再 resume",
     "error_code": "INTERNAL_ERROR"},
    {"code": "supply_shortage",
     "title": "内容供给不足/已过期",
     "handling": "标 supply gap，不用旧文案填补",
     "recovery": "按需求单生产新内容，过审后 mkt grow stage 入队",
     "error_code": "INPUT_REQUIRED"},
    {"code": "timezone_switch",
     "title": "两个日期时区切换",
     "handling": "以 RunSpec IANA 时区计算窗口",
     "recovery": "校验时间戳与日配额后再恢复；不要用本机时区顶替",
     "error_code": "REVIEW_REQUIRED"},
)

_MATRIX_BY_CODE = {row["code"]: row for row in FAULT_MATRIX}

#: 首次 live 的默认：一个已验证平台、一种动作、最小配额、无自动扩量。
DEFAULT_ACTIONS_PER_WEEK = 7
DEFAULT_TARGET = None


class RunbookError(RuntimeError):
    """带 C-01 错误码的拒绝：调用方按码决定补资料/申请人审/终止。"""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code if code in C01_ERROR_CODES else "INTERNAL_ERROR"


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------

def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _today() -> str:
    return date.today().isoformat()


def _text(value) -> str:
    return str(value if value is not None else "").strip()


def _belongs(campaign: str, run_id: str) -> bool:
    campaign = _text(campaign)
    return campaign == run_id or campaign.startswith(run_id + "-")


def _parse_day(value) -> Optional[date]:
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def run_path_for(data_dir, run_id: str) -> Path:
    return Path(data_dir) / "grow" / f"{_text(run_id)}.json"


def _incident_id(run_id: str, kind: str, key: str) -> str:
    """稳定且唯一：同一 run 的同一故障每次列出来都是同一个 ID。"""
    import hashlib
    seed = f"{_text(run_id)}|{_text(kind)}|{_text(key)}"
    return "INC-" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:12]


def _matrix(code: str) -> Dict[str, str]:
    return _MATRIX_BY_CODE.get(code, FAULT_MATRIX[0])


def _load_run(data_dir, run_path=None, run_id: str = ""):
    from .grow_v2 import GrowRunV2

    path = Path(run_path) if run_path else run_path_for(data_dir, run_id)
    run = GrowRunV2(path)
    return run, run.load()


# ---------------------------------------------------------------------------
# 第一步：读事实、授权、登录态、账号
# ---------------------------------------------------------------------------

def _facts_state(path) -> Dict[str, Any]:
    source = Path(str(path or "")).expanduser()
    if not source.is_file():
        return {"ok": False, "code": "INPUT_REQUIRED",
                "detail": f"找不到产品事实包：{source}",
                "fix": f"mkt grow start --facts {source} --goal <goal-id> "
                       "--dry-run"}
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"ok": False, "code": "INPUT_REQUIRED",
                "detail": f"事实包不是合法 JSON：{exc}",
                "fix": "修好 facts.json（product/icp/value_events）"}
    if not isinstance(payload, dict):
        return {"ok": False, "code": "INPUT_REQUIRED",
                "detail": "事实包必须是 JSON 对象"}
    product = payload.get("product") if isinstance(payload.get("product"), dict) else {}
    missing = [name for name in ("name", "what", "url") if not _text(product.get(name))]
    if missing:
        return {"ok": False, "code": "INPUT_REQUIRED",
                "detail": "产品事实缺字段：" + "、".join(missing),
                "fix": "补齐 product.name / product.what / product.url"}
    return {"ok": True, "facts": payload, "path": str(source.resolve()),
            "product": product,
            "icp": [row for row in (payload.get("icp") or []) if isinstance(row, dict)],
            "value_events": [row for row in (payload.get("value_events") or [])
                             if isinstance(row, dict)],
            "red_lines": [str(x) for x in
                          ((payload.get("constraints") or {}).get("red_lines") or [])]}


def _policy_state(data_dir, actions: Sequence[str],
                  account: str) -> Dict[str, Any]:
    """把常驻授权拆成「动作覆盖 / 账号范围 / 身份记录」三段判断。"""
    from .execution_policy import get_policy

    try:
        policy = get_policy(Path(data_dir))
    except Exception as exc:  # noqa: BLE001 坏 policy 必须 fail closed
        return {"ok": False, "code": "POLICY_DENIED",
                "detail": f"常驻授权文件读不了：{exc}",
                "fix": "修复或重新生成 policies/routine-marketing.json"}
    if not isinstance(policy, dict) or not policy:
        actions_text = ",".join(sorted(actions)) or "x:reply"
        return {"ok": False, "code": "AUTH_REQUIRED",
                "detail": "还没有常驻授权：真实写会被执行门禁拒绝",
                "fix": f'mkt policy enable --by "$USER" --actions {actions_text} --days 30'}
    if policy.get("revoked"):
        return {"ok": False, "code": "AUTH_REQUIRED",
                "detail": "常驻授权已被撤销",
                "fix": "重新走人工授权（mkt policy enable），不要让 Agent 自签"}
    expires_at = _text(policy.get("expires_at"))
    if expires_at:
        try:
            expiry = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
            current = datetime.now(expiry.tzinfo) if expiry.tzinfo else datetime.now()
            if current >= expiry:
                return {"ok": False, "code": "AUTH_REQUIRED",
                        "detail": f"常驻授权已过期：{expires_at}",
                        "fix": "重新人工授权（mkt policy enable --days 30）"}
        except ValueError:
            return {"ok": False, "code": "POLICY_DENIED",
                    "detail": f"授权过期时间不是合法 ISO 时间：{expires_at!r}",
                    "fix": "重新生成授权"}
    allowed = [str(a) for a in (policy.get("allow_actions") or [])]
    missing = [str(a) for a in actions if str(a) not in allowed]
    if missing:
        return {"ok": False, "code": "AUTH_REQUIRED",
                "detail": "常驻授权未覆盖动作：" + "、".join(missing),
                "fix": ('mkt policy enable --by "$USER" --actions '
                        + ",".join(sorted(set(allowed) | set(actions)))
                        + " --days 30")}
    accounts = [str(a) for a in (policy.get("allow_accounts") or [])]
    if accounts and _text(account) not in accounts:
        return {"ok": False, "code": "AUTH_REQUIRED",
                "detail": f"账号 {account!r} 不在授权范围：{accounts}",
                "fix": ('mkt policy enable --by "$USER" --actions '
                        + ",".join(sorted(allowed)) + " --account " + _text(account))}
    if not accounts and _text(account) not in ("", "default"):
        return {"ok": False, "code": "AUTH_REQUIRED",
                "detail": "授权没有登记账号范围，但本次指定了具体账号",
                "fix": "重新授权并带上 --account/--identities"}
    records = ((policy.get("account_identities") or {}).get(_text(account)) or [])
    if policy.get("require_identity") and not records:
        return {"ok": True, "identity_ok": False, "code": "REVIEW_REQUIRED",
                "detail": "授权要求绑定平台身份，但这个账号没有身份记录",
                "fix": ('mkt policy enable --by "$USER" --actions '
                        + ",".join(sorted(allowed)) + " --identity "
                        + (_text(account) or "default") + "=x:<stable-user-id>"),
                "policy": policy, "records": [],
                "require_identity": True, "expires_at": expires_at or None}
    return {"ok": True, "identity_ok": True, "policy": policy,
            "records": records,
            "require_identity": bool(policy.get("require_identity")),
            "expires_at": expires_at or None}


def _login_state(channel: str,
                 login_probe: Optional[Callable[[str], Dict[str, Any]]]):
    from . import session_gate
    if not channel or channel not in session_gate.PROBES:
        return {"channel": channel, "status": "unknown",
                "error": "该渠道没有登录态探测：真实写前请人工确认"}
    probe = login_probe or session_gate.check_login
    try:
        state = probe(channel) or {}
    except Exception as exc:  # noqa: BLE001 探测失败按通道不可用处理
        state = {"channel": channel,
                 "status": session_gate.CDP_UNAVAILABLE,
                 "error": " ".join(str(exc).split())[:160]}
    return state


def _identity_state(data_dir, account: str, policy_state: Dict[str, Any]
                    ) -> Dict[str, Any]:
    if not _text(account):
        return {"ok": True, "code": None,
                "detail": "未指定账号：按 legacy 授权执行，不校验账号健康"}
    try:
        from .accounts import AccountRegistry
        record = AccountRegistry().get(account)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "code": "REVIEW_REQUIRED",
                "detail": f"读不到账号 {account}：{exc}",
                "fix": "mkt account add 登记账号（或去掉 --account）"}
    status = _text(record.get("status"))
    score = float((record.get("health") or {}).get("score") or 0)
    if status != "eligible" or score < 60:
        return {"ok": False, "code": "REVIEW_REQUIRED",
                "detail": f"{account}: status={status}, health={score}",
                "fix": "先把账号修到 eligible（mkt account 系列命令），不要带病开闸"}
    return {"ok": True, "code": None,
            "detail": f"{account}: status={status}, health={score}"}


def _auto_channels() -> Dict[str, str]:
    try:
        from . import platform_caps
        return {str(row["platform"]): str(row["tier"])
                for row in platform_caps.matrix()}
    except Exception:  # noqa: BLE001
        return {"x": "auto", "email": "auto"}


def _channel_actions(channel: str) -> set:
    try:
        from channels import REGISTRY
        return set(REGISTRY.get(channel, {}).get("actions") or [])
    except Exception:  # noqa: BLE001
        return set()


# ---------------------------------------------------------------------------
# 首批 3 条候选预览（离线、确定、不可直接发）
# ---------------------------------------------------------------------------

_PREVIEW_FORMULAS = ("reply.agree_extend_ask", "reply.contrast_boundary",
                     "reply.koc_peer")
_PREVIEW_ANGLES = (
    "先复述对方的具体问题，再给一个可复算的细节",
    "指出一个常见做法的边界，再给出我们的取舍",
    "引用对方原话里的一句，补一个同类场景的做法",
)


def _preview_candidates(facts: Dict[str, Any], audience: str,
                        limit: int = 3) -> List[Dict[str, Any]]:
    product = (facts.get("product") or {}) if isinstance(facts, dict) else {}
    icp = [row for row in (facts.get("icp") or []) if isinstance(row, dict)]
    what = _text(product.get("what")) or "产品要说清的那件事"
    who = _text((icp[0] if icp else {}).get("hypothesis")) or "目标用户"
    queries: List[str] = []
    try:
        from . import grow_fit
        profile = grow_fit.product_profile(facts)
        queries = [str(q) for q in (grow_fit.auto_queries(profile) or [])]
    except Exception:  # noqa: BLE001 预览不依赖搜索能力
        queries = []
    while len(queries) < limit:
        queries.append(f"{who} 讨论 {what} 的帖子")
    rows = []
    for index in range(limit):
        rows.append({
            "index": index + 1,
            "kind": "reply",
            "formula": _PREVIEW_FORMULAS[index % len(_PREVIEW_FORMULAS)],
            "target_hint": f"讨论「{queries[index]}」的帖子（未读取，需人工确认）",
            "their_problem": f"{who} 在「{what}」上遇到的具体问题（待读原帖核实）",
            "why_we_help": f"{what}：先给可复算的细节，不先做承诺",
            "expected_action": "试用一次最小样例（例如一次对账）",
            "draft_angle": _PREVIEW_ANGLES[index % len(_PREVIEW_ANGLES)],
            "risk": "未读原帖、未核身份：只是预览，不能直接发",
            "needs_review": True,
            "is_sendable": False,
        })
    return rows


def _risk_block(facts: Dict[str, Any], channels: Sequence[str],
                actions: Sequence[str], actions_per_week: int,
                audience: str) -> Dict[str, Any]:
    tiers = _auto_channels()
    gated = [ch for ch in channels if tiers.get(ch) != "auto"]
    from . import grow_schedule as gs
    week = gs.week_start_of(_today(), _today())
    daily = gs.split_week(int(actions_per_week), week)
    return {
        "channels": list(channels),
        "actions": list(actions),
        "actions_per_week": int(actions_per_week),
        "daily_preview": {day: sum(bucket.values())
                          for day, bucket in daily.items()},
        "max_per_day": max([sum(b.values()) for b in daily.values()] or [0]),
        "auto_scale": False,
        "audience": audience,
        "red_lines": list(((facts.get("constraints") or {})
                          .get("red_lines") or [])),
        "gated_channels": gated,
        "note": ("首次 live 只用一个已验证平台、一种动作、最小配额、无自动扩量；"
                 "扩渠道/加量必须重新人审"),
    }


# ---------------------------------------------------------------------------
# 日报订阅：生成与发送是两件事
# ---------------------------------------------------------------------------

def delivery_plan(run_id: str, *, mode: str = "local", owner: str = "",
                  email_authorized: bool = False, env=None) -> Dict[str, Any]:
    import os
    env = os.environ if env is None else env
    mode = _text(mode or "local").lower()
    if mode not in ("local", "email"):
        raise RunbookError("INPUT_REQUIRED",
                           "delivery 只支持 local / email")
    base = {
        "mode": mode,
        "owner": _text(owner),
        "wizard_sends": False,
        "live_send": False,
        "card_dir": f"data/grow/{run_id}/cards",
        "outbox_dir": f"data/grow/{run_id}/outbox",
    }
    if mode == "local":
        return dict(base, email_authorized=False,
                    next_action=("日报默认只写本地文件（cards/ 与 outbox 的 dry-run .eml）；"
                                 "要发邮件需单独授权"))
    if not _text(owner):
        return dict(base, email_authorized=False,
                    next_action=("Email 发送需要收件人：加 --email-owner <邮箱>，"
                                 "并用 --authorize-email 单独授权"))
    missing_env = [key for key in ("MKT_EMAIL_USER", "MKT_EMAIL_APP_PASSWORD")
                   if not _text(env.get(key))]
    if not email_authorized:
        return dict(base, email_authorized=False, missing_env=missing_env,
                    next_action=("Email 发送需要单独授权：加 --authorize-email"
                                 "（并配置 MKT_EMAIL_USER / MKT_EMAIL_APP_PASSWORD）"))
    return dict(base, email_authorized=True,
                live_send=not missing_env, missing_env=missing_env,
                next_action=("发送仍由人执行 mkt grow tick <id> --deliver；"
                             "日报生成与邮件发送是两步，向导不会替你发"))


# ---------------------------------------------------------------------------
# 九步向导
# ---------------------------------------------------------------------------

def _step(name: str, state: str, detail: str, next_action: str,
          *, blocking: bool = False, code: Optional[str] = None,
          evidence: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    title = dict(START_STEPS).get(name, name)
    if state not in STEP_STATES:
        raise RunbookError("INTERNAL_ERROR", f"未知步骤状态：{state}")
    return {"name": name, "title": title, "state": state, "detail": detail,
            "next_action": next_action, "blocking": bool(blocking),
            "error_code": code, "evidence": evidence or {}}


def _slug(value: str) -> str:
    text = re.sub(r"[^a-z0-9-]+", "-", _text(value).lower()).strip("-")
    return text or "grow"


def build_start_plan(data_dir, *, facts_path, goal_id: str,
                     run_id: str = "", target: Optional[float] = None,
                     channels: Sequence[str] = ("x",),
                     actions: Sequence[str] = ("reply",),
                     actions_per_week: int = DEFAULT_ACTIONS_PER_WEEK,
                     account: str = "", handle: str = "",
                     audience: str = "us-west", owner: str = "",
                     started: str = "", schedule: str = "foreground",
                     delivery: str = "local", email_owner: str = "",
                     email_authorized: bool = False,
                     source_systems: Sequence[str] = (),
                     login_probe: Optional[Callable[[str], Dict[str, Any]]] = None,
                     now: Optional[str] = None) -> Dict[str, Any]:
    """只读地算出九步计划；不写 run、不装调度、不发送。"""
    data_dir = Path(data_dir)
    channels = [str(c) for c in (channels or []) if _text(c)]
    actions = [str(a) for a in (actions or []) if _text(a)]
    source_systems = [str(s) for s in (source_systems or []) if _text(s)]
    run_id = _text(run_id)
    facts_state = _facts_state(facts_path)
    facts = facts_state.get("facts") if facts_state.get("ok") else {}
    if not run_id:
        product = (facts or {}).get("product") or {}
        run_id = (f"{_slug(product.get('name') or 'grow')}-"
                  f"{_today().replace('-', '')}")
    actions_list = ([f"{channels[0]}:{actions[0]}"]
                    if channels and actions else [])
    allowed_actions = _channel_actions(channels[0]) if channels else set()
    bad_actions = [a for a in actions
                   if allowed_actions and a not in allowed_actions]
    policy_state = _policy_state(data_dir, actions_list, account)
    identity_state = _identity_state(data_dir, account, policy_state)
    login_state = _login_state(channels[0] if channels else "", login_probe)
    delivery_state = delivery_plan(run_id, mode=delivery, owner=email_owner,
                                   email_authorized=email_authorized)
    risk = _risk_block(facts or {}, channels, actions, int(actions_per_week),
                       audience)
    preview = _preview_candidates(facts or {}, audience)

    steps: List[Dict[str, Any]] = []
    missing: List[str] = []
    blocking_issues: List[Dict[str, Any]] = []

    def record(issue: str, code: Optional[str] = None):
        if code:
            blocking_issues.append({"issue": issue, "code": code})

    # 1 doctor
    if not facts_state.get("ok"):
        missing.append("product_facts")
        steps.append(_step("doctor", "needs_input",
                           facts_state.get("detail", "事实包不可用"),
                           facts_state.get("fix", ""), blocking=True,
                           code="INPUT_REQUIRED"))
        record("facts", "INPUT_REQUIRED")
    elif bad_actions:
        steps.append(_step(
            "doctor", "needs_review",
            "渠道不支持的动作：" + "、".join(bad_actions),
            f"{channels[0]} 支持：" + "、".join(sorted(allowed_actions)),
            blocking=True, code="UNSUPPORTED_SCOPE"))
        record("scope", "UNSUPPORTED_SCOPE")
    elif not policy_state.get("ok"):
        steps.append(_step("doctor", "blocked",
                           policy_state.get("detail", "授权不可用"),
                           policy_state.get("fix", ""), blocking=True,
                           code=policy_state.get("code")))
        record("policy", policy_state.get("code"))
    elif login_state.get("status") in ("logged_out",):
        steps.append(_step(
            "doctor", "needs_input",
            f"{login_state.get('channel')} 未登录：真实写会被停",
            f"在调试 Chrome 里登录 {login_state.get('channel')} 后重跑 "
            "mkt grow doctor", blocking=True, code="AUTH_REQUIRED"))
        record("login", "AUTH_REQUIRED")
    elif login_state.get("status") == "cdp_unavailable":
        steps.append(_step(
            "doctor", "needs_input",
            "调试浏览器未连接：" + _text(login_state.get("error")),
            "先运行 ./launch.command 启动调试 Chrome，再重跑 mkt grow doctor",
            blocking=True, code="AUTH_REQUIRED"))
        record("browser", "AUTH_REQUIRED")
    elif policy_state.get("identity_ok") is False:
        steps.append(_step("doctor", "needs_review",
                           policy_state.get("detail", "账号身份未就绪"),
                           policy_state.get("fix", ""), blocking=True,
                           code="REVIEW_REQUIRED"))
        record("identity", "REVIEW_REQUIRED")
    elif not identity_state.get("ok"):
        steps.append(_step("doctor", "needs_review",
                           identity_state.get("detail", "身份未就绪"),
                           identity_state.get("fix", ""), blocking=True,
                           code=identity_state.get("code")))
        record("identity", identity_state.get("code"))
    else:
        steps.append(_step("doctor", "ok",
                           "工作区、事实包、授权与登录态体检通过",
                           "确认凭据与权限清单"))

    # 2 credentials
    if bad_actions:
        steps.append(_step("credentials", "needs_review",
                           "动作不在渠道能力范围内：" + "、".join(bad_actions),
                           f"{channels[0]} 支持：" + "、".join(sorted(allowed_actions)),
                           blocking=True, code="UNSUPPORTED_SCOPE"))
    elif not policy_state.get("ok"):
        missing.append("risk_policy")
        steps.append(_step("credentials", "blocked",
                           policy_state.get("detail", "缺少常驻授权"),
                           policy_state.get("fix", ""), blocking=True,
                           code=policy_state.get("code")))
    elif login_state.get("status") == "logged_out":
        steps.append(_step("credentials", "needs_input",
                           f"{login_state.get('channel')} 登录态缺失",
                           "登录后重跑 mkt grow doctor", blocking=True,
                           code="AUTH_REQUIRED"))
    elif login_state.get("status") in ("cdp_unavailable", "unknown"):
        steps.append(_step("credentials", "needs_input",
                           "登录态未确认：" + _text(login_state.get("error")),
                           "启动调试 Chrome 并登录后重跑 mkt grow doctor",
                           blocking=True, code="AUTH_REQUIRED"))
    else:
        steps.append(_step("credentials", "ok",
                           "授权覆盖动作：" + "、".join(actions_list),
                           "账号身份与授权账号一致后才能开闸"))

    # 3 identity
    identity_blocked = (not identity_state.get("ok")
                        or policy_state.get("identity_ok") is False)
    if identity_blocked:
        missing.append("verified_account")
        detail = (identity_state.get("detail") if not identity_state.get("ok")
                  else policy_state.get("detail"))
        fix = (identity_state.get("fix") if not identity_state.get("ok")
               else policy_state.get("fix"))
        code = (identity_state.get("code") if not identity_state.get("ok")
                else "REVIEW_REQUIRED")
        steps.append(_step("identity", "needs_review",
                           detail or "账号身份未证明", fix or "",
                           blocking=True, code=code))
    else:
        detail = identity_state.get("detail", "账号身份就绪")
        if policy_state.get("require_identity"):
            detail += "；授权要求真实发送前观测到同一账号"
        steps.append(_step("identity", "ok", detail,
                           "真实发送前仍会做一次账号身份观测（不接受自报）"))

    # 4 goal
    goal_ok = bool(_text(goal_id))
    if not goal_ok:
        missing.append("goal")
        steps.append(_step("goal", "needs_input", "还没有确认主目标",
                           "mkt grow start --goal awareness.followers ...",
                           blocking=True, code="INPUT_REQUIRED"))
    else:
        goal_step_state = "ok"
        goal_detail = f"主目标：{goal_id}"
        goal_next = "目标与指标来源已确认"
        if not facts_state.get("value_events"):
            missing.append("activation_definition")
            goal_step_state = "needs_review"
            goal_detail += "；没有激活事件定义（只能报告互动与执行结果，不显示激活=0）"
            goal_next = ("补 value_events（激活定义）或在 mkt grow events import "
                         "里声明授权来源")
        if not source_systems:
            missing.append("source_access")
            if goal_step_state == "ok":
                goal_step_state = "needs_review"
            goal_next = (goal_next + "；" if goal_step_state != "ok" else "") + \
                "业务事件源未声明：可先跑，但只报互动/执行结果"
        steps.append(_step("goal", goal_step_state, goal_detail, goal_next,
                           blocking=not goal_ok,
                           code="INPUT_REQUIRED" if not goal_ok else None))

    # 5 preview
    steps.append(_step("preview", "ok",
                       f"已生成 {len(preview)} 条离线候选预览（未入队、不可直发）",
                       "逐条人工看对象与内容；确认后再 mkt grow stage"))

    # 6 risk
    if not channels:
        steps.append(_step("risk", "needs_input", "还没有选择渠道",
                           "加 --channels x（首次 live 只用一个 AUTO 渠道）",
                           blocking=True, code="INPUT_REQUIRED"))
    elif bad_actions:
        steps.append(_step(
            "risk", "needs_review",
            "渠道不支持的动作：" + "、".join(bad_actions),
            f"{channels[0]} 支持：" + "、".join(sorted(allowed_actions)),
            blocking=True, code="UNSUPPORTED_SCOPE"))
    elif risk["gated_channels"]:
        steps.append(_step("risk", "needs_review",
                           "渠道未解锁自动执行：" + "、".join(risk["gated_channels"]),
                           "首次 live 只用 AUTO 渠道（x/email）；其余出人工作业单",
                           blocking=True, code="UNSUPPORTED_SCOPE"))
        record("scope", "UNSUPPORTED_SCOPE")
    elif int(actions_per_week) < 1:
        steps.append(_step("risk", "needs_input", "配额必须 ≥1",
                           "加 --actions-per-week 1（最小配额起步）",
                           blocking=True, code="INPUT_REQUIRED"))
    else:
        steps.append(_step(
            "risk", "ok",
            f"风险与配额：{len(channels)} 个渠道、{int(actions_per_week)} 条/周、"
            f"单日上限 {risk['max_per_day']}、无自动扩量",
            "确认红线：" + ("、".join(risk["red_lines"]) or "（facts 未写）")))

    # 7 arm（永远等人）
    missing.append("required_human_review")
    steps.append(_step("arm", "pending_human",
                       "开闸必须由人显式执行；向导不会代签",
                       f"mkt grow arm {run_id}"))

    # 8 schedule
    schedule = _text(schedule or "foreground").lower()
    if schedule not in ("foreground", "scheduled"):
        steps.append(_step("schedule", "needs_input",
                           "模式只支持 foreground / scheduled",
                           "加 --schedule foreground 或 --schedule scheduled",
                           blocking=True, code="INPUT_REQUIRED"))
    else:
        loop_script = str(Path(data_dir) / "grow" / run_id / "run-loop.command")
        commands = ([loop_script, f"mkt grow daemon {run_id}"]
                    if schedule == "foreground"
                    else [f"mkt grow install {run_id}"])
        steps.append(_step("schedule", "ok",
                           ("前台常驻（Ctrl-C 停止）" if schedule == "foreground"
                            else "生成 launchd/crontab 文件，不自动加载"),
                           "；".join(commands)))

    # 9 delivery
    if delivery_state["mode"] == "email" and not delivery_state["email_authorized"]:
        steps.append(_step("delivery", "needs_review",
                           "日报默认本地文件；Email 发送未授权",
                           delivery_state["next_action"]))
    else:
        steps.append(_step("delivery", "ok",
                           f"日报模式：{delivery_state['mode']}（生成与发送分离）",
                           delivery_state["next_action"]))

    # 汇总状态：只有 blocking 步骤影响能否进入 ready_to_arm
    ux_state = "READY"
    for step in steps:
        if not step["blocking"]:
            continue
        if step["state"] == "blocked":
            ux_state = "BLOCKED"
            break
        if step["state"] == "needs_input" and ux_state != "BLOCKED":
            ux_state = "NEEDS_INPUT"
        elif step["state"] == "needs_review" and ux_state == "READY":
            ux_state = "NEEDS_REVIEW"
    blocked = next((i for i in blocking_issues if i["code"]), None)
    blocked_code = blocked["code"] if blocked else None
    can_apply = (facts_state.get("ok") and policy_state.get("ok")
                 and goal_ok and bool(channels) and not bad_actions
                 and not risk["gated_channels"]
                 and int(actions_per_week) >= 1)
    ready_to_arm = can_apply and ux_state == "READY"
    next_command, next_action = _next_for_plan(
        ux_state=ux_state, run_id=run_id, facts_state=facts_state,
        policy_state=policy_state, login_state=login_state,
        identity_state=identity_state, goal_ok=goal_ok,
        risk=risk, can_apply=can_apply, channels=channels,
        bad_actions=bad_actions)
    return {
        "schema": "growovo.start-plan/1",
        "generated_at": now or _now(),
        "data_dir": str(data_dir.resolve()),
        "run_id": run_id,
        "applied": False,
        "ux_state": ux_state,
        "can_apply": bool(can_apply),
        "ready_to_arm": bool(ready_to_arm),
        "blocked_reason_code": blocked_code,
        "missing": missing,
        "next_actor": "human",
        "next_required_action": next_action,
        "next_command": next_command,
        "steps": steps,
        "preview": preview,
        "risk": risk,
        "schedule": {
            "mode": schedule,
            "commands": ([str(Path(data_dir) / "grow" / run_id / "run-loop.command"),
                          f"mkt grow daemon {run_id}"]
                         if schedule == "foreground"
                         else [f"mkt grow install {run_id}"]),
            "files_only": schedule == "scheduled",
        },
        "delivery": delivery_state,
        "options": {
            "facts_path": str(Path(str(facts_path)).expanduser().resolve()),
            "goal_id": _text(goal_id),
            "run_id": run_id,
            "target": target,
            "channels": channels,
            "actions": actions,
            "actions_per_week": int(actions_per_week),
            "account": _text(account),
            "handle": _text(handle),
            "audience": _text(audience) or "us-west",
            "owner": _text(owner) or _text(email_owner),
            "started": _text(started) or _today(),
            "schedule": schedule,
            "delivery": delivery_state,
            "source_systems": source_systems,
        },
        "login_state": login_state,
        "policy_state": {k: v for k, v in policy_state.items() if k != "policy"},
    }


def _next_for_plan(*, ux_state: str, run_id: str, facts_state: Dict[str, Any],
                   policy_state: Dict[str, Any], login_state: Dict[str, Any],
                   identity_state: Dict[str, Any], goal_ok: bool,
                   risk: Dict[str, Any], can_apply: bool,
                   channels: Sequence[str] = (),
                   bad_actions: Sequence[str] = ()) -> Tuple[str, str]:
    if not facts_state.get("ok"):
        return (facts_state.get("fix", ""),
                facts_state.get("detail", "缺产品事实包"))
    if not channels:
        return ("mkt grow start --channels x ...",
                "先选一个 AUTO 渠道（首次 live 只用一个）")
    if bad_actions:
        return ("mkt grow platforms",
                "渠道不支持这些动作：" + "、".join(bad_actions))
    if not policy_state.get("ok"):
        return (policy_state.get("fix", ""),
                policy_state.get("detail", "缺常驻授权"))
    if login_state.get("status") == "logged_out":
        channel = _text(login_state.get("channel"))
        return ("mkt grow doctor",
                f"在调试 Chrome 里登录 {channel} 后重跑 mkt grow doctor")
    if login_state.get("status") in ("cdp_unavailable", "unknown"):
        return ("mkt grow doctor",
                "启动调试 Chrome 并登录后重跑 mkt grow doctor")
    if not identity_state.get("ok"):
        return (identity_state.get("fix", ""),
                identity_state.get("detail", "账号身份未就绪"))
    if not goal_ok:
        return ("mkt grow start --goal awareness.followers ...",
                "确认主目标")
    if risk.get("gated_channels"):
        return ("mkt grow platforms",
                "首次 live 只用 AUTO 渠道：" + "、".join(risk["gated_channels"]))
    if not can_apply:
        return ("mkt grow start --dry-run", "先补齐启动输入")
    if ux_state == "READY":
        return (f"mkt grow arm {run_id}",
                "人工确认授权范围与配额后开闸（向导不会替你开）")
    return ("mkt grow doctor", "先处理体检阻断项")


def apply_start_plan(plan: Dict[str, Any], data_dir=None,
                     *, dry_run: bool = False) -> Dict[str, Any]:
    """把计划落成 run：不 Arm、不发送、不联网；dry_run=True 只回显计划。"""
    from .grow_v2 import GrowRunV2

    plan = dict(plan or {})
    if dry_run or plan.get("applied"):
        return dict(plan, applied=False, artifacts=plan.get("artifacts") or [])
    if not plan.get("can_apply"):
        raise RunbookError(plan.get("blocked_reason_code") or "INPUT_REQUIRED",
                           plan.get("next_required_action")
                           or "计划还不能落地：先补齐阻断项")
    options = dict(plan.get("options") or {})
    base = Path(data_dir or plan.get("data_dir") or ".")
    run_id = _text(options.get("run_id")) or plan["run_id"]
    path = run_path_for(base, run_id)
    if path.exists():
        raise RunbookError("INPUT_REQUIRED",
                           f"run {run_id!r} 已存在：先 mkt grow status {run_id}，"
                           "不要重复建")
    facts_state = _facts_state(options.get("facts_path"))
    if not facts_state.get("ok"):
        raise RunbookError("INPUT_REQUIRED",
                           facts_state.get("detail", "事实包不可用"))
    # 向导只建「计划」：不 arm、不执行。执行授权仍由 arm + 全部执行门禁负责，
    # 所以这里编译 RunSpec 而不走 launch 的 policy 预检（policy 状态已在步骤里
    # 单独判定；缺授权时 can_apply=False，根本到不了这里）。
    try:
        from .grow_compile import compile_run
        spec = compile_run(
            run_id=run_id, facts=facts_state["facts"],
            goal_id=options["goal_id"], channels=list(options["channels"]),
            target=options.get("target"),
            actions_per_week=int(options.get("actions_per_week") or 7),
            started=options.get("started") or _today(),
            owner=options.get("owner") or "",
            account=options.get("account") or "",
            handle=options.get("handle") or "",
            audience=options.get("audience") or "us-west",
            actions=options.get("actions") or None,
            data_dir=base)
        spec["facts_ref"] = options["facts_path"]
    except Exception as exc:  # noqa: BLE001 编译失败给可行动的错误
        raise RunbookError("INPUT_REQUIRED",
                           f"启动计划编译失败：{type(exc).__name__}: {exc}") from exc
    GrowRunV2(path).save(spec)
    GrowRunV2._write_briefs(base, spec)

    run = GrowRunV2(path)
    data = run.load()
    data["go"] = False
    data["status"] = "ready_to_arm" if plan.get("ready_to_arm") else "draft"
    data["onboarding"] = {
        "schema": plan.get("schema"),
        "generated_at": plan.get("generated_at"),
        "ux_state": plan.get("ux_state"),
        "steps": [{k: step.get(k) for k in
                   ("name", "title", "state", "detail", "next_action")}
                  for step in plan.get("steps") or []],
        "preview_count": len(plan.get("preview") or []),
        "arm_required": True,
    }
    data["delivery"] = dict(plan.get("delivery") or {})
    data.setdefault("history", []).append({
        "at": _today(), "kind": "onboarding",
        "note": (f"向导建计划（{data['status']}）：九步完成，开闸仍需人工 "
                 f"mkt grow arm {run_id}")})
    run.save(data)
    preview_path = base / "grow" / run_id / "start-preview.json"
    preview_path.parent.mkdir(parents=True, exist_ok=True)
    preview_path.write_text(json.dumps({
        "run": run_id,
        "generated_at": plan.get("generated_at"),
        "preview": plan.get("preview") or [],
        "note": "预览不是队列：要发必须先人工确认对象与内容并 mkt grow stage",
    }, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    artifacts = [str(path), str(preview_path)]
    schedule = (plan.get("schedule") or {}).get("mode") or "foreground"
    try:
        if schedule == "scheduled":
            installed = run.install(base, load=False)
            artifacts.extend([installed.get("path", ""),
                              installed.get("fallback", "")])
        else:
            artifacts.append(str(run.write_loop_script(base)))
    except Exception as exc:  # noqa: BLE001 调度文件失败不改变 run 状态
        artifacts.append(f"调度文件生成失败：{exc}")
    result = dict(plan, dry_run=False, applied=True, status=data["status"],
                  run_path=str(path), artifacts=[a for a in artifacts if a])
    return result


# ---------------------------------------------------------------------------
# 故障扫描：唯一事件 ID + 恢复建议
# ---------------------------------------------------------------------------

def _incident(run_id: str, kind: str, key: str, *, detail: str,
              severity: str, recovery: str, next_command: str,
              evidence: Optional[Dict[str, Any]] = None,
              detected_at: str = "") -> Dict[str, Any]:
    meta = _matrix(kind)
    return {
        "event_id": _incident_id(run_id, kind, key),
        "kind": kind,
        "title": meta["title"],
        "severity": severity,
        "detail": detail,
        "evidence": evidence or {},
        "recovery": recovery,
        "next_command": next_command,
        "error_code": meta["error_code"],
        "blocks_auto_retry": kind == "indeterminate_external_action",
        "detected_at": detected_at or _now(),
        "status": "open",
    }


def _row(code: str, state: str, detail: str, *,
         error_code: Optional[str] = None,
         event_ids: Sequence[str] = (),
         evidence: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    meta = _matrix(code)
    return {
        "code": code, "title": meta["title"], "state": state,
        "detail": detail, "handling": meta["handling"],
        "recovery": meta["recovery"],
        "error_code": error_code or meta["error_code"],
        "event_ids": list(event_ids), "evidence": evidence or {},
    }


def _read_ledger(data_dir) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    from .ledger import Ledger
    try:
        return Ledger(Path(data_dir) / "ledger.jsonl").all(), None
    except Exception as exc:  # noqa: BLE001 坏账本 fail closed，转成告警
        return [], f"{type(exc).__name__}: {exc}"


def _suppression_error(data_dir) -> Optional[str]:
    try:
        from .gates import load_suppressions
        load_suppressions(Path(data_dir))
        return None
    except Exception as exc:  # noqa: BLE001
        return f"{type(exc).__name__}: {exc}"


def _readings_state(data: Dict[str, Any]) -> Dict[str, Any]:
    days = ((data.get("telemetry") or {}).get("days") or {})
    latest_error = None
    latest_day = ""
    reading_days = 0
    for day, entry in sorted(days.items()):
        readings = (entry or {}).get("readings") or {}
        if readings.get("error"):
            latest_error = str(readings.get("error"))[:200]
            latest_day = str(day)
        if readings.get("followers") is not None:
            reading_days += 1
    return {"reading_days": reading_days, "days": len(days),
            "latest_error": latest_error, "latest_error_day": latest_day}


def _supply_state(run, data_dir, data: Dict[str, Any],
                  today: str) -> Dict[str, Any]:
    from .grow_v2 import GrowRunV2
    from .ledger import Ledger
    run_id = _text(data.get("id"))
    try:
        ledger = Ledger(Path(data_dir) / "ledger.jsonl")
        committed = GrowRunV2._committed_targets(ledger, data)
        state = run._supply_state(data, Path(data_dir), today, committed)
        queues = list(GrowRunV2._discover_queues(Path(data_dir), run_id))
        stale = 0
        for path in queues:
            queue = GrowRunV2._load_queue(path)
            stale += GrowRunV2._count_stale(queue, on=today,
                                            committed_targets=committed)
        return {"gap": int(state.get("gap") or 0),
                "staged": int(state.get("staged") or 0),
                "stale": stale, "note": state.get("note", "")}
    except Exception as exc:  # noqa: BLE001 供给检查失败不阻塞其余诊断
        return {"gap": 0, "staged": 0, "stale": 0,
                "note": f"供给检查跳过：{exc}"}


def _scan(data_dir, run_path=None, run_id: str = "", *,
          on: Optional[str] = None,
          login_probe: Optional[Callable[[str], Dict[str, Any]]] = None,
          now: Optional[str] = None) -> Dict[str, Any]:
    from . import grow_lock, timezone_windows as tw
    data_dir = Path(data_dir)
    run, data = _load_run(data_dir, run_path, run_id)
    run_id = _text(data.get("id"))
    today = on or _today()
    incidents: List[Dict[str, Any]] = []
    rows: List[Dict[str, Any]] = []
    by_kind: Dict[str, List[str]] = {}

    def add(incident: Dict[str, Any]) -> None:
        incidents.append(incident)
        by_kind.setdefault(incident["kind"], []).append(incident["event_id"])

    # --- 账本 / 抑制表 ---
    ledger_rows, ledger_error = _read_ledger(data_dir)
    suppression_error = _suppression_error(data_dir)
    corrupt = []
    if ledger_error:
        corrupt.append(("ledger", ledger_error))
    if suppression_error:
        corrupt.append(("suppression", suppression_error))
    if corrupt:
        for key, error in corrupt:
            add(_incident(
                run_id, "ledger_or_suppression_corrupt", key,
                detail=f"{key} 读不了：{error}",
                severity="blocking",
                recovery=("修好坏数据再恢复：坏行不能当没有，"
                          "修复后跑 mkt doctor 证明完整性"),
                next_command="mkt doctor",
                evidence={"source": key, "error": error},
                detected_at=now or _now()))
        rows.append(_row("ledger_or_suppression_corrupt", "blocked",
                         "账本/抑制表损坏，读写已 fail closed",
                         event_ids=by_kind["ledger_or_suppression_corrupt"]))
    else:
        rows.append(_row("ledger_or_suppression_corrupt", "ok",
                         "账本与抑制表可读"))

    # --- 未知回执（indeterminate） ---
    indeterminate = [rec for rec in ledger_rows
                     if _text(rec.get("result")) == "indeterminate"
                     and _belongs(_text(rec.get("campaign")), run_id)]
    for rec in indeterminate:
        key = _text(rec.get("ref")) or f"{rec.get('ts')}|{rec.get('target')}"
        ref = _text(rec.get("ref"))
        add(_incident(
            run_id, "indeterminate_external_action", key,
            detail=(f"动作结果未知：{rec.get('channel')}:{rec.get('action')} → "
                    f"{rec.get('target')}（可能已发出，禁止自动重试）"),
            severity="blocking",
            recovery=("人工到平台后台核对这条是否已发；"
                      f"mkt ledger resolve {ref or '<ref>'} --result "
                      "committed|failed --by <谁> --note <依据>，"
                      "再 mkt grow resume --after-reconciliation"),
            next_command=(f"mkt ledger resolve {ref} --result committed "
                          "--by <谁> --note <平台后台截图/链接>"
                          if ref else "人工核对账本记录缺少 ref 的那条"),
            evidence={"ref": ref, "target": _text(rec.get("target")),
                      "channel": _text(rec.get("channel")),
                      "action": _text(rec.get("action")),
                      "ts": _text(rec.get("ts"))},
            detected_at=now or _text(rec.get("ts")) or _now()))
    rows.append(_row(
        "indeterminate_external_action",
        "blocked" if indeterminate else "ok",
        (f"{len(indeterminate)} 条未知回执待人工裁决" if indeterminate
         else "没有未裁决的外部动作"),
        event_ids=by_kind.get("indeterminate_external_action", [])))

    # --- 授权 / 账号 ---
    from .execution_policy import get_policy
    policy = None
    policy_error = None
    try:
        policy = get_policy(data_dir)
    except Exception as exc:  # noqa: BLE001
        policy_error = str(exc)
    actions = [str(a) for a in data.get("actions") or []]
    account = _text((data.get("guards") or {}).get("account_id"))
    policy_problem = None
    if policy_error:
        policy_problem = f"授权文件读不了：{policy_error}"
    elif not isinstance(policy, dict) or not policy:
        policy_problem = "没有常驻授权：真实写会被拒"
    elif policy.get("revoked"):
        policy_problem = "授权已被撤销"
    else:
        expires_at = _text(policy.get("expires_at"))
        expiry = None
        if expires_at:
            try:
                expiry = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
            except ValueError:
                expiry = None
        if expiry is not None:
            current = datetime.now(expiry.tzinfo) if expiry.tzinfo else datetime.now()
            if current >= expiry:
                policy_problem = f"授权已过期：{expires_at}"
        if not policy_problem:
            allowed = {str(a) for a in policy.get("allow_actions") or []}
            missing = [a for a in actions if a not in allowed]
            if missing:
                policy_problem = "授权未覆盖动作：" + "、".join(missing)
    if policy_problem:
        add(_incident(
            run_id, "account_or_policy", "policy",
            detail=policy_problem, severity="blocking",
            recovery=("重新确认授权账号与身份后由人重新授权；"
                      "不要用旧的批准继续真实写"),
            next_command=('mkt policy enable --by "$USER" --actions '
                          + ",".join(sorted(actions or ["x:reply"]))
                          + " --days 30"),
            evidence={"status": data.get("status"), "actions": actions},
            detected_at=now or _now()))
        rows.append(_row("account_or_policy", "blocked", policy_problem,
                         event_ids=by_kind["account_or_policy"]))
    else:
        expiry = _text(policy.get("expires_at")) if isinstance(policy, dict) else ""
        rows.append(_row("account_or_policy", "ok",
                         f"授权有效（到期：{expiry or '长期'}）"))

    # --- 登录 / 浏览器（只有显式探测才下结论） ---
    channels = [str(c) for c in data.get("channels") or []]
    login_state = None
    if login_probe is not None and channels:
        login_state = _login_state(channels[0], login_probe)
        status = _text(login_state.get("status"))
        if status == "logged_out":
            add(_incident(
                run_id, "browser_unavailable", f"login:{channels[0]}",
                detail=f"{channels[0]} 浏览器登录态缺失，真实写已停",
                severity="blocking",
                recovery=(f"在调试 Chrome 里登录 {channels[0]} 后重跑 "
                          "mkt grow doctor，再恢复"),
                next_command="mkt grow doctor",
                evidence=login_state, detected_at=now or _now()))
        elif status == "cdp_unavailable":
            add(_incident(
                run_id, "browser_unavailable", "cdp",
                detail="调试浏览器不可用：" + _text(login_state.get("error")),
                severity="blocking" if data.get("go") else "warning",
                recovery=("重启调试 Chrome，重新做账号身份证明后再恢复；"
                          "读数标记降级，不伪造 0"),
                next_command="./launch.command",
                evidence=login_state, detected_at=now or _now()))
        elif status == "unknown":
            add(_incident(
                run_id, "browser_unavailable", "login-unknown",
                detail="登录态未确认：" + _text(login_state.get("error")),
                severity="warning",
                recovery="先确认调试浏览器停在该站点并已登录",
                next_command="mkt grow doctor",
                evidence=login_state, detected_at=now or _now()))
        browser_ids = by_kind.get("browser_unavailable", [])
        rows.append(_row(
            "browser_unavailable", "blocked" if browser_ids else "ok",
            (login_state.get("status") or "unknown")
            + ("：" + _text(login_state.get("error"))
               if login_state.get("error") else ""),
            event_ids=browser_ids))
    else:
        rows.append(_row("browser_unavailable", "unknown",
                         "未现场探测登录态（mkt grow doctor 或 incident --list --probe-login）"))

    # --- 读数 ---
    readings = _readings_state(data)
    if readings["latest_error"]:
        add(_incident(
            run_id, "readings_degraded", "reading-error",
            detail=(f"{readings['latest_error_day']} 读数失败："
                    f"{readings['latest_error']}；缺数写取不到，不伪造 0"),
            severity="warning",
            recovery=("恢复读数后核对；读数失败时缺数写取不到，不伪造 0；"
                      "必要时先只做不受读数约束的低风险活动"),
            next_command="mkt grow doctor",
            evidence={"error": readings["latest_error"],
                      "day": readings["latest_error_day"]},
            detected_at=now or _now()))
        rows.append(_row("readings_degraded", "warn",
                         f"读数失败：{readings['latest_error'][:80]}",
                         event_ids=by_kind["readings_degraded"]))
    elif (data.get("measurement") or {}).get("readers") and not readings["reading_days"]:
        add(_incident(
            run_id, "readings_degraded", "no-reading",
            detail="配置了读数账号但还没有任何有效读数；缺数不写 0",
            severity="warning",
            recovery="先跑 tick 读数；失败时保持取不到，不伪造 0",
            next_command="mkt grow doctor",
            evidence=readings, detected_at=now or _now()))
        rows.append(_row("readings_degraded", "warn", "还没有有效读数",
                         event_ids=by_kind["readings_degraded"]))
    else:
        rows.append(_row("readings_degraded", "ok",
                         f"{readings['reading_days']}/{max(1, readings['days'])} 天有读数"))

    # --- 供给 ---
    supply = _supply_state(run, data_dir, data, today)
    if supply["gap"] or supply["stale"]:
        add(_incident(
            run_id, "supply_shortage", "gap" if supply["gap"] else "stale",
            detail=(f"未来供给缺口 {supply['gap']} 条"
                    + (f"，另有 {supply['stale']} 条过期未发" if supply["stale"] else "")
                    + "；不用旧文案填补"),
            severity="warning",
            recovery="按需求单生产新内容，过审后 mkt grow stage 入队",
            next_command=f"mkt grow briefs {run_id}",
            evidence=supply, detected_at=now or _now()))
        rows.append(_row("supply_shortage", "warn", supply["note"] or "供给不足",
                         event_ids=by_kind["supply_shortage"]))
    else:
        rows.append(_row("supply_shortage", "ok", "未来供给充足"))

    # --- 重复启动 ---
    try:
        lock = grow_lock.lock_status(Path(run.path))
    except Exception as exc:  # noqa: BLE001
        lock = {"held": None, "note": str(exc)}
    if lock.get("held") is True:
        add(_incident(
            run_id, "duplicate_instance", "lock",
            detail="另一实例正在执行（单实例锁被占用）；本实例不执行",
            severity="warning",
            recovery=("另实例不执行、但必须可被识别与管理：确认它在跑什么；"
                      f"要接管先 mkt grow pause {run_id}，停稳后再 resume"),
            next_command=f"mkt grow pause {run_id}",
            evidence=lock, detected_at=now or _now()))
        rows.append(_row("duplicate_instance", "warn",
                         "另一实例持锁运行中",
                         event_ids=by_kind["duplicate_instance"]))
    elif lock.get("held") is None:
        rows.append(_row("duplicate_instance", "unknown",
                         lock.get("note") or "锁状态未知"))
    else:
        rows.append(_row("duplicate_instance", "ok", "单实例锁空闲"))

    # --- 时区 ---
    windows = data.get("windows") if isinstance(data.get("windows"), dict) else {}
    audience = _text(windows.get("audience")) or "us-west"
    iana = _text(windows.get("iana"))
    zone_ok = audience in tw.AUDIENCES
    if zone_ok and iana:
        zone_ok = tw.zone_for(audience, iana) is not None
    if not zone_ok:
        add(_incident(
            run_id, "timezone_switch", f"tz:{audience}",
            detail=f"窗口时区不可用：audience={audience!r}, iana={iana!r}",
            severity="blocking",
            recovery=("用 RunSpec 的 IANA 时区重算窗口（不要用本机时区顶替），"
                      "并校验时间戳与日配额"),
            next_command="mkt grow launch <id> --audience us-west ...",
            evidence={"audience": audience, "iana": iana,
                      "host_timezone": datetime.now().astimezone().tzinfo.__str__()},
            detected_at=now or _now()))
        rows.append(_row("timezone_switch", "blocked",
                         "窗口时区不可用",
                         event_ids=by_kind["timezone_switch"]))
    else:
        resolved = tw.audience_iana(audience, iana)
        rows.append(_row("timezone_switch", "ok",
                         f"窗口按 IANA {resolved} 计算",
                         evidence={"audience": audience, "iana": resolved}))

    return {"run": run_id, "rows": rows, "incidents": incidents,
            "blocking": sum(1 for i in incidents
                            if i["severity"] == "blocking"),
            "has_pending_external_effect": bool(indeterminate),
            "login_state": login_state}


def list_incidents(data_dir, run_path=None, *, run_id: str = "",
                   on: Optional[str] = None,
                   login_probe: Optional[Callable[[str], Dict[str, Any]]] = None,
                   now: Optional[str] = None) -> Dict[str, Any]:
    scan = _scan(data_dir, run_path, run_id, on=on,
                 login_probe=login_probe, now=now)
    return {"run": scan["run"], "incidents": scan["incidents"],
            "count": len(scan["incidents"]), "blocking": scan["blocking"],
            "has_pending_external_effect": scan["has_pending_external_effect"]}


def diagnose(data_dir, run_path=None, *, run_id: str = "",
             on: Optional[str] = None,
             login_probe: Optional[Callable[[str], Dict[str, Any]]] = None,
             now: Optional[str] = None) -> Dict[str, Any]:
    scan = _scan(data_dir, run_path, run_id, on=on,
                 login_probe=login_probe, now=now)
    return {"run": scan["run"], "rows": scan["rows"],
            "blocking": scan["blocking"], "incidents": scan["incidents"]}


# ---------------------------------------------------------------------------
# 状态解释（C-01 六字段）
# ---------------------------------------------------------------------------

def _last_updated(data: Dict[str, Any], run_path: Path) -> str:
    stamps: List[str] = []
    for row in data.get("history") or []:
        if isinstance(row, dict) and row.get("at"):
            stamps.append(_text(row["at"]))
    for row in data.get("decisions") or []:
        if isinstance(row, dict) and row.get("at"):
            stamps.append(_text(row["at"]))
    heartbeat = (data.get("heartbeat") or {}).get("last_tick")
    if heartbeat:
        stamps.append(_text(heartbeat))
    stamps = [s for s in stamps if s]
    if stamps:
        return max(stamps)
    try:
        return datetime.fromtimestamp(
            Path(run_path).stat().st_mtime).isoformat(timespec="seconds")
    except OSError:
        return ""


def _quota_remaining(data_dir, data: Dict[str, Any], on: str) -> Dict[str, Any]:
    from . import grow_schedule as gs
    from .ledger import Ledger
    run_id = _text(data.get("id"))
    week = gs.week_start_of(on, _text(data.get("started")) or on)
    daily = gs.split_week(int((data.get("quota") or {}).get("actions_per_week") or 0),
                          week, (data.get("quota") or {}).get("action_weights"))
    today_quota = daily.get(on, {})
    used: Dict[str, int] = {}
    try:
        for rec in Ledger(Path(data_dir) / "ledger.jsonl").all():
            if _text(rec.get("result")) != "committed":
                continue
            if not _belongs(_text(rec.get("campaign")), run_id):
                continue
            if _text(rec.get("ts"))[:10] != on:
                continue
            action = _text(rec.get("action")) or "?"
            used[action] = used.get(action, 0) + 1
    except Exception:  # noqa: BLE001 账本坏了由告警负责，这里不崩
        used = {}
    planned = sum(int(v) for v in today_quota.values())
    done = sum(used.values())
    return {"date": on, "planned": planned, "used": done,
            "remaining": max(0, planned - done), "by_action": today_quota,
            "used_by_action": used}


def _data_coverage(data_dir, data: Dict[str, Any]) -> Dict[str, Any]:
    readings = _readings_state(data)
    source = False
    try:
        from . import outcome_layers
        source = bool(outcome_layers.has_authorized_imports(
            Path(data_dir), run_id=_text(data.get("id"))))
    except Exception:  # noqa: BLE001
        source = False
    return {"reading_days": readings["reading_days"],
            "telemetry_days": readings["days"],
            "product_event_source": source,
            "note": ("业务事件源未接入：激活只报 unknown，不写 0" if not source
                     else "业务事件源已接入")}



#: C-01-Lite 状态词表（冻结，见 ai输出/ovo-C01-Lite-最小委托契约-v1-20261115.md）。
_DELEGATION_STATE = {
    "draft": "NEEDS_INPUT",
    "ready_to_arm": "READY",
    "active": "READY",
    "needs_attention": "NEEDS_REVIEW",
    "paused": "BLOCKED",
    "completed": "DELIVERED",
    "cancelled": "BLOCKED",
}


def delegation_block(*, run_id, canonical_status, next_actor, next_action,
                     blocked_code, result_ref, scope, recovery_entry, gaps=0):
    """C-01-Lite 最小委托投影：八个字段 + 一句边界。只投影，不改状态。"""
    mapped = _DELEGATION_STATE.get(str(canonical_status or ""), "BLOCKED")
    if mapped == "READY" and int(gaps or 0):
        mapped = "PARTIAL"
    return {
        "task_id": str(run_id or ""),
        "task_state": mapped,
        "next_actor": str(next_actor or ""),
        "next_required_action": str(next_action or ""),
        "blocked_reason_code": blocked_code,
        "result_ref": result_ref,
        "authorization_scope": scope,
        "recovery_entry": recovery_entry,
        "delegation_boundary": ("投影只说明这次运行推进到哪一步；DELIVERED 不等于"
                                "获得用户或收入，L3 UNKNOWN 不等于 0"),
    }


def explain_status(data_dir, run_path=None, *, run_id: str = "",
                   on: Optional[str] = None,
                   login_probe: Optional[Callable[[str], Dict[str, Any]]] = None,
                   now: Optional[str] = None) -> Dict[str, Any]:
    """状态解释：canonical 状态 + C-01 字段 + 下一步命令。只读。"""
    from .grow_v2 import GrowRunV2

    data_dir = Path(data_dir)
    run, data = _load_run(data_dir, run_path, run_id)
    on = on or _today()
    scan = _scan(data_dir, run.path, on=on,
                 login_probe=login_probe, now=now)
    incidents = scan["incidents"]
    blocking = [i for i in incidents if i["severity"] == "blocking"]
    pending = scan["has_pending_external_effect"]
    indeterminate = [i for i in incidents
                     if i["kind"] == "indeterminate_external_action"]
    status = _text(data.get("status"))
    if status in ("paused", "cancelled"):
        ux_state = "BLOCKED"
    elif blocking:
        ux_state = "BLOCKED"
    elif status == "draft":
        ux_state = "NEEDS_INPUT"
    elif status == "ready_to_arm":
        ux_state = "NEEDS_INPUT"
    elif status == "needs_attention":
        ux_state = "NEEDS_REVIEW"
    elif status == "completed":
        ux_state = "DELIVERED"
    else:
        ux_state = "READY"
    next_actor = "none" if status == "completed" else (
        "system" if status == "active" and not blocking and not pending
        else "human")
    try:
        goal_status = GrowRunV2._goal_progress(data, data_dir)
    except Exception:  # noqa: BLE001
        goal_status = {"metric": (data.get("goal") or {}).get("metric"),
                       "current": None, "note": "目标进度读取失败"}
    artifacts = [str(run.path)]
    preview = Path(data_dir) / "grow" / _text(data.get("id")) / "start-preview.json"
    if preview.is_file():
        artifacts.append(str(preview))
    cards = Path(data_dir) / "grow" / _text(data.get("id")) / "cards"
    if cards.is_dir():
        artifacts.extend(str(p) for p in sorted(cards.glob("*"))[-4:])
    run_id = _text(data.get("id"))
    if indeterminate:
        incident = indeterminate[0]
        blocked_code = "INDETERMINATE_EXTERNAL_ACTION"
        next_action = (f"先核对平台回执 {incident['event_id']}，再恢复："
                       f"mkt grow resume {run_id} --after-reconciliation "
                       f"{incident['event_id']} --by <operator>")
        next_command = (f"mkt grow resume {run_id} --after-reconciliation "
                        f"{incident['event_id']} --by <operator>")
    elif blocking:
        incident = blocking[0]
        blocked_code = incident["error_code"]
        next_action = f"{incident['title']}：{incident['recovery']}"
        next_command = incident["next_command"]
    elif status in ("ready_to_arm", "draft"):
        blocked_code = "AUTH_REQUIRED" if status == "ready_to_arm" else "INPUT_REQUIRED"
        next_action = ("人工确认授权范围与配额后开闸（向导不会替你开）"
                       if status == "ready_to_arm"
                       else "先补齐登录/授权/身份，再 arm")
        next_command = (f"mkt grow arm {run_id}" if status == "ready_to_arm"
                        else "mkt grow doctor")
    elif status in ("paused", "needs_attention"):
        blocked_code = "REVIEW_REQUIRED"
        next_action = "人工确认后恢复：mkt grow resume <id> --by <operator> --note <处理说明>"
        next_command = f"mkt grow resume {run_id} --by <operator> --note <处理说明>"
    elif pending:
        blocked_code = "INDETERMINATE_EXTERNAL_ACTION"
        next_action = "有未裁决的外部动作：先 mkt grow incident <id> --list"
        next_command = f"mkt grow incident {run_id} --list"
    else:
        blocked_code = None
        decisions = [d for d in data.get("decisions") or []
                     if isinstance(d, dict) and d.get("on") == on]
        if not decisions and status == "active":
            next_action = "今天还没有决定：默认继续（mkt grow decide <id> --action continue）"
            next_command = f"mkt grow decide {run_id} --action continue"
        elif status == "active":
            next_action = "无需动作：调度器下一轮继续；先看日卡再决定"
            next_command = f"mkt grow tick {run_id}"
        else:
            next_action = status
            next_command = f"mkt grow status {run_id}"
    return {
        "schema": "growovo.status-explain/1",
        "run": run_id,
        "canonical_status": status,
        "ux_state": ux_state,
        "go": bool(data.get("go")),
        "next_actor": next_actor,
        "next_required_action": next_action,
        "next_command": next_command,
        "blocked_reason_code": blocked_code,
        "delegation": delegation_block(
            run_id=run_id, canonical_status=status, next_actor=next_actor,
            next_action=next_action, blocked_code=blocked_code,
            result_ref=(artifacts[0] if artifacts else None),
            scope={"actions": (data.get("quota") or {}).get("actions")
                   if isinstance(data.get("quota"), dict) else None,
                   "account_id": str((data.get("guards") or {}).get("account_id") or "")},
            recovery_entry=(next_command if status in ("paused", "needs_attention", "cancelled")
                            else None),
            gaps=len((data.get("supply_state") or {}).get("gap_reasons") or [])),
        "last_updated_at": _last_updated(data, run.path),
        "has_pending_external_effect": bool(pending),
        "artifact_refs": artifacts,
        "goal_status": goal_status,
        "quota_remaining": _quota_remaining(data_dir, data, on),
        "observation_window": {
            "start": (data.get("goal") or {}).get("starts"),
            "end": (data.get("goal") or {}).get("ends"),
            "days": (data.get("goal") or {}).get("window_days"),
            "days_observed": len((data.get("telemetry") or {}).get("days") or {}),
        },
        "data_coverage": _data_coverage(data_dir, data),
        "incidents": incidents,
        "faults": scan["rows"],
        "checks": [{"name": row["code"], "state": row["state"],
                    "detail": row["detail"], "fix": row["recovery"]}
                   for row in scan["rows"]],
    }


# ---------------------------------------------------------------------------
# 恢复：必须先核对外部动作
# ---------------------------------------------------------------------------

def _resolved_ledger_record(data_dir, run_id: str, incident_id: str
                            ) -> Optional[Dict[str, Any]]:
    """事件已从列表消失时，按稳定 ID 找回它（对应账本行必须已裁决）。"""
    from .ledger import Ledger
    ledger = Ledger(Path(data_dir) / "ledger.jsonl")
    try:
        resolved = {r.get("ref"): r for r in ledger.all() if r.get("ref")}
        raw = ledger._raw()
    except Exception:  # noqa: BLE001
        return None
    for rec in raw:
        if _text(rec.get("result")) != "indeterminate":
            continue
        if not _belongs(_text(rec.get("campaign")), run_id):
            continue
        key = _text(rec.get("ref")) or f"{rec.get('ts')}|{rec.get('target')}"
        if _incident_id(run_id, "indeterminate_external_action", key) != incident_id:
            continue
        current = resolved.get(rec.get("ref")) or rec
        return {
            "event_id": incident_id, "kind": "indeterminate_external_action",
            "title": _matrix("indeterminate_external_action")["title"],
            "severity": "blocking", "status": "resolved",
            "detail": "外部动作已人工裁决",
            "recovery": "已裁决", "next_command": "",
            "error_code": "INDETERMINATE_EXTERNAL_ACTION",
            "blocks_auto_retry": True,
            "evidence": {"ref": rec.get("ref"),
                         "result": _text(current.get("result")),
                         "resolved_by": _text(current.get("resolved_by"))},
            "detected_at": _text(rec.get("ts")) or _now(),
        }
    return None


def _require_resolution(data_dir, incident: Dict[str, Any]) -> None:
    ref = _text((incident.get("evidence") or {}).get("ref"))
    from .ledger import Ledger
    ledger = Ledger(Path(data_dir) / "ledger.jsonl")
    try:
        rows = ledger.all()
    except Exception as exc:  # noqa: BLE001
        raise RunbookError("INTERNAL_ERROR",
                           f"账本读不了，无法确认裁决：{exc}") from exc
    for rec in rows:
        if ref and _text(rec.get("ref")) == ref:
            if _text(rec.get("result")) != "indeterminate" and _text(
                    rec.get("resolved_by")):
                return
            break
    raise RunbookError(
        "INDETERMINATE_EXTERNAL_ACTION",
        ("这条外部动作结果未知，未裁决前不能恢复，也不能重试。先人工核对平台后台："
         f"mkt ledger resolve {ref} --result committed|failed --by <谁> "
         "--note <依据>；账本裁决后再"
         f"mkt grow resume <id> --after-reconciliation {incident['event_id']} "
         "--by <operator>"))


def blocking_incidents(data_dir, run_path=None, *, on: Optional[str] = None):
    """arm/resume 共用的阻塞清单：只有 blocking 才拦，warning 不拦。"""
    report = list_incidents(data_dir, run_path, on=on)
    return [row for row in report["incidents"]
            if row.get("severity") == "blocking" and not row.get("resolved")]


def arm_run(data_dir, run_path, *, by: str = "", on: Optional[str] = None
            ) -> Dict[str, Any]:
    """显式人工开闸：署名 + 有效授权 + 无 blocking 告警，缺一不可。

    与 resume 分开：arm 不消费 indeterminate，也不改任何事件；它只是把
    ready_to_arm/未开闸的 run 推到 active，并留下谁在何时按下的记录。
    """
    from .grow_v2 import GrowRunV2
    operator = _text(by)
    if not operator:
        raise RunbookError(
            "AUTH_REQUIRED",
            "arm 必须带 --by <operator> 署名；开闸是人的决定，Agent 不能自签")
    run = GrowRunV2(Path(run_path))
    data = run.load()
    blocking = blocking_incidents(data_dir, Path(run_path), on=on)
    if blocking:
        first = blocking[0]
        details = "；".join(
            f"{row['event_id']} {row['title']}（{row['recovery']}）"
            for row in blocking[:3])
        raise RunbookError(
            first.get("error_code") or "REVIEW_REQUIRED",
            f"还有 {len(blocking)} 条阻塞告警，不能开闸：{details}")
    return run.arm(by=operator, data_dir=Path(data_dir))


def resume_run(data_dir, run_path, *, by: str = "", note: str = "",
               after_reconciliation: str = "", on: Optional[str] = None
               ) -> Dict[str, Any]:
    """resume 的唯一入口：默认路径也必须先过 indeterminate/blocking 扫描。

    - 带 --after-reconciliation：走人工核对路径（要求该事件已 resolved）；
    - 不带：先看有没有 blocking 告警；有就拒绝并给出下一步，不碰 status；
    - 只有干净（warning 可留）才回到 active。
    """
    if _text(after_reconciliation):
        return resume_after_reconciliation(
            data_dir, run_path, after_reconciliation, by=by, note=note)
    from .grow_v2 import GrowRunV2
    operator = _text(by)
    if not operator:
        raise RunbookError(
            "AUTH_REQUIRED", "resume 必须署名（--by）：恢复是人的决定")
    run = GrowRunV2(Path(run_path))
    data = run.load()
    if data.get("status") not in ("needs_attention", "paused"):
        return {"resumed": False, "status": data.get("status"),
                "reason": "当前状态不需要恢复"}
    blocking = blocking_incidents(data_dir, Path(run_path), on=on)
    if blocking:
        first = blocking[0]
        code = first.get("error_code") or "REVIEW_REQUIRED"
        details = "；".join(
            f"{row['event_id']} {row['title']}（{row['recovery']}）"
            for row in blocking[:3])
        hint = (f"mkt grow resume {data.get('id')} --after-reconciliation "
                f"{first['event_id']} --by <operator> --note <依据>")
        next_step = _text(first.get("next_command"))
        raise RunbookError(
            code,
            f"还有 {len(blocking)} 条阻塞告警，不能恢复：{details}。"
            f"先执行：{next_step or hint}；核对后再 {hint}")
    out = run.resume(note=note, by=operator)
    return {"resumed": True, "status": out.get("status"),
            "go": out.get("go"), "run": out.get("id"),
            "note": "警告级告警不拦恢复；暂停闸与执行门照旧生效"}


def resume_after_reconciliation(data_dir, run_path=None, incident_id: str = "",
                                *, by: str, run_id: str = "", note: str = "",
                                now: Optional[str] = None) -> Dict[str, Any]:
    """恢复 needs_attention/paused 的 run；indeterminate 必须先有裁决记录。"""
    from .grow_v2 import GrowRunV2

    data_dir = Path(data_dir)
    by = _text(by)
    if not by:
        raise RunbookError("INPUT_REQUIRED",
                           "恢复必须由人署名（--by）：机器不能替人确认外部动作")
    incident_id = _text(incident_id)
    if not incident_id:
        raise RunbookError(
            "INPUT_REQUIRED",
            "resume 需要 --after-reconciliation <事件 ID>：先 mkt grow incident "
            "<run> --list 拿到 INC-…")
    run, data = _load_run(data_dir, run_path, run_id)
    status = _text(data.get("status"))
    if status not in ("needs_attention", "paused"):
        return {"resumed": False, "status": status, "incident": None,
                "reason": f"当前 status={status}，不需要恢复"}
    scan = _scan(data_dir, run.path, now=now)
    incidents = scan["incidents"]
    target = next((i for i in incidents if i["event_id"] == incident_id), None)
    if target is None:
        target = _resolved_ledger_record(data_dir, _text(data.get("id")),
                                         incident_id)
        if target is None:
            raise RunbookError(
                "INPUT_REQUIRED",
                f"没有这个事件 ID：{incident_id}；先 mkt grow incident "
                f"{_text(data.get('id'))} --list 确认")
    elif target.get("blocks_auto_retry"):
        _require_resolution(data_dir, target)
    elif target.get("severity") == "blocking":
        raise RunbookError(
            target["error_code"],
            f"{target['title']} 还没处理：{target['recovery']}")
    remaining = [i for i in incidents
                 if i.get("severity") == "blocking"
                 and i["event_id"] != incident_id]
    if remaining:
        other = remaining[0]
        raise RunbookError(
            "REVIEW_REQUIRED",
            f"还有未处理的阻断告警 {other['event_id']}（{other['title']}）："
            f"先处理它：{other['next_command']}")
    data["status"] = "active"
    data.setdefault("history", []).append({
        "at": _today(), "kind": "resume", "by": by,
        "note": _text(note)[:300] or "人工核对后恢复",
        "after_reconciliation": incident_id})
    run.save(data)
    return {"resumed": True, "status": "active", "incident": target,
            "next_action": "已恢复 active；暂停总闸与执行门禁照旧生效",
            "external_actions_unchanged": True}


# ---------------------------------------------------------------------------
# 人读渲染
# ---------------------------------------------------------------------------

_STATE_LABELS = {"ok": "就绪", "needs_input": "缺输入",
                 "needs_review": "需人审", "blocked": "阻断",
                 "pending_human": "等人开闸"}


def render_start_plan(plan: Dict[str, Any]) -> str:
    lines = [f"# 启动向导 · {plan.get('run_id')}（{plan.get('ux_state')}）", ""]
    for index, step in enumerate(plan.get("steps") or [], 1):
        mark = _STATE_LABELS.get(step.get("state"), step.get("state"))
        lines.append(f"{index}. {step.get('title')}｜{mark}")
        lines.append(f"   {step.get('detail')}")
        if step.get("next_action"):
            lines.append(f"   下一步：{step['next_action']}")
    lines += ["", "## 首批 3 条候选预览（未入队、不可直发）"]
    for row in plan.get("preview") or []:
        lines.append(f"{row['index']}. [{row['formula']}] {row['target_hint']}")
        lines.append(f"   问题：{row['their_problem']}")
        lines.append(f"   价值：{row['why_we_help']}｜期望动作：{row['expected_action']}")
        lines.append(f"   写法：{row['draft_angle']}｜风险：{row['risk']}")
    risk = plan.get("risk") or {}
    lines += ["", "## 风险与配额",
              f"- {risk.get('note', '')}",
              f"- 周配额 {risk.get('actions_per_week')}｜单日上限 "
              f"{risk.get('max_per_day')}｜自动扩量 "
              f"{'开' if risk.get('auto_scale') else '关'}",
              f"- 红线：{'、'.join(risk.get('red_lines') or []) or '（facts 未写）'}"]
    lines += ["", "## 下一步", f"- {plan.get('next_required_action')}",
              f"- 命令：{plan.get('next_command')}"]
    if not plan.get("applied"):
        lines += ["", "（未写入：确认无误后加 --apply 建计划；开闸仍需 mkt grow arm）"]
    return "\n".join(lines)


def render_incidents(report: Dict[str, Any]) -> str:
    incidents = report.get("incidents") or []
    lines = [f"# 运行告警 · {report.get('run')}（{len(incidents)} 条）", ""]
    if not incidents:
        lines.append("没有告警。")
        return "\n".join(lines)
    for incident in incidents:
        lines.append(f"- {incident['event_id']}｜{incident['title']}"
                     f"｜{incident['severity']}")
        lines.append(f"  详情：{incident['detail']}")
        lines.append(f"  恢复建议：{incident['recovery']}")
        if incident.get("next_command"):
            lines.append(f"  命令：{incident['next_command']}")
    return "\n".join(lines)


def render_status(explain: Dict[str, Any]) -> str:
    quota = explain.get("quota_remaining") or {}
    window = explain.get("observation_window") or {}
    coverage = explain.get("data_coverage") or {}
    lines = [
        f"# 状态解释 · {explain.get('run')}",
        "",
        f"- canonical 状态：{explain.get('canonical_status')}"
        f"（UX：{explain.get('ux_state')}）｜实跑 go={explain.get('go')}",
        f"- 责任人：{explain.get('next_actor')}",
        f"- 下一动作：{explain.get('next_required_action')}",
        f"- 命令：{explain.get('next_command')}",
        f"- 阻断码：{explain.get('blocked_reason_code') or '无'}",
        f"- 未决外部动作：{'有' if explain.get('has_pending_external_effect') else '无'}",
        f"- 最后更新：{explain.get('last_updated_at') or '取不到'}",
        f"- 今日配额：计划 {quota.get('planned')}｜已发 {quota.get('used')}"
        f"｜剩余 {quota.get('remaining')}",
        f"- 观测窗口：{window.get('start')} ~ {window.get('end')}"
        f"｜已观测 {window.get('days_observed')} 天",
        f"- 数据覆盖：有读数 {coverage.get('reading_days')} 天"
        f"｜业务事件源 {'已接入' if coverage.get('product_event_source') else '未接入'}",
    ]
    incidents = explain.get("incidents") or []
    if incidents:
        lines += ["", "## 告警"]
        for incident in incidents:
            lines.append(f"- {incident['event_id']}｜{incident['title']}"
                         f"｜{incident['severity']}")
            lines.append(f"  {incident['recovery']}")
    lines += ["", "## 八行故障矩阵"]
    for row in explain.get("faults") or []:
        lines.append(f"- {row['code']}｜{row['state']}：{row['detail']}")
    lines += ["", "说明：canonical 状态沿用 run 文件；这里只做只读投影，不覆盖原状态。", ""]
    return "\n".join(lines)
