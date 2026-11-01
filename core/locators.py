#!/usr/bin/env python3
"""Locator ladder: CSS first, then semantic locators, then an optional visual bridge.

Platform pages change. A workflow that hard-codes one CSS selector is a demo, not
an operator. This module resolves one logical target through an ordered ladder:

    css -> placeholder -> label -> role -> text -> visual

The visual step is an *explicitly configured* callable (Stagehand/Midscene or a
human-supplied finder). When no visual provider is configured, the ladder stops
at ``visual_unavailable`` — it never guesses a click target and calls it
recovery.

Nothing here performs a write on its own; callers decide what to do with the
resolved locator.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, Iterable, List, Optional

STRATEGIES = ("css", "placeholder", "label", "role", "text", "visual")
SEMANTIC = ("placeholder", "label", "role", "text")


class LocatorError(RuntimeError):
    pass


class LocatorUnresolved(LocatorError):
    """No strategy in the ladder resolved the target."""

    def __init__(self, target: str, tried: List[Dict[str, str]], visual_configured: bool):
        self.target = target
        self.tried = tried
        self.visual_configured = visual_configured
        reason = "visual_unavailable" if not visual_configured and any(
            item.get("strategy") == "visual" for item in tried) else "no_locator_matched"
        super().__init__(f"{target}: {reason} after {len(tried)} strategies")


def normalize(spec: Dict[str, Any]) -> Dict[str, str]:
    """Validate one locator spec; unknown strategies raise instead of being skipped."""
    if not isinstance(spec, dict):
        raise LocatorError("locator spec must be an object")
    strategy = str(spec.get("strategy") or "").strip().lower()
    if strategy not in STRATEGIES:
        raise LocatorError(f"unknown locator strategy {strategy!r}; use {', '.join(STRATEGIES)}")
    value = str(spec.get("value") or "").strip()
    role = str(spec.get("role") or "").strip()
    name = str(spec.get("name") or "").strip()
    if strategy == "role" and not role:
        raise LocatorError("role locator needs a role")
    if strategy in ("css", "placeholder", "label", "text") and not value:
        raise LocatorError(f"{strategy} locator needs a value")
    if strategy == "visual" and not (value or name):
        raise LocatorError("visual locator needs a value or name describing the target")
    return {"strategy": strategy, "value": value, "role": role, "name": name}


def _first(candidate: Any) -> Any:
    """Playwright locators expose .first; fake pages may return a single object."""
    return getattr(candidate, "first", candidate)


def resolve(
    page: Any,
    locators: Iterable[Dict[str, Any]],
    *,
    visual: Optional[Callable[[Any, Dict[str, str]], Any]] = None,
    visible_only: bool = True,
) -> Dict[str, Any]:
    """Resolve the first working locator and return it with the strategy used.

    A candidate that exists but is not visible is treated as a miss; a hidden
    element is how a "successful" click silently does nothing.
    """
    if page is None:
        raise LocatorError("browser page is not available")
    tried: List[Dict[str, str]] = []
    for raw in locators:
        spec = normalize(raw)
        strategy = spec["strategy"]
        if strategy == "visual" and visual is None:
            tried.append({"strategy": "visual", "value": spec["value"] or spec["name"], "result": "unavailable"})
            continue
        try:
            if strategy == "css":
                candidate = _first(page.locator(spec["value"]))
            elif strategy == "placeholder":
                candidate = _first(page.get_by_placeholder(spec["value"]))
            elif strategy == "label":
                candidate = _first(page.get_by_label(spec["value"]))
            elif strategy == "role":
                candidate = _first(page.get_by_role(spec["role"], name=spec["name"] or None))
            elif strategy == "text":
                candidate = _first(page.get_by_text(spec["value"]))
            else:  # visual
                candidate = visual(page, spec)
                candidate = _first(candidate)
        except Exception as exc:  # page errors are misses, not fatal ladder errors
            tried.append({"strategy": strategy, "value": spec["value"] or spec["name"],
                          "result": f"error: {str(exc)[:120]}"})
            continue
        if candidate is None:
            tried.append({"strategy": strategy, "value": spec["value"] or spec["name"], "result": "missing"})
            continue
        if visible_only:
            try:
                if hasattr(candidate, "is_visible") and not candidate.is_visible():
                    tried.append({"strategy": strategy, "value": spec["value"] or spec["name"],
                                  "result": "missing_or_hidden"})
                    continue
            except Exception:
                pass
        return {"locator": candidate, "strategy": strategy, "spec": spec, "tried": tried}
    raise LocatorUnresolved("target", tried, visual is not None)


def ladder_specs(locators: Iterable[Dict[str, Any]]) -> List[Dict[str, str]]:
    """Normalize and order specs by the fixed ladder, keeping caller order inside a strategy."""
    specs = [normalize(spec) for spec in locators]
    order = {name: index for index, name in enumerate(STRATEGIES)}
    return sorted(specs, key=lambda spec: order[spec["strategy"]])


def is_semantic(strategy: str) -> bool:
    return strategy in SEMANTIC
