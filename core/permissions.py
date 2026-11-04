#!/usr/bin/env python3
"""Minimal local actor/permission guards for the GTM work OS.

This is not an enterprise identity provider.  It makes agent/human authority,
author/reviewer separation and named-receiver decisions explicit in the Python
API.  Existing plan approval remains the authority for external writes.
"""
from __future__ import annotations


class PermissionDenied(ValueError):
    pass


def require_actor(*, actor_id: str, actor_type: str, operation: str,
                  author_id: str = "", receiver_id: str = "", risk_class: str = "R1") -> None:
    if not actor_id.strip():
        raise PermissionDenied("操作必须关联 actor_id")
    if actor_type not in ("human", "agent", "service"):
        raise PermissionDenied("actor_type 必须是 human/agent/service")
    if operation == "deliverable.review":
        if actor_type != "human":
            raise PermissionDenied("Agent/服务不能终审交付物，必须由人类 reviewer 验收")
        if risk_class in ("R3", "R4") and actor_id == author_id:
            raise PermissionDenied("R3/R4 交付物作者不能终审自己的交付物")
        return
    if operation in ("handoff.accept", "handoff.reject"):
        if actor_type != "human":
            raise PermissionDenied("Agent/服务不能代替人类接收人决定交接")
        if actor_id != receiver_id:
            raise PermissionDenied("只有指定接收人可以决定交接")
        return
    if operation == "action.go":
        if actor_type != "human":
            raise PermissionDenied("外部写动作只能由人类通过既有审批执行链授权")
        return
    if operation in ("read", "draft", "work.ready", "action_request.materialize"):
        return
    raise PermissionDenied(f"未知权限操作: {operation}")
