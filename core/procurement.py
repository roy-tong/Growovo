#!/usr/bin/env python3
"""requires_procurement 显式分支（评审 2026-11-17 §5）。

预算与采购不属于端到端范围：遇到付费数据源、广告、付费 API、第三方
OCR/数据服务时，Growovo 不失败、不静默跳过、更不代替用户购买，而是输出
结构化状态（terminal_state=BLOCKED_USER_INPUT、transaction_executed=false），
把唯一明确的用户行动项交回给人。

规划期就要识别并提示（compile 触发点），避免执行到一半才停。
"""
import json
from typing import Dict, List, Optional, Sequence

REASON = "requires_procurement"
TERMINAL_STATE = "BLOCKED_USER_INPUT"

DEFAULT_ALTERNATIVES: List[str] = ["CDP 可见页", "复制粘贴", "截图 OCR", "缩小范围"]

#: 已知付费输入（渠道/数据源形态 → 采购对象与原因）。这不是能力清单的替代品：
#: 命中这里的请求会被转成采购分支，而不是当成普通渠道拒绝。
PAID_ITEMS: Dict[str, Dict[str, str]] = {
    "x_ads": {
        "item": "X 广告投放（付费流量）",
        "why": "广告投放需要用户的广告账号、预算与出价决定；免费可见路径"
               "（organic 内容/回复）已在 x 渠道覆盖，不能代替付费触达",
    },
    "ads": {
        "item": "广告投放（付费流量）",
        "why": "广告投放需要用户的广告账号与预算；Growovo 不代投、不代付款",
    },
    "google_ads": {
        "item": "Google Ads 广告投放",
        "why": "广告投放需要用户的广告账号与预算；Growovo 不代投、不代付款",
    },
    "facebook_ads": {
        "item": "Facebook/Meta 广告投放",
        "why": "广告投放需要用户的广告账号与预算；Growovo 不代投、不代付款",
    },
    "linkedin_ads": {
        "item": "LinkedIn 广告投放",
        "why": "广告投放需要用户的广告账号与预算；Growovo 不代投、不代付款",
    },
    "x_api_paid": {
        "item": "X 官方付费 API（读数/检索）",
        "why": "官方 API 读数按量付费；免费路径是用户浏览器可见页的只读采集",
    },
    "sales_navigator": {
        "item": "LinkedIn Sales Navigator（付费线索库）",
        "why": "线索库是订阅制付费服务；免费路径是公开页与用户自提供的名单",
    },
    "paid_api": {
        "item": "付费第三方 API/数据服务",
        "why": "该数据源按量或按订阅收费；需要用户先开通并承担费用",
    },
}


def blocked(item: str, why: str, user_action: str,
            alternatives: Optional[Sequence[str]] = None) -> Dict[str, object]:
    """构造评审 §5 的结构化采购分支 JSON。"""
    if not str(item or "").strip():
        raise ValueError("requires_procurement 必须写明 item（需要用户采购什么）")
    if not str(why or "").strip():
        raise ValueError("requires_procurement 必须写明 why（为什么免费路径不够）")
    if not str(user_action or "").strip():
        raise ValueError("requires_procurement 必须写明 user_action（用户做什么）")
    return {
        "terminal_state": TERMINAL_STATE,
        "reason": REASON,
        "item": str(item),
        "why": str(why),
        "user_action": str(user_action),
        "alternatives": list(alternatives or DEFAULT_ALTERNATIVES),
        "transaction_executed": False,
    }


def message(payload: Dict[str, object]) -> str:
    """单行错误消息（异常文本用）；完整结构看 payload 本身。"""
    return "%s：%s——%s（transaction_executed=false，用户行动：%s）" % (
        payload["terminal_state"], payload["reason"],
        payload["item"], payload["user_action"])


def render(payload: Dict[str, object]) -> str:
    """CLI/JSON 输出：一个完整 JSON 对象。"""
    return json.dumps(payload, ensure_ascii=False, indent=1)


def match_paid_channel(channels: Sequence[str]) -> Optional[Dict[str, str]]:
    """requested 渠道里命中的第一个付费形态 → (canonical, item, why)。"""
    for raw in channels or []:
        key = str(raw or "").strip().lower().replace("-", "_").replace(" ", "_")
        if key in PAID_ITEMS:
            meta = PAID_ITEMS[key]
            return {"canonical": key, "item": meta["item"], "why": meta["why"]}
    return None
