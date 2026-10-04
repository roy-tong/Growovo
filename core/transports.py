#!/usr/bin/env python3
"""Transport 通道注册表 —— 多种操作平台方式的显式抽象（05 §4，06 §4 通道储备库）。

ACTIVE 通道：当前架构内可用。
RESERVE 通道：已设计、未实现的备用路径——"当一条路走不通"时按降级决策表启用。
降级是内核内的确定性规则；规划层无权指定执行通道。
"""

TRANSPORTS = {
    # ---- ACTIVE ----
    "api": {
        "desc": "官方/开放 HTTP API（OAuth）",
        "credentials": "token（仅环境变量）",
        "risk": "中性（限额内）",
        "status": "接口预留（Reddit OAuth 二期）",
        "tier": "ACTIVE",
    },
    "browser-cdp": {
        "desc": "CDP 接管真实浏览器（Playwright connect_over_cdp）",
        "credentials": "零（复用本机登录态）",
        "risk": "友好（真实指纹+真实会话）",
        "status": "PROVEN（X / Gmail web 已实测）",
        "tier": "ACTIVE",
    },
    "os-input": {
        "desc": "OS 级键鼠/Accessibility 模拟（macOS AppleScript/System Events）",
        "credentials": "零",
        "risk": "最像人；但无 DOM 选择器，必须截图留证",
        "status": "UNVERIFIED（transports/os_input.py 骨架）",
        "tier": "ACTIVE",
    },
    "protocol": {
        "desc": "应用层协议（SMTP 写 / IMAP 读）",
        "credentials": "app password（仅环境变量）",
        "risk": "中性（受服务商限额）",
        "status": "SMTP 写=PROVEN（1300 封量级模式）；IMAP 读=预留",
        "tier": "ACTIVE",
    },
    # ---- RESERVE（06 文档 §4：一条路走不通时的备件库）----
    "webdriver": {
        "desc": "WebDriver 协议（Selenium/Playwright webdriver）驱动真实浏览器",
        "credentials": "零（复用登录态）",
        "risk": "中；连接协议与自动化指纹和 CDP 不同，可作 CDP 失联/被识别时的同族备件",
        "status": "RESERVE（未实现）",
        "tier": "RESERVE",
    },
    "rss-public": {
        "desc": "公共只读端点（Reddit .json、RSS/Atom、镜像站）",
        "credentials": "零",
        "risk": "读侧最低风险；只做 search/inbox 类只读，禁写",
        "status": "RESERVE（未实现）",
        "tier": "RESERVE",
    },
    "esp-api": {
        "desc": "ESP 事务邮件 API（SendGrid/Postmark 等）",
        "credentials": "API key（仅环境变量）",
        "risk": "官方通道、送达率高于裸 SMTP；成本按量",
        "status": "RESERVE（未实现；email 写通道第二备件）",
        "tier": "RESERVE",
    },
    "mobile-appium": {
        "desc": "移动端 App 自动化（adb/Appium）",
        "credentials": "零（复用 App 登录态）",
        "risk": "App 端风控面与 Web 不同（部分平台更宽）；维护成本高",
        "status": "RESERVE（未实现）",
        "tier": "RESERVE",
    },
    "integration": {
        "desc": "官方集成/平台原生能力（原生定时发帖、Buffer 类官方 API 连接器）",
        "credentials": "OAuth 授权",
        "risk": "最低（平台自己的白名单路径）；能力有限，多为发布类",
        "status": "RESERVE（未实现）",
        "tier": "RESERVE",
    },
    "semi-auto-ui": {
        "desc": "半自动：agent 准备（找目标/填内容/截图）+ 人工确认点击",
        "credentials": "零",
        "risk": "零（人点的）；速度=人速",
        "status": "RESERVE（LinkedIn Tier B 已用此模式，泛化为全渠道最后兜底）",
        "tier": "RESERVE",
    },
}

# 渠道 → 动作 → 降级链（首选在前，含 RESERVE 备件）。"*" 为该渠道缺省。
CHANNEL_TRANSPORTS = {
    "x": {"*": ["browser-cdp", "webdriver", "api", "semi-auto-ui"]},
    "email": {
        "message": ["protocol", "esp-api", "browser-cdp", "semi-auto-ui"],
        "publish": ["protocol", "esp-api", "browser-cdp", "semi-auto-ui"],
        "reply": ["protocol", "esp-api", "browser-cdp", "semi-auto-ui"],
        "inbox": ["browser-cdp", "protocol"],   # protocol=IMAP（预留）
        "*": ["browser-cdp"],
    },
    "reddit": {
        "search": ["rss-public", "browser-cdp", "api"],
        "*": ["browser-cdp", "api", "semi-auto-ui"],
    },
    "linkedin": {"*": ["browser-cdp", "semi-auto-ui"]},  # 无个人 API；半自动即兜底
    "desktop": {"*": ["os-input", "semi-auto-ui"]},
}


def chain(channel, action):
    """渠道×动作的降级链（ACTIVE 与 RESERVE 混列，按优先序）；未知渠道返回空列表。"""
    m = CHANNEL_TRANSPORTS.get(channel, {})
    return m.get(action) or m.get("*", [])


def active_chain(channel, action):
    """只返回 ACTIVE 状态的通道（当前可直接执行的）。"""
    return [t for t in chain(channel, action) if TRANSPORTS.get(t, {}).get("tier") == "ACTIVE"]


def primary(channel, action):
    c = chain(channel, action)
    return c[0] if c else None


def known(name):
    return name in TRANSPORTS
