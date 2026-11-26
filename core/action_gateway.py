from __future__ import annotations

import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from . import growth_runtime

# X publish/reply 有两个历史实现：vendor CDP（调试浏览器 9222）和
# browser-attach（WebBridge 受管标签）。默认只允许一条：已登录的操作者
# 浏览器。调试浏览器只能由调用方显式选择 legacy_cdp=True / transport="cdp"，
# 避免身份探测和实际执行落在不同浏览器。
_X_BROWSER_ATTACH_ACTIONS = {"publish", "reply"}
LEASE_SECONDS = 300
LEASE_HEARTBEAT_SECONDS = 60


class AccountBusy(RuntimeError):
    def __init__(self, account, route):
        super().__init__(f"账号忙：{account}/{route} 正在执行另一个写动作，稍后重试")
        self.account = account
        self.route = route


class LeaseLost(RuntimeError):
    """租约已被他人取得或本进程已丢失；写动作必须停。"""
    code = "lease_lost"

    def __init__(self, account, route, detail=""):
        self.account = account
        self.route = route
        self.detail = detail
        super().__init__(f"执行租约已丢失：{account}/{route} {detail}".strip())


class LeaseState:
    """执行者持有的租约视图：fence 不匹配或心跳判定丢失后禁止继续写。"""

    def __init__(self, data_dir, account, route, owner, fence):
        self.data_dir = Path(data_dir)
        self.account = account
        self.route = route
        self.owner = owner
        self.fence = int(fence)
        self.lost = False
        self.reason = ""
        self._lock = threading.Lock()

    def mark_lost(self, reason=""):
        with self._lock:
            if not self.lost:
                self.lost = True
                self.reason = str(reason or "")

    def ensure(self):
        if self.lost:
            raise LeaseLost(self.account, self.route, self.reason or "heartbeat_lost")
        state = growth_runtime.execution_lane_state(self.data_dir, self.account, self.route)
        if (not state or state["owner"] != self.owner or state["fence"] != self.fence):
            self.mark_lost("lease_changed")
            raise LeaseLost(self.account, self.route, "lease_changed")
        return True


def resolve_transport(kwargs):
    """返回补齐默认传输后的 kwargs；不修改调用方字典。"""
    kwargs = dict(kwargs)
    channel = str(kwargs.get("channel") or "").strip().lower()
    action = str(kwargs.get("action") or "").strip().lower()
    transport = str(kwargs.get("transport") or "").strip().lower()
    legacy = bool(kwargs.get("legacy_cdp", False))
    if channel == "x" and action in _X_BROWSER_ATTACH_ACTIONS:
        if legacy or transport in {"cdp", "legacy-cdp"}:
            transport = ""
        else:
            # routine X 写也不再默认走 9222：没有 owner-attested 时会明确报
            # authorization_missing，而不是静默改用 routine CDP。
            if kwargs.get("routine"):
                kwargs["routine"] = False
            if not transport:
                transport = "browser-attach"
    elif transport in {"cdp", "legacy-cdp"}:
        transport = ""
    kwargs["transport"] = transport
    return kwargs


def _start_heartbeat(data_dir, account, route, owner, state=None):
    """续租线程。租约被抢占立即标记丢失；异常时等到租约自然过期再标丢失。"""
    stop = threading.Event()
    last_ok = [time.monotonic()]

    def renew():
        while not stop.wait(LEASE_HEARTBEAT_SECONDS):
            try:
                renewed = growth_runtime.try_acquire_execution_lane(
                    data_dir, account, route, owner, lease_seconds=LEASE_SECONDS)
            except Exception:
                renewed = None
            if renewed:
                last_ok[0] = time.monotonic()
                continue
            if renewed is False or time.monotonic() - last_ok[0] >= LEASE_SECONDS:
                if state is not None:
                    state.mark_lost("lease_taken" if renewed is False else "renewal_failed")
                return

    thread = threading.Thread(target=renew, daemon=True)
    thread.start()
    return stop, thread


def _stop_heartbeat(heartbeat):
    stop, thread = heartbeat
    stop.set()
    thread.join()


@contextmanager
def lease(data_dir, *, account="default", route="unknown"):
    """给仍直接调用渠道实现的 CLI 快捷入口加同一把执行租约。"""
    data_dir = Path(data_dir)
    owner = f"{account}:{route}:{uuid.uuid4().hex}"
    if not growth_runtime.try_acquire_execution_lane(
            data_dir, account, route, owner, lease_seconds=LEASE_SECONDS):
        raise AccountBusy(account, route)
    lane = growth_runtime.execution_lane_state(data_dir, account, route) or {}
    state = LeaseState(data_dir, account, route, owner, lane.get("fence") or 0)
    heartbeat = _start_heartbeat(data_dir, account, route, owner, state=state)
    try:
        yield state
    finally:
        _stop_heartbeat(heartbeat)
        growth_runtime.release_execution_lane(data_dir, account, route, owner)


def execute(**kwargs):
    from .action_runner import run_action

    kwargs = resolve_transport(kwargs)
    data_dir = Path(kwargs["data_dir"])
    account = str(kwargs.get("account") or "default")
    route = str(kwargs.get("channel") or kwargs.get("transport") or "unknown")
    owner = f"{account}:{route}:{uuid.uuid4().hex}"
    acquired = growth_runtime.try_acquire_execution_lane(
        data_dir, account, route, owner, lease_seconds=LEASE_SECONDS)
    if not acquired:
        return {"ok": False, "result": "account_busy", "retryable": True,
                "account": account, "route": route}
    lane = growth_runtime.execution_lane_state(data_dir, account, route) or {}
    state = LeaseState(data_dir, account, route, owner, lane.get("fence") or 0)
    heartbeat = _start_heartbeat(data_dir, account, route, owner, state=state)
    try:
        from .ledger import bind_execution
        action_id = str(kwargs.pop("growth_action_id", "") or "")
        with bind_execution(action_id=action_id,
                            attempt_id=str(kwargs.get("attempt_id") or ""), account=account):
            return run_action(lease_guard=state, **kwargs)
    except Exception as exc:
        if getattr(exc, "code", "") == "lease_lost":
            return {"ok": False, "result": "lease_lost", "retryable": False,
                    "account": account, "route": route, "detail": str(exc)}
        raise
    finally:
        _stop_heartbeat(heartbeat)
        growth_runtime.release_execution_lane(data_dir, account, route, owner)
