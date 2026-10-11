#!/usr/bin/env python3
"""渠道注册表 —— 能力声明（架构文档 §2.3）。

status 三级（02 §6.2，诚实交付）：
  PROVEN     在真实账号跑通过（或模式来源已跑通且未改动逻辑）
  PORTED     模式已验证、框架化封装后未整跑（首跑清单见 03 文档 §5）
  UNVERIFIED 选择器/流程按公开 UI 编写，未实测；强制 dry-run 门槛
"""
REGISTRY = {
    "x": {
        "status": "PROVEN",
        "note": "vendor 引入 x-ops-automation，选择器与纪律为真实账号实测",
        "actions": {"publish", "reply", "message", "engage", "search", "inbox"},
        "transports": {"cdp"},
    },
    "email": {
        "status": "PORTED",
        "note": "SMTP 写（newoutreach 模式）+ CDP 读（scan_inbox 模式）；封装后未整跑",
        "actions": {"publish", "reply", "message", "inbox"},
        "transports": {"smtp", "cdp"},
    },
    "reddit": {
        "status": "UNVERIFIED",
        "note": "old.reddit 选择器未实测；engage 禁用；publish 日≤3",
        "actions": {"publish", "reply", "message", "search", "inbox"},
        "transports": {"cdp", "api"},
    },
    "linkedin": {
        "status": "UNVERIFIED",
        "note": "Tier B 半自动；全自动默认锁死（MKT_LINKEDIN_AUTO=1 才解锁）",
        "actions": {"publish", "reply", "message", "search", "inbox"},
        "transports": {"cdp"},
    },
    "github": {
        # 安全语义按写原语取级（MCP 实跑门禁用）；读原语已在真实仓库实测
        "status": "UNVERIFIED",
        "note": "api 通道首发渠道；读原语 audit/search/inbox PROVEN（2026-10-12 实测）；"
                "reply 走统一写路径，首跑需人工盯 + 复核公开可见；凭据走 MKT_GITHUB_TOKEN",
        "actions": {"reply", "search", "inbox", "audit"},
        "transports": {"api"},
    },
}
