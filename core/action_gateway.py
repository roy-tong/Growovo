from __future__ import annotations

import threading
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


def _start_heartbeat(data_dir, account, route, owner):
    stop = threading.Event()

    def renew():
        while not stop.wait(LEASE_HEARTBEAT_SECONDS):
            try:
                if not growth_runtime.try_acquire_execution_lane(
                        data_dir, account, route, owner, lease_seconds=LEASE_SECONDS):
                    return
            except Exception:
                continue

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
    heartbeat = _start_heartbeat(data_dir, account, route, owner)
    try:
        yield
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
    heartbeat = _start_heartbeat(data_dir, account, route, owner)
    try:
        return run_action(**kwargs)
    finally:
        _stop_heartbeat(heartbeat)
        growth_runtime.release_execution_lane(data_dir, account, route, owner)
