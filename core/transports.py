#!/usr/bin/env python3
"""Transport 通道注册表 —— 多种操作平台方式的显式抽象（05 产品方案 §4）。

四通道：api / browser-cdp / os-input / protocol。
能力矩阵给每个 渠道×动作 声明"首选通道 + 降级链"；降级是内核内的确定性规则，
规划层无权指定执行通道（只说做什么，不说怎么连）。
"""

TRANSPORTS = {
    "api": {
        "desc": "官方/开放 HTTP API（OAuth）",
        "credentials": "token（仅环境变量）",
        "risk": "中性（限额内）",
        "status": "接口预留（Reddit OAuth 二期）",
    },
    "browser-cdp": {
        "desc": "CDP 接管真实浏览器（Playwright connect_over_cdp）",
        "credentials": "零（复用本机登录态）",
        "risk": "友好（真实指纹+真实会话）",
        "status": "PROVEN（X / Gmail web 已实测）",
    },
    "os-input": {
        "desc": "OS 级键鼠/Accessibility 模拟（macOS AppleScript/System Events）",
        "credentials": "零",
        "risk": "最像人；但无 DOM 选择器，必须截图留证",
        "status": "UNVERIFIED（transports/os_input.py 骨架）",
    },
    "protocol": {
        "desc": "应用层协议（SMTP/IMAP）",
        "credentials": "app password（仅环境变量）",
        "risk": "中性（受服务商限额）",
        "status": "PROVEN（邮件 1300 封量级模式）",
    },
}

# 渠道 → 动作 → 降级链（首选在前）。"*" 为该渠道缺省。
CHANNEL_TRANSPORTS = {
    "x": {"*": ["browser-cdp"]},
    "email": {
        "message": ["protocol", "browser-cdp"],   # 449 事故后定的顺序：协议为主，CDP 兜底
        "publish": ["protocol", "browser-cdp"],
        "reply": ["protocol", "browser-cdp"],
        "*": ["browser-cdp"],                     # inbox 等只读动作走 CDP
    },
    "reddit": {"*": ["browser-cdp", "api"]},      # api 通道二期（需 OAuth 注册）
    "linkedin": {"*": ["browser-cdp"]},
    "desktop": {"*": ["os-input"]},               # 无浏览器界面的平台/桌面客户端（未来）
}


def chain(channel, action):
    """渠道×动作的降级链；未知渠道返回空列表（调用方应拒绝）。"""
    m = CHANNEL_TRANSPORTS.get(channel, {})
    return m.get(action) or m.get("*", [])


def primary(channel, action):
    c = chain(channel, action)
    return c[0] if c else None


def known(name):
    return name in TRANSPORTS
