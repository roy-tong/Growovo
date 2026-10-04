#!/usr/bin/env python3
"""os-input 通道 —— 验证等级：UNVERIFIED（05 产品方案 §4.3）。

macOS OS 级键鼠/Accessibility 模拟，供无浏览器界面或 CDP 不可用/被检测的场景兜底。
实现为 AppleScript（osascript → System Events）子进程封装；不装任何依赖。

与 browser-cdp 的本质差别：没有 DOM 选择器——元素定位靠 Accessibility 树或坐标，
因此**每一步必须截图留证**（core.evidence），且仅限目标 App 处于前台激活态时执行。

全部流程未经实测。坐标点击需要 cliclick（brew install cliclick）或 pyautogui，
本骨架先提供 AppleScript 可达的原语（激活/按键/输入/截图），坐标点击留接口。
"""
import subprocess
import time
from pathlib import Path


def _osascript(script, timeout=15):
    r = subprocess.run(["osascript", "-e", script], capture_output=True,
                       text=True, timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError(f"osascript 失败: {r.stderr.strip()[:200]}")
    return r.stdout.strip()


def activate(app_name):
    """把目标 App 带到前台（一切 os-input 操作的前置条件）。"""
    _osascript(f'tell application "{app_name}" to activate')
    time.sleep(1.0)


def frontmost_is(app_name):
    """确认当前前台就是目标 App（防误操作系统级 UI 的第一道闸）。"""
    got = _osascript('tell application "System Events" to get name of first process '
                     'whose frontmost is true')
    return got.lower() == app_name.lower()


def press_key(key_code, modifiers=()):
    """按键（key code，如 36=Return, 48=Tab, 126=Up）；modifiers: {"cmd","shift","opt","ctrl"}。"""
    mods = "".join(f"{m} down " for m in modifiers)
    _osascript(f'tell application "System Events" to key code {key_code}'
               + (f" using {{{mods.strip()}}}" if mods else ""))


def type_text(text, delay_after=0.4):
    """逐段键入（keystroke；人类节奏由调用方配合 Throttle 控制）。"""
    for chunk in _chunks(text):
        escaped = chunk.replace('"', '\\"')
        _osascript(f'tell application "System Events" to keystroke "{escaped}"')
    time.sleep(delay_after)


def _chunks(text, n=60):
    """AppleScript keystroke 长度不稳，按 60 字符分段。"""
    return [text[i:i + n] for i in range(0, len(text), n)] or [""]


def click_at(x, y):
    """坐标点击。需要 cliclick（brew install cliclick）——本骨架不静默失败。"""
    try:
        subprocess.run(["cliclick", f"c:{int(x)},{int(y)}"], check=True, timeout=5)
    except FileNotFoundError as exc:
        raise RuntimeError("坐标点击需要 cliclick（brew install cliclick）") from exc


def screenshot(path):
    """全屏截图留证（os-input 通道的强制留证手段）。"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["screencapture", "-x", str(p)], check=True, timeout=10)
    return p


def guard_run(app_name, fn, *args, shots_dir, label="os-input", **kwargs):
    """os-input 操作的统一守卫：确认前台 → 执行 → 截图留证。"""
    if not frontmost_is(app_name):
        raise RuntimeError(f"前台不是 {app_name}，拒绝执行（防误操作系统级 UI）")
    result = fn(*args, **kwargs)
    screenshot(Path(shots_dir) / f"{label}-{int(time.time())}.png")
    return result
