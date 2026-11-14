#!/usr/bin/env python3
"""结果闭环 v2 —— 用户给事实与目标，引擎自动编译策略、按日配额执行、读数、
出日结果，并把「继续 / 优化策略」的决定权交给用户。

与 v1 的区别：
  - 执行单位从「整份队列」变成「日配额切片」：计划量真正控制发送量；
  - 读数进入 tick：传感器失败只降级不误判；
  - 每天出日结果卡，周卡增加业务事件/护栏/供给；
  - 决策语义：用户每天可 continue / optimize，引擎只调整执行参数并留回滚。
安全边界不变：所有发送仍走 run_queue/action_runner；常驻授权、暂停、DNC、
预算、回读、账本幂等全部保留；arm 仍是唯一人工开闸动作。
"""
from __future__ import annotations

import json
import hashlib
import os
import shlex
import sys
import time
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from .grow_lock import locked, run_lock
from . import grow_schedule as gs
from . import platform_caps, result_card
from .attribution import OutcomeLedger
from .grow_compile import SCHEMA, CompileError, compile_run
from .ledger import Ledger
from .safety import is_paused

HEARTBEAT_DEFAULT = 30
QUOTA_ATTENTION_BELOW = 0.5
SCALE_UP_RATIO = 1.25
SCALE_UP_CAP_RATIO = 2.0
READING_MISS_ATTENTION = 3
STALE_DAYS = 2


class GrowError(RuntimeError):
    pass


def _today() -> str:
    return date.today().isoformat()


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _belongs(campaign: str, run_id: str) -> bool:
    return campaign == run_id or campaign.startswith(run_id + "-")


def _default_window(data: Dict[str, Any]) -> str:
    windows = data.get("windows") or {}
    audience = windows.get("audience") or gs.DEFAULT_AUDIENCE
    local = windows.get("local") or gs.DEFAULT_WINDOWS["local"]
    parts = []
    for pair in local:
        lo, hi = float(pair[0]), float(pair[1])
        parts.append(f"{int(lo):02d}:{int(round((lo - int(lo)) * 60)):02d}-"
                     f"{int(hi):02d}:{int(round((hi - int(hi)) * 60)):02d}")
    return ",".join(parts) + f"@{audience}"


def _fmt_window(lo: float, hi: float) -> str:
    def one(v: float) -> str:
        h = int(v)
        m = int(round((v - h) * 60))
        return f"{h:02d}:{m:02d}"
    return f"{one(float(lo))}-{one(float(hi))}"


def _load_json(path: Path) -> Dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _write_text(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


class GrowRunV2:
    """结果闭环 v2 的读写与生命周期。"""

    def __init__(self, path):
        self.path = Path(path)

    # ---------- 存取 ----------

    def load(self) -> Dict[str, Any]:
        if not self.path.exists():
            raise FileNotFoundError(f"没有这个闭环执行记录：{self.path}")
        data = _load_json(self.path)
        if data.get("schema") != SCHEMA:
            raise GrowError(f"{self.path} 不是 v2 闭环（schema={data.get('schema')!r}）")
        return data

    def save(self, data: Dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode="w", dir=self.path.parent, delete=False,
                                         encoding="utf-8") as fh:
            temporary = Path(fh.name)
            try:
                fh.write(json.dumps(data, ensure_ascii=False, indent=1) + "\n")
                fh.flush()
                os.fsync(fh.fileno())
                os.replace(temporary, self.path)
            finally:
                temporary.unlink(missing_ok=True)

    # ---------- 启动 ----------

    @classmethod
    def launch(cls, path, *, run_id: str, facts_path: str, goal_id: str,
               channels: List[str], target: Optional[float] = None,
               window_days: Optional[int] = None, actions_per_week: int = 20,
               started: Optional[str] = None, owner: str = "", account: str = "",
               handle: str = "", audience: str = gs.DEFAULT_AUDIENCE,
               actions: Optional[List[str]] = None,
               data_dir, probe: Optional[Callable[..., Any]] = None,
               skip_probe: bool = False) -> Dict[str, Any]:
        """预检 + 编译 + 落盘。返回 {run, summary, missing_policy, warnings}。"""
        data_dir = Path(data_dir)
        facts_file = Path(facts_path).expanduser().resolve()
        if not facts_file.is_file():
            raise GrowError(f"找不到产品事实包：{facts_file}")
        try:
            facts = _load_json(facts_file)
        except json.JSONDecodeError as exc:
            raise GrowError(f"事实包不是合法 JSON：{exc}") from exc
        started = started or _today()
        try:
            spec = compile_run(run_id=run_id, facts=facts, goal_id=goal_id,
                               channels=channels, target=target,
                               window_days=window_days,
                               actions_per_week=actions_per_week,
                               started=started, owner=owner, account=account,
                               handle=handle, audience=audience,
                               actions=actions, data_dir=data_dir)
        except CompileError as exc:
            raise GrowError(f"策略编译失败：{exc}") from exc
        except Exception as exc:  # goal 库的 GoalError 等统一带上上下文
            raise GrowError(f"策略编译失败：{exc}") from exc
        spec["facts_ref"] = str(facts_file)

        missing = cls._missing_policy(data_dir, spec["actions"])
        warnings: List[str] = []
        if missing:
            raise GrowError(
                "常驻授权未覆盖本 run 的动作：" + "、".join(missing)
                + f"\n先人工执行：{cls.policy_command(spec['actions'])}")
        policy = {}
        try:
            from .execution_policy import get_policy
            policy = get_policy(data_dir) or {}
        except Exception:  # noqa: BLE001 授权范围检查失败由执行门禁兜底
            policy = {}
        allowed = [str(a) for a in (policy.get("allow_actions")
                                    or policy.get("actions") or [])]
        extra = sorted(a for a in allowed if a not in set(spec["actions"]))
        if extra:
            warnings.append(
                "常驻授权还覆盖本 run 不用的动作：" + "、".join(extra)
                + "；canary 结束后建议收窄（会替换当前策略）")
        if spec["strategy"]["primary"].get("residency_days"):
            warnings.append(
                f"该渠道账号建议养号 {spec['strategy']['primary']['residency_days']} 天；"
                "health 不达标的账号 arm 后会被安全检查拦住")

        # 账号健康（注册过才校验；没注册只提醒，不阻塞本地试用）
        health = cls._account_health(data_dir, account)
        if health.get("checked"):
            if not health.get("eligible"):
                warnings.append(
                    f"账号 {account} 当前 {health.get('status')}（health="
                    f"{health.get('score')}）：只能 dry-run，先把账号修到 eligible")
            spec["guards"]["account_health"] = health

        # 环境探针：默认要做；失败直接拒绝启动，避免「第一次 tick 才发现通道不可用」
        probe_result: Dict[str, Any] = {}
        if not skip_probe:
            if probe is None:
                raise GrowError(
                    "启动预检需要环境探针（CDP 可达并能读到粉丝数）。"
                    "先 `mkt doctor` 修复，或确认只做离线准备时加 --skip-probe")
            try:
                probe_result = probe(spec) or {}
            except Exception as exc:  # noqa: BLE001 探针失败=启动失败
                raise GrowError(
                    f"环境探针失败：{exc}\n修好通道后重试，或加 --skip-probe 只建计划") from exc
            if not isinstance(probe_result, dict):
                probe_result = {}
        else:
            warnings.append("已跳过环境探针：第一次实跑前请先 `mkt grow doctor`")

        followers = probe_result.get("followers")
        if isinstance(followers, int):
            cls._record_reading(data_dir, spec, followers=followers,
                                impressions=probe_result.get("impressions"),
                                source="launch-probe", window=started)
            if spec["goal"].get("baseline") is None:
                spec["goal"]["baseline"] = followers
                target = spec["goal"].get("target")
                if spec["goal"].get("target_kind") == "delta" and isinstance(target, (int, float)):
                    spec["goal"]["target_text"] = (
                        f"从 {followers} 到 {followers + int(target)}（+{int(target)}）")
        elif spec["goal"].get("baseline") is None:
            warnings.append("facts 没给关注者基线，启动也没读到：首周新增粉丝会写「取不到」")
        readers = (spec.get("measurement") or {}).get("readers") or []
        if "x" in spec.get("channels", []) and not any(
                r.get("handle") for r in readers):
            warnings.append("没有 --handle：每日自动读数会跳过，结果单曝光/粉丝会写「取不到」")

        GrowRunV2(path).save(spec)
        cls._write_briefs(data_dir, spec)
        return {"run": spec, "summary": cls._summary(spec),
                "missing_policy": [], "warnings": warnings}

    @staticmethod
    def policy_command(actions: List[str]) -> str:
        return ('mkt policy enable --by "$USER" --actions '
                + ",".join(sorted(actions)) + " --days 30")

    @staticmethod
    def _missing_policy(data_dir: Path, actions: List[str]) -> List[str]:
        from .execution_policy import allows
        missing = []
        for item in actions:
            channel, action = str(item).split(":", 1)
            if not allows(Path(data_dir), channel, action, "", ""):
                missing.append(item)
        return missing

    @staticmethod
    def _account_health(data_dir: Path, account_id: str) -> Dict[str, Any]:
        if not account_id:
            return {"checked": False}
        try:
            from .accounts import AccountRegistry
            acc = AccountRegistry().get(account_id)
        except Exception:  # noqa: BLE001 未注册账号按未检查处理
            return {"checked": False}
        health = acc.get("health") or {}
        score = float(health.get("score") or 0)
        status = str(acc.get("status") or "")
        return {"checked": True, "status": status, "score": score,
                "eligible": status == "eligible" and score >= 60}

    @staticmethod
    def _record_reading(data_dir: Path, data: Dict[str, Any], *,
                        followers: Optional[int] = None,
                        impressions: Optional[int] = None,
                        source: str = "", window: str = "") -> Dict[str, int]:
        written: Dict[str, int] = {}
        ledger = OutcomeLedger(Path(data_dir))
        campaign = data.get("id") or ""
        window = window or _today()
        if isinstance(followers, int):
            ledger.add_count(metric="x_followers", value=followers, window=window,
                             source_ref=source or "reading", approximated=True,
                             campaign=campaign)
            written["x_followers"] = followers
        if isinstance(impressions, int):
            ledger.add_count(metric="x_impressions", value=impressions, window=window,
                             source_ref=source or "reading", approximated=True,
                             campaign=campaign)
            written["x_impressions"] = impressions
        return written

    @staticmethod
    def _write_briefs(data_dir: Path, data: Dict[str, Any]) -> Path:
        supply = data.get("supply") or {}
        lines = [f"# 内容需求单 · {data['id']}", "",
                 f"目标：{data['goal'].get('name')}（{data['goal'].get('target_text')}）",
                 f"周期：{data['goal'].get('starts')} ~ {data['goal'].get('ends')}", "",
                 "运营 agent 按下面的 brief 生产内容，过审稿门后 `mkt grow stage` 入队。",
                 ""]
        for brief in supply.get("briefs") or []:
            lines += [f"## {brief['id']}（{brief['channel']}/{brief['action']}）", "",
                      f"- 每周需要：{brief.get('count_per_week')} 条",
                      f"- 写作公式：{brief.get('formula')}",
                      f"- 受众：{brief.get('audience')}",
                      f"- 长度上限：{brief.get('max_len')} 字符",
                      f"- 必须过的门：{'、'.join(brief.get('gates') or [])}",
                      f"- 纪律：{brief.get('rule')}", ""]
        return _write_text(Path(data_dir) / "grow" / data["id"] / "briefs.md",
                           "\n".join(lines))

    @staticmethod
    def _summary(data: Dict[str, Any]) -> List[str]:
        goal = data["goal"]
        strat = data["strategy"]
        primary = strat["primary"]
        quota = data["quota"]
        windows = data["windows"]
        lines = [
            f"目标：{goal.get('name')} · {goal.get('target_text')}（{goal.get('window_days')} 天）",
            f"打法：主渠道 {primary.get('platform') or primary.get('channel')}"
            f"（{'/'.join(primary.get('actions') or [])}，每周 {quota['actions_per_week']} 条）"
            + (f"＋承接 {'、'.join(s['channel'] for s in strat.get('support') or [])}"
               if strat.get("support") else ""),
            f"节奏：平均每天约 {max(1, round(quota['actions_per_week'] / 7))} 条；受众黄金窗 "
            f"{', '.join(_fmt_window(a, b) for a, b in windows.get('local') or [])}"
            f"（{windows.get('audience')}），凌晨死区不发",
            f"供给：本周需 {quota['actions_per_week']} 条，需求单已写到 "
            f"data/grow/{data['id']}/briefs.md；缺口提前 3 天报警",
            f"读数：每天自动读粉丝与曝光（UNVERIFIED，第一周人工对数后升级）；"
            "每天出日结果，每周出结果单",
            f"开闸：mkt grow arm {data['id']}（之后不需要每天操作；异常才需要裁决）",
        ]
        return lines

    # ---------- 内容供给 ----------

    def briefs(self, data_dir=None) -> List[Dict[str, Any]]:
        """内容需求单：agent 按这个生产，人也可以读。"""
        return list((self.load().get("supply") or {}).get("briefs") or [])

    @locked
    def stage(self, data_dir, items: List[Dict[str, Any]], *,
              scheduled_for: Optional[str] = None,
              channel: Optional[str] = None, action: Optional[str] = None,
              window: str = "", by: str = "agent",
              resolve_target_text: bool = False,
              strict: bool = False) -> Dict[str, Any]:
        """把内容写进按日队列（agent 供给入口）。过编辑期门禁才落盘。

        items 每项至少 {target, body}；可选 window/formula/audience。
        同一目标已在本 run 账本 committed 的，直接拒绝，避免供给时重复造内容。
        """
        data = self.load()
        data_dir = Path(data_dir)
        primary = (data.get("strategy") or {}).get("primary") or {}
        channel = channel or str(primary.get("channel") or "x")
        action = action or str((primary.get("actions") or ["reply"])[0])
        if f"{channel}:{action}" not in data.get("actions", []):
            raise GrowError(f"{channel}:{action} 不在本 run 的动作范围内")
        if channel not in (data.get("channels") or []):
            raise GrowError(f"{channel} 不在本 run 的自动执行渠道 {data.get('channels')}")
        if not items:
            raise GrowError("stage 需要至少一条内容（--from 文件为空）")
        day = str(scheduled_for or _today())
        try:
            date.fromisoformat(day)
        except ValueError as exc:
            raise GrowError(f"--date 应为 YYYY-MM-DD：{day}") from exc
        name = f"{data['id']}-{day.replace('-', '')}"
        path = data_dir / "queues" / f"{name}.json"
        existing: Dict[str, Any] = {}
        if path.is_file():
            try:
                existing = _load_json(path)
            except json.JSONDecodeError as exc:
                raise GrowError(f"现有队列不是合法 JSON：{path}（{exc}）") from exc
        queue = existing or {"campaign": name, "channel": channel,
                             "action": action, "items": []}
        if queue.get("channel") != channel or queue.get("action") != action:
            name = f"{name}-{channel}-{action}"
            path = data_dir / "queues" / f"{name}.json"
            queue = (_load_json(path) if path.is_file() else
                     {"campaign": name, "channel": channel, "action": action, "items": []})
        from . import grow_content
        product_name = str((data.get("product") or {}).get("name") or "")
        kind = "reply" if action == "reply" else "post"
        committed = self._committed_targets(Ledger(data_dir / "ledger.jsonl"), data)
        committed = {self._key(k) for k in committed}
        seen = {self._key(i.get("target")) for p in self._discover_queues(data_dir, data["id"])
                for i in self._load_queue(p).get("items", [])}
        added = 0
        quality_scores: List[int] = []
        for raw in items:
            target = str((raw or {}).get("target") or "").strip()
            body = str((raw or {}).get("body") or "").strip()
            if not target or not body:
                raise GrowError(f"第 {added + 1} 条缺 target 或 body（不许空发）")
            if self._key(target) in committed:
                raise GrowError(f"目标已在本 run 发过：{target}（不要重复造内容）")
            if self._key(target) in seen:
                continue
            target_text = str(raw.get("target_text") or "")
            if not target_text and resolve_target_text and target.startswith("http"):
                try:
                    from .grow_targets import read_tweet_text
                    target_text = read_tweet_text(target)
                except Exception:  # noqa: BLE001 读原帖失败不阻塞入队
                    target_text = ""
            koc_flag = bool(raw.get("koc"))
            formula = str(raw.get("formula") or "") or grow_content.suggest_formula(
                target_text, kind=kind, koc=koc_flag)
            check = grow_content.lint_stage_item({
                "body": body, "kind": kind,
                "target_text": target_text,
                "formula": formula}, product_name=product_name)
            if kind == "reply" and "回复里带链接" in " ".join(check["issues"]):
                raise GrowError(f"回复里不许带链接：{target}")
            if strict and (check["score"] < 55 or check.get("unknown_formula")):
                raise GrowError(
                    f"内容结构分 {check['score']} 未过严格门槛（55）："
                    + "；".join(check["issues"][:3]))
            item = {"id": f"{name}-{len(queue['items']) + 1:03d}",
                    "target": target, "body": body,
                    "scheduled_for": day, "staged_by": by,
                    "kind": kind, "formula": check["formula"],
                    "target_text": target_text,
                    "content_score": check["score"],
                    "content_issues": check["issues"][:6],
                    "author_handle": str(raw.get("author_handle") or "").lstrip("@")}
            if window or raw.get("window"):
                item["window"] = window or raw.get("window")
            for extra in ("audience", "brief_id", "source", "subject", "editorial_review", "fit_review"):
                if raw.get(extra):
                    item[extra] = raw[extra]
            from . import grow_fit as _fit
            author_followers = raw.get("author_followers")
            author_tier = raw.get("author_tier") or _fit.account_tier(author_followers)
            koc = raw.get("koc")
            if koc is None:
                koc = author_tier == "koc"
            item["author_followers"] = author_followers
            item["author_tier"] = author_tier
            item["author_handle"] = str(raw.get("author_handle") or "").lstrip("@")
            item["koc"] = bool(koc)
            if item["author_handle"]:
                try:
                    _fit.mark_engaged(
                        data_dir, data["id"], item["author_handle"], on=day,
                        meta={"followers": author_followers,
                              "tier": author_tier, "is_koc": bool(koc),
                              "score": check["score"]})
                except Exception:  # noqa: BLE001 名单写入失败不阻塞入队
                    pass
            data.setdefault("content_index", {})[target] = {
                "formula": check["formula"], "kind": kind,
                "window": item.get("window") or "",
                "audience": raw.get("audience") or "",
                "author_tier": author_tier, "author_followers": author_followers,
                "author_handle": item["author_handle"],
                "koc": bool(koc),
                "score": check["score"], "staged_at": day}
            quality_scores.append(int(check["score"]))
            queue["items"].append(item)
            seen.add(self._key(target))
            added += 1
        if not queue["items"]:
            return {"path": str(path), "campaign": name, "added": 0, "total": 0,
                    "supply": self._supply_state(data, data_dir, day, committed),
                    "quality": {"average": None, "low": 0, "formulas": {}}}
        from .queue import lint_queue
        errs = lint_queue(queue, path=path)
        if errs:
            raise GrowError("队列没过编辑期门禁：" + "；".join(errs[:5]))
        _write_text(path, json.dumps(queue, ensure_ascii=False, indent=1) + "\n")
        supply = self._supply_state(data, data_dir, day, committed)
        data["supply_state"] = supply
        self.save(data)
        average = round(sum(quality_scores) / len(quality_scores), 1) if quality_scores else None
        low = [int(s) for s in quality_scores if int(s) < 55]
        formulas: Dict[str, int] = {}
        for path_item in queue.get("items") or []:
            formula = str(path_item.get("formula") or "") if isinstance(path_item, dict) else ""
            if formula:
                formulas[formula] = formulas.get(formula, 0) + 1
        return {"path": str(path), "campaign": name, "added": added,
                "total": len(queue["items"]), "supply": supply,
                "quality": {"average": average, "low": len(low),
                            "formulas": formulas}}

    @staticmethod
    def load_drafts(path) -> List[Dict[str, Any]]:
        """读取待暂存内容：JSON 数组 / {"items": [...]} / CSV（target,body 两列）。"""
        source = Path(path)
        if not source.is_file():
            raise GrowError(f"找不到内容文件：{source}")
        text = source.read_text(encoding="utf-8")
        if source.suffix.lower() == ".json":
            try:
                payload = json.loads(text)
            except json.JSONDecodeError as exc:
                raise GrowError(f"内容文件不是合法 JSON：{exc}") from exc
            if isinstance(payload, dict):
                payload = payload.get("items") or payload.get("drafts") or []
            if not isinstance(payload, list):
                raise GrowError("JSON 内容文件应为数组，或含 items 数组")
            return [dict(x) for x in payload if isinstance(x, dict)]
        rows: List[Dict[str, Any]] = []
        import csv
        for row in csv.DictReader(text.splitlines()):
            rows.append({"target": (row.get("target") or "").strip(),
                         "body": (row.get("body") or "").strip(),
                         "window": (row.get("window") or "").strip()})
        return rows

    # ---------- 开闸 / 恢复 ----------

    @locked
    def arm(self) -> Dict[str, Any]:
        data = self.load()
        health = data.get("guards", {}).get("account_health") or {}
        if health.get("checked") and not health.get("eligible"):
            raise GrowError("账号健康不达标，不能开闸（先修账号或换账号）")
        if data.get("go"):
            return data
        data["go"] = True
        data.setdefault("history", []).append(
            {"at": _today(), "kind": "arm", "note": "实跑已开闸；暂停闸与全部执行门照旧"})
        self.save(data)
        return data

    @locked
    def resume(self, *, note: str, by: str = "") -> Dict[str, Any]:
        data = self.load()
        if data.get("status") != "needs_attention":
            return data
        note = (note or "").strip()
        if not note:
            raise GrowError("恢复需要 --note 写清处理了什么（哪条队列、结论、谁修的）")
        data["status"] = "active"
        data.setdefault("history", []).append(
            {"at": _today(), "kind": "resume", "by": by, "note": note[:300]})
        self.save(data)
        return data

    # ---------- 读数 ----------

    def _read_once(self, data: Dict[str, Any], data_dir: Path, on: str,
                   reader: Optional[Callable[..., Any]]) -> Dict[str, Any]:
        day = data.setdefault("telemetry", {}).setdefault("days", {}).setdefault(on, {})
        existing = day.get("readings") or {}
        # 成功读数当天只读一次；失败最多重试 3 次，避免早间 Chrome 未醒就整天没数
        if existing and (not existing.get("error")
                         or int(existing.get("attempts") or 1) >= 3):
            return existing
        attempts = int((existing or {}).get("attempts") or 0) + 1
        result: Dict[str, Any] = {"at": datetime.now().isoformat(timespec="seconds"),
                                  "attempts": attempts}
        if reader is False:
            result["skipped"] = True
            day["readings"] = result
            return result
        try:
            if reader is None:
                readings = data.get("measurement", {}).get("readers") or []
                handle = next((str(r.get("handle") or "") for r in readings
                               if r.get("metric") == "x_followers"), "")
                if not handle:
                    raise GrowError("没有配置读数账号 handle（launch 时加 --handle）")
                from channels.x import read_stats
                stats = read_stats.read(handle, Path(data_dir) / "shots" / "x-stats")
                result["followers"] = stats.get("followers")
                result["impressions"] = stats.get("impressions_yesterday")
                result["source"] = stats.get("saved", "")
                self._record_reading(data_dir, data,
                                     followers=result.get("followers"),
                                     impressions=result.get("impressions"),
                                     source=str(result.get("source") or ""),
                                     window=on)
            else:
                got = reader(on) or {}
                result.update({"followers": got.get("followers"),
                               "impressions": got.get("impressions"),
                               "source": got.get("source", "injected-reader")})
                self._record_reading(data_dir, data,
                                     followers=result.get("followers"),
                                     impressions=result.get("impressions"),
                                     source=str(result.get("source") or ""),
                                     window=on)
        except Exception as exc:  # noqa: BLE001 传感器失败只降级，不停闭环
            err_text = " ".join(str(exc).split())
            result["error"] = f"{type(exc).__name__}: {err_text}"[:200]
        day["readings"] = result
        return result

    # ---------- 日循环 ----------

    def produce(self, data_dir, **kwargs):
        from .grow_lock import run_lock
        from .grow_agent import produce
        with run_lock(self.path):
            return produce(self, data_dir, **kwargs)

    def configure_agent(self, *, enabled=True):
        from .grow_agent import executable
        from .grow_lock import run_lock
        if enabled and not executable():
            raise GrowError("找不到 Codex CLI：设置 GROWOVO_CODEX_BIN")
        with run_lock(self.path):
            data = self.load()
            data.setdefault("automation", {}).update(enabled=enabled, provider="codex")
            if enabled:
                data.setdefault("supply", {})["min_cover_days"] = 1
            self.save(data)
        return data["automation"]

    def tick(self, data_dir, **kwargs):
        from .grow_lock import run_lock, BusyRun
        try:
            with run_lock(self.path):
                return self._tick(data_dir, **kwargs)
        except BusyRun:
            return {"run": self.load()["id"], "skipped": "busy", "ran": []}

    def _tick(self, data_dir, *, on: Optional[str] = None,
             runner: Optional[Callable[..., Any]] = None,
             reader: Optional[Callable[..., Any]] = None,
             metrics_reader: Optional[Callable[..., Any]] = None,
             content_writer=None, discover=None,
             now: Optional[datetime] = None,
             deliver: bool = False, log: Callable[..., Any] = print) -> Dict[str, Any]:
        data_dir = Path(data_dir)
        on = on or _today()
        now = now or _now_utc()
        data = self.load()
        if data.get("status") != "active":
            return {"run": data["id"], "skipped": data.get("status"), "ran": []}
        if is_paused(data_dir):
            data.setdefault("history", []).append(
                {"at": on, "kind": "skip", "reason": "paused"})
            self.save(data)
            return {"run": data["id"], "skipped": "paused", "ran": []}

        end = (data.get("goal") or {}).get("ends")
        if on < data["started"]:
            return {"run": data["id"], "skipped": "not_started", "ran": []}
        if end and on > end:
            weekly = self._close_week(data, data_dir, on, log=log)
            data["status"] = "completed"
            self.save(data)
            return {"run": data["id"], "skipped": "goal_window_ended", "ran": [], "weekly": weekly}
        production = None
        if (data.get("automation") or {}).get("enabled") and (content_writer is not None or
                (data.get("go") and runner is None)):
            from .grow_agent import produce
            try:
                production = produce(self, data_dir, on=on, writer=content_writer, discover=discover)
            except Exception as exc:
                production = {"added": 0, "error": str(exc)[:300]}
                log(f"[grow] 自动供给失败：{exc}")
            data = self.load()
            data.setdefault("automation", {})["last_result"] = production
        self._heartbeat(data, on=on, ok=True)
        readings = self._read_once(data, data_dir, on, reader)
        injected_runner = runner is not None

        if runner is None:
            from .queue_runner import run_queue as runner  # noqa: E731
        from .queue_runner import rejection_kind

        ledger = Ledger(data_dir / "ledger.jsonl")
        committed_today = self._committed_today(ledger, data, on)
        committed_targets = self._committed_targets(ledger, data)
        committed_targets |= self._suppressed_targets(data_dir)
        week_start = gs.week_start_of(on, data["started"])
        quota = data.get("quota") or {}
        daily = gs.split_week(int(quota.get("actions_per_week") or 0), week_start,
                              quota.get("action_weights"))
        today_quota = daily.get(on, {})
        done_by_action = self._count_by_action(committed_today)
        sent: Dict[str, int] = {}
        ran: List[Dict[str, Any]] = []
        needs_attention = False
        default_window = _default_window(data)

        for queue_path in self._discover_queues(data_dir, data["id"]):
            q = self._load_queue(queue_path)
            channel = str(q.get("channel") or "")
            action = str(q.get("action") or "")
            left = int(today_quota.get(action, 0)) - int(done_by_action.get(action, 0)) \
                - int(sent.get(action, 0))
            marker = f"{on}T{now.strftime('%H')}"
            if data.get("last_queue_run", {}).get(str(queue_path)) == marker:
                continue
            if left <= 0:
                ran.append({"queue": str(queue_path), "records": 0,
                            "note": f"{action} 今日配额已满（{today_quota.get(action, 0)}），顺延"})
                data.setdefault("last_queue_run", {})[str(queue_path)] = marker
                continue
            stale = self._count_stale(q, on=on,
                                      committed_targets=committed_targets)
            candidates = self._select_items(q, on=on, now=now,
                                            default_window=default_window,
                                            committed_targets=committed_targets,
                                            limit=left)
            if not candidates:
                note = "没有到期且窗口内的条目（留到下个 tick）"
                if stale:
                    note += f"；另有 {stale} 条过期未发"
                ran.append({"queue": str(queue_path), "records": 0,
                            "note": note, "stale": stale})
                continue
            records: List[Dict[str, Any]] = []
            err = None
            try:
                records = list(runner(
                    queue_path, data_dir=data_dir, go=bool(data.get("go")),
                    routine=True, account=str((data.get("guards") or {}).get("account_id") or ""),
                    continue_on_duplicate=True, only_ids=[c["id"] for c in candidates],
                    interval_seconds=tuple((data.get("quota") or {}).get("interval_seconds") or ()) or None,
                    log=log) or [])
            except Exception as exc:  # noqa: BLE001 队列级失败不吞
                err = f"{type(exc).__name__}: {exc}"[:300]
            results = [str(r.get("result") or "") for r in records if isinstance(r, dict)]
            dup = [r for r in records if isinstance(r, dict)
                   and rejection_kind(r) == "duplicate"]
            suppressed = [r for r in records if isinstance(r, dict)
                          and (r.get("result") == "suppressed"
                               or rejection_kind(r) == "suppression")]
            blocking = [r for r in records if isinstance(r, dict)
                        and (r.get("result") in ("failed", "indeterminate")
                             or (r.get("result") == "rejected"
                                 and rejection_kind(r) == "other"))]
            published = sum(1 for r in results if r == "committed")
            failed_n = sum(1 for r in results if r == "failed")
            indeterminate_n = sum(1 for r in results if r == "indeterminate")
            rejected_n = sum(1 for r in results if r == "rejected")
            entry = {"queue": str(queue_path), "records": len(records),
                     "results": results, "published": published,
                     "duplicates": len(dup), "suppressed": len(suppressed),
                     "stale": stale, "go": bool(data.get("go"))}
            if stale:
                entry["note"] = f"另有 {stale} 条过期未发"
            sent[action] = sent.get(action, 0) + published + len(dup) + len(suppressed)
            self._record_day_telemetry(data, on, action=action, published=published,
                                       duplicates=len(dup), suppressed=len(suppressed),
                                       failed=failed_n,
                                       indeterminate=indeterminate_n,
                                       rejected=rejected_n)
            if err:
                entry["error"] = err
                needs_attention = True
            elif blocking:
                detail = str(blocking[0].get("detail") or "")[:120]
                entry["error"] = "存在未知/失败/被拒结果，需要人工裁决" + (
                    f"：{detail}" if detail else "")
                needs_attention = True
            if dup:
                entry["note"] = f"{len(dup)} 条此前已发过，未重复发送"
            ran.append(entry)
            data.setdefault("last_queue_run", {})[str(queue_path)] = marker
            if needs_attention:
                break

        committed_targets = self._committed_targets(ledger, data)
        committed_today = self._committed_today(ledger, data, on)
        if not injected_runner:
            try:
                self._resolve_post_urls(data, data_dir, on, log=log)
            except Exception as exc:  # noqa: BLE001 URL 解析失败不影响闭环
                log(f"[grow] 解析自己帖子 URL 失败：{exc}")
        # Daily learning is part of the loop, with no external reads in dry-run/tests.
        if metrics_reader is not None or (data.get("go") and not injected_runner):
            from . import grow_learning
            try:
                collected = self.metrics(data_dir, on=on, reader=metrics_reader, _data=data)
                learning = grow_learning.analyze(data_dir, data["id"], data)
                learning["on"] = on
                learning["collection"] = {k: v for k, v in collected.items() if k != "items"}
                grow_learning.write_learning(data_dir, data["id"], learning, on=on)
                data["learning"] = learning
            except Exception as exc:
                data["learning"] = {"on": on, "changes": [],
                                    "recommendation": "指标采集失败，保持当前策略并重试",
                                    "error": str(exc)[:300]}
                log(f"[grow] 复盘读数失败：{exc}")
        data["supply_state"] = self._supply_state(data, data_dir, on, committed_targets)
        card = self._build_daily(data, data_dir, on,
                                 committed_today=committed_today,
                                 today_quota=today_quota, readings=readings)
        self._save_daily(data, data_dir, card)
        if deliver:
            self._deliver_daily(data, data_dir, card, log=log)
        due_card = None
        weekly = self._close_week(data, data_dir, on, log=log)
        if weekly:
            due_card = weekly
            self._deliver_weekly(data, data_dir, weekly, deliver=deliver, log=log)

        if needs_attention:
            data["status"] = "needs_attention"
            data.setdefault("history", []).append(
                {"at": on, "kind": "tick",
                 "note": "有队列需要人工处理，循环暂停"})
        else:
            data.setdefault("history", []).append(
                {"at": on, "kind": "tick", "ran": len(ran),
                 "published": sum(int(r.get("published") or 0) for r in ran)})
        self._heartbeat(data, on=on, ok=not needs_attention)
        self.save(data)
        return {"run": data["id"], "on": on, "ran": ran, "daily": card,
                "weekly": weekly, "readings": readings,
                "status": data["status"], "supply": data["supply_state"], "production": production}

    def _resolve_post_urls(self, data: Dict[str, Any], data_dir: Path, on: str,
                           *, limit: int = 3, log=print) -> int:
        """对今天新 committed 的 X 帖子解析「我们自己的 URL」，供指标采集。"""
        from . import grow_learning
        post_urls = data.setdefault("post_urls", {})
        readers = (data.get("measurement") or {}).get("readers") or []
        handle = next((str(r.get("handle") or "") for r in readers
                       if r.get("handle")), "")
        if not handle:
            return 0
        bodies = {}
        for path in self._discover_queues(data_dir, data["id"]):
            for item in (self._load_queue(path).get("items") or []):
                if isinstance(item, dict) and item.get("target"):
                    bodies[self._key(str(item["target"]))] = str(item.get("body") or "")
        ledger = Ledger(Path(data_dir) / "ledger.jsonl")
        resolved = 0
        for rec in ledger.all():
            if resolved >= limit:
                break
            if rec.get("result") != "committed" or rec.get("channel") != "x":
                continue
            if not _belongs(str(rec.get("campaign") or ""), data["id"]):
                continue
            if str(rec.get("ts") or "")[:10] != on:
                continue
            key = self._key(str(rec.get("target") or ""))
            if not key or key in post_urls:
                continue
            body = bodies.get(key) or ""
            if not body:
                continue
            lookup = f"https://x.com/{handle}" if rec.get("action") == "publish" else key
            found = grow_learning.resolve_post_url(
                lookup, body, handle, log=log)
            if found:
                post_urls[key] = found
                resolved += 1
                log(f"[grow] 已记录自己的帖子 URL：{found}")
        return resolved

    @staticmethod
    def _key(target: str) -> str:
        return str(target or "").removeprefix("tweet:").strip()

    @staticmethod
    def _heartbeat(data: Dict[str, Any], *, on: str, ok: bool) -> None:
        try:
            from .grow_ops import stamp_heartbeat
            stamp_heartbeat(data, at=_now_utc().isoformat(timespec="seconds"),
                            interval_minutes=int((data.get("heartbeat") or {}).get(
                                "interval_minutes") or HEARTBEAT_DEFAULT), ok=ok)
        except Exception:  # noqa: BLE001 心跳模块缺失时退化，不影响闭环
            data["heartbeat"] = {"last_tick": datetime.now().isoformat(timespec="seconds"),
                                 "interval_minutes": HEARTBEAT_DEFAULT, "ok": ok}

    @staticmethod
    def _load_queue(path: Path) -> Dict[str, Any]:
        try:
            return _load_json(path)
        except (json.JSONDecodeError, OSError):
            return {}

    @staticmethod
    def _discover_queues(data_dir: Path, run_id: str) -> List[Path]:
        directory = Path(data_dir) / "queues"
        if not directory.is_dir():
            return []
        return sorted(p for p in directory.glob(f"{run_id}-*.json") if p.is_file())

    @staticmethod
    def _committed_today(ledger: Ledger, data: Dict[str, Any], on: str) -> List[Dict[str, Any]]:
        out = []
        for rec in ledger.all():
            if rec.get("result") != "committed":
                continue
            if not _belongs(str(rec.get("campaign") or ""), data["id"]):
                continue
            if str(rec.get("ts") or "")[:10] != on:
                continue
            out.append(rec)
        return out

    @staticmethod
    def _committed_targets(ledger: Ledger, data: Dict[str, Any]) -> set:
        out = set()
        for rec in ledger.all():
            if rec.get("result") != "committed":
                continue
            if not _belongs(str(rec.get("campaign") or ""), data["id"]):
                continue
            if rec.get("target"):
                out.add(str(rec["target"]))
            if rec.get("ref"):
                out.add(str(rec["ref"]))
        return out

    @staticmethod
    def _suppressed_targets(data_dir: Path) -> set:
        from .gates import load_suppressions
        try:
            return {str(x) for x in load_suppressions(Path(data_dir))}
        except Exception:  # noqa: BLE001 抑制源坏了由发送门禁 fail-closed，不在选条目处放大
            return set()

    @staticmethod
    def _count_by_action(records: List[Dict[str, Any]]) -> Dict[str, int]:
        out: Dict[str, int] = {}
        for rec in records:
            act = str(rec.get("action") or "")
            out[act] = out.get(act, 0) + 1
        return out

    @staticmethod
    def _count_stale(queue: Dict[str, Any], *, on: str,
                     committed_targets: set) -> int:
        """统计过期未发（scheduled_for 早于今天-STALE_DAYS 且未发）。"""
        oldest = (date.fromisoformat(on)
                  - timedelta(days=STALE_DAYS)).isoformat()
        count = 0
        for item in queue.get("items") or []:
            if not isinstance(item, dict):
                continue
            key = str(item.get("target") or item.get("id") or "")
            if key and key in committed_targets:
                continue
            scheduled = str(item.get("scheduled_for") or on)
            if scheduled < oldest:
                count += 1
        return count

    @staticmethod
    def _select_items(queue: Dict[str, Any], *, on: str, now: datetime,
                      default_window: str, committed_targets: set,
                      limit: int, max_lateness_days: int = 2) -> List[Dict[str, Any]]:
        selected = []
        oldest = (date.fromisoformat(on)
                  - timedelta(days=max(0, int(max_lateness_days)))).isoformat()
        for item in queue.get("items") or []:
            if not isinstance(item, dict):
                continue
            key = str(item.get("target") or item.get("id") or "")
            if key and key in committed_targets:
                continue
            scheduled = str(item.get("scheduled_for") or on)
            if scheduled < oldest:
                continue  # 过期内容不再发：宁可少发，不补发陈旧回复
            window = item.get("window") or default_window
            probe = dict(item)
            probe.setdefault("window", window)
            if not gs.item_due(probe, today=on, now_utc=now):
                continue
            selected.append(item)
            if len(selected) >= max(0, int(limit)):
                break
        return selected

    @staticmethod
    def _record_day_telemetry(data: Dict[str, Any], on: str, *, action: str,
                              published: int, duplicates: int, suppressed: int,
                              failed: int = 0, indeterminate: int = 0,
                              rejected: int = 0, stale: int = 0) -> None:
        day = data.setdefault("telemetry", {}).setdefault("days", {}).setdefault(on, {})
        counts = day.setdefault("counts", {})
        bucket = counts.setdefault(action or "?", {
            "published": 0, "duplicates": 0, "suppressed": 0,
            "failed": 0, "indeterminate": 0, "rejected": 0, "stale": 0})
        bucket["published"] += int(published)
        bucket["duplicates"] += int(duplicates)
        bucket["suppressed"] += int(suppressed)
        bucket["failed"] += int(failed)
        bucket["indeterminate"] += int(indeterminate)
        bucket["rejected"] += int(rejected)
        bucket["stale"] += int(stale)

    def _supply_state(self, data: Dict[str, Any], data_dir: Path, on: str,
                      committed_targets: set) -> Dict[str, Any]:
        queues = []
        for path in self._discover_queues(data_dir, data["id"]):
            queues.append({"path": str(path), "queue": self._load_queue(path)})
        week_start = gs.week_start_of(on, data["started"])
        daily = gs.split_week(int(data["quota"]["actions_per_week"]), week_start,
                              data["quota"].get("action_weights"))
        daily_quota = max(1, sum(daily.get(on, {}).values()) or 1)
        state = gs.supply_status(queues, today=on, days=int(
            (data.get("supply") or {}).get("min_cover_days") or 3),
            daily_quota=daily_quota, committed_targets=committed_targets)
        state["week_plan"] = int(data["quota"]["actions_per_week"])
        return state

    # ---------- 日结果 ----------

    def daily(self, data_dir, *, on: Optional[str] = None,
              reader: Optional[Callable[..., Any]] = None) -> Dict[str, Any]:
        """生成（或重放）某天的日结果卡；不发送。"""
        data_dir = Path(data_dir)
        on = on or _today()
        data = self.load()
        if reader is not None:
            self._read_once(data, data_dir, on, reader)
        ledger = Ledger(data_dir / "ledger.jsonl")
        committed_today = self._committed_today(ledger, data, on)
        week_start = gs.week_start_of(on, data["started"])
        daily = gs.split_week(int(data["quota"]["actions_per_week"]), week_start,
                              data["quota"].get("action_weights"))
        card = self._build_daily(data, data_dir, on,
                                 committed_today=committed_today,
                                 today_quota=daily.get(on, {}),
                                 readings=(data.get("telemetry", {})
                                           .get("days", {}).get(on, {})
                                           .get("readings") or {}))
        self.save(data)
        return card

    def _build_daily(self, data: Dict[str, Any], data_dir: Path, on: str, *,
                     committed_today: List[Dict[str, Any]],
                     today_quota: Dict[str, int],
                     readings: Dict[str, Any]) -> Dict[str, Any]:
        by_action: Dict[str, int] = self._count_by_action(committed_today)
        published = sum(by_action.values())
        planned = sum(int(v) for v in today_quota.values())
        goal = self._goal_progress(data, data_dir)
        day_telemetry = data.get("telemetry", {}).get("days", {}).get(on, {})
        counts = day_telemetry.get("counts") or {}
        guards = {
            "failed": sum(int(v.get("failed") or 0) for v in counts.values()),
            "indeterminate": sum(int(v.get("indeterminate") or 0)
                                 for v in counts.values()),
            "duplicates": sum(int(v.get("duplicates") or 0) for v in counts.values()),
            "suppressed": sum(int(v.get("suppressed") or 0) for v in counts.values()),
        }
        decisions = [d for d in data.get("decisions") or [] if d.get("on") == on]
        return {
            "schema": "growovo.daily-card/1",
            "run": data["id"],
            "date": on,
            "goal": goal,
            "published": {"total": published, "planned": planned,
                          "by_action": by_action},
            "readings": readings or {},
            "guardrails": guards,
            "supply": data.get("supply_state") or {},
            "learning": data.get("learning") or {},
            "production": (data.get("automation") or {}).get("last_result") or {},
            "decision": {"pending": not decisions,
                         "options": ["continue", "optimize"],
                         "last": decisions[-1] if decisions else None},
            "go": bool(data.get("go")),
            "status": data.get("status"),
        }

    @staticmethod
    def render_daily(card: Dict[str, Any]) -> str:
        goal = card.get("goal") or {}
        pub = card.get("published") or {}
        readings = card.get("readings") or {}
        supply = card.get("supply") or {}
        guards = card.get("guardrails") or {}
        lines = [f"# 日结果 · {card.get('date')}（{card.get('run')}）", "",
                 f"目标：{goal.get('target_text') or goal.get('metric')}"
                 f"｜当前 {goal.get('current') if goal.get('current') is not None else '取不到'}"
                 f"｜基线 {goal.get('baseline') if goal.get('baseline') is not None else '取不到'}",
                 "",
                 "| 口径 | 今天 |",
                 "|---|---|",
                 f"| 发布量（committed） | {pub.get('total', 0)}（计划 {pub.get('planned', 0)}） |",
                 f"| 曝光（昨日） | {readings.get('impressions') if readings.get('impressions') is not None else '取不到'} |",
                 f"| 关注者 | {readings.get('followers') if readings.get('followers') is not None else '取不到'} |",
                 f"| 供给（未来 3 天） | 已暂存 {supply.get('staged', 0)} 条"
                 f"{'，缺 ' + str(supply.get('gap')) + ' 条' if supply.get('gap') else ''} |",
                 "",
                 f"护栏：失败 {guards.get('failed', 0)}｜未知 "
                 f"{guards.get('indeterminate', 0)}｜重复拦截 "
                 f"{guards.get('duplicates', 0)}｜抑制跳过 {guards.get('suppressed', 0)}"]
        if readings.get("error"):
            lines.append(f"读数异常：{readings['error']}")
        production = card.get("production") or {}
        if production:
            lines += ["", f"自动内容供给：本轮入队 {production.get('added', 0)} 条"]
            lines.extend(f"- {issue}" for issue in production.get("rejected", []))
            if production.get("error") or production.get("skipped"):
                lines.append(str(production.get("error") or production.get("skipped")))
        learning = card.get("learning") or {}
        if learning:
            lines += ["", "## 今日复盘", f"有效帖子样本：{learning.get('sample', 0)}",
                      str(learning.get("recommendation") or "继续采集")]
            if learning.get("error"):
                lines.append(f"采集异常：{learning['error']}")
            lines.extend(f"- {c.get('detail', '')}" for c in learning.get("changes") or [])
        decision = card.get("decision") or {}
        lines += ["", "## 今天要不要调整",
                  "默认继续。看完上面的数，你也可以决定优化策略：",
                  f"- `mkt grow decide {card.get('run')} --action continue`",
                  f"- `mkt grow decide {card.get('run')} --action optimize --note '为什么'`"]
        if decision.get("last"):
            last = decision["last"]
            lines += ["", f"已完成决定：{last.get('action')}"
                          f"（{last.get('by') or '用户'}）"
                          + (f"：{last.get('note')}" if last.get("note") else "")]
        lines += ["", "边界：committed 只代表发出，不代表转化；读数 UNVERIFIED，"
                      "第一周人工对数后升级。", ""]
        return "\n".join(lines)

    def _save_daily(self, data: Dict[str, Any], data_dir: Path,
                    card: Dict[str, Any]) -> None:
        cards = data.setdefault("daily_cards", [])
        cards[:] = [c for c in cards if c.get("date") != card["date"]]
        cards.append(card)
        cards.sort(key=lambda c: str(c.get("date") or ""))
        text = self.render_daily(card)
        try:
            from .deliver import write_card
            write_card(data_dir, data["id"], "daily", card["date"], text,
                       title=f"日结果 {card['date']}")
        except Exception:  # noqa: BLE001 投递模块缺失不影响记账
            _write_text(Path(data_dir) / "grow" / data["id"] / "cards"
                        / f"daily-{card['date']}.md", text)

    def _deliver_daily(self, data: Dict[str, Any], data_dir: Path,
                       card: Dict[str, Any], *, log=print) -> None:
        text = self.render_daily(card)
        try:
            from .deliver import deliver_card
            out = deliver_card(data_dir, data["id"], "daily", card["date"], text,
                               title=f"日结果 {data['id']} {card['date']}",
                               to=str(data.get("owner") or ""),
                               subject=f"{data['id']} 日结果 {card['date']}",
                               live=False)
            log(f"[grow] 日结果已落盘：{out.get('html') or out}")
        except Exception as exc:  # noqa: BLE001 投递失败不影响结果生成
            log(f"[grow] 日结果投递失败（已保留卡片）：{exc}")

    # ---------- 周日结算 ----------

    def _close_week(self, data: Dict[str, Any], data_dir: Path, on: str,
                    log=print) -> Optional[Dict[str, Any]]:
        windows = result_card.week_bounds(data["started"], on)
        closed = {str(c.get("week_end") or c.get("end") or "")
                  for c in data.get("result_cards") or []}
        due = None
        for week, start, end in windows:
            if end < on and end not in closed:
                due = (week, start, end)
                break
        if not due:
            return None
        week, start, end = due
        data.setdefault("telemetry", {}).setdefault("weeks", {})[start] = \
            self._week_telemetry(data, start, end)
        card = result_card.build(data_dir, data, start, end, on=on)
        card["week"] = week
        card["week_start"] = start
        card["week_end"] = end
        self._apply_adjustments(data, card, week, start, end)
        # 结算调整要出现在「下周动作」里，但不能改写本周的「计划量」：
        # 只用重算结果替换调整相关字段，三件套与计划量保持结算当刻口径。
        fresh = result_card.build(data_dir, data, start, end, on=on)
        card["adjustments"] = fresh.get("adjustments") or []
        card["next_week"] = fresh.get("next_week") or {}
        data.setdefault("result_cards", []).append(card)
        data["result_cards"].sort(key=lambda c: str(c.get("week_start") or c.get("start") or ""))
        log(f"[grow] 第 {week} 周结果单已生成：{start} ~ {end}")
        return card

    @staticmethod
    def _week_telemetry(data: Dict[str, Any], start: str, end: str) -> Dict[str, Any]:
        days = (data.get("telemetry") or {}).get("days") or {}
        out = {"failed": 0, "indeterminate": 0, "rejected": 0,
               "duplicates": 0, "suppressed": 0, "stale": 0,
               "readings_days": 0, "published": 0}
        for day, entry in days.items():
            if not (start <= day <= end):
                continue
            counts = entry.get("counts") or {}
            out["failed"] += sum(int(v.get("failed") or 0) for v in counts.values())
            out["indeterminate"] += sum(int(v.get("indeterminate") or 0)
                                        for v in counts.values())
            out["rejected"] += sum(int(v.get("rejected") or 0) for v in counts.values())
            out["stale"] += sum(int(v.get("stale") or 0) for v in counts.values())
            out["duplicates"] += sum(int(v.get("duplicates") or 0) for v in counts.values())
            out["suppressed"] += sum(int(v.get("suppressed") or 0) for v in counts.values())
            out["published"] += sum(int(v.get("published") or 0) for v in counts.values())
            if (entry.get("readings") or {}).get("followers") is not None:
                out["readings_days"] += 1
        out["account_health"] = ((data.get("guards") or {}).get("account_health") or {}).get("score")
        return out

    @staticmethod
    def _apply_adjustments(data: Dict[str, Any], card: Dict[str, Any], week: int,
                           start: str, end: str) -> None:
        plan = data.setdefault("plan", {})
        quota = data.setdefault("quota", {})
        committed = int(card["published"]["total"])
        planned = int(plan.get("actions_per_week") or quota.get("actions_per_week") or 0)
        adjustments = data.setdefault("adjustments", [])
        health = ((data.get("guards") or {}).get("account_health") or {}).get("score")
        if isinstance(health, (int, float)) and health < 60:
            old = int(plan.get("actions_per_week") or 0)
            new = max(1, round(old * 0.8))
            plan["actions_per_week"] = new
            quota["actions_per_week"] = new
            adjustments.append({"on": end, "kind": "safety", "param": "actions_per_week",
                                "from": old, "to": new,
                                "detail": f"账号健康 {health} < 60：先降量 20%，修好再谈加量"})
            return
        if planned and committed >= 0.9 * planned:
            old = int(plan.get("actions_per_week") or planned)
            initial = int(plan.get("initial_actions_per_week") or old)
            cap = round(SCALE_UP_CAP_RATIO * initial)
            new = min(round(old * SCALE_UP_RATIO), cap)
            if new != old:
                adjustments.append({"on": end, "kind": "proposal",
                                    "param": "actions_per_week", "from": old, "to": new,
                                    "detail": f"第 {week} 周完成 {committed}/{planned}"
                                              f"（≥90%），建议下周计划量 {old} → {new}（上限 {cap}）；等待用户决定"})
        elif planned and committed < QUOTA_ATTENTION_BELOW * planned:
            kind = "dry-run" if not data.get("go") and committed == 0 else "attention"
            adjustments.append({
                "on": end, "kind": kind,
                "detail": f"第 {week} 周完成 {committed}/{planned}："
                          + ("未开闸（dry-run），不计完成率" if kind == "dry-run"
                             else "先查供给与读数，不自动降量")})
        supply = data.get("supply_state") or {}
        if supply.get("gap"):
            adjustments.append({"on": end, "kind": "supply",
                                "detail": f"未来供给缺口 {supply['gap']} 条："
                                          "先补内容，缺口不算完成"})

    def _deliver_weekly(self, data: Dict[str, Any], data_dir: Path,
                        card: Dict[str, Any], *, deliver: bool, log=print) -> None:
        text = result_card.render(card)
        label = f"week-{card.get('week'):02d}" if card.get("week") else "week"
        try:
            from .deliver import deliver_card
            outcome = deliver_card(data_dir, data["id"], "weekly", label, text,
                                   title=f"结果单 {data['id']} {card.get('week_start')}",
                                   to=str(data.get("owner") or ""),
                                   subject=f"{data['id']} 第 {card.get('week')} 周结果单",
                                   live=False)
            log(f"[grow] 结果单已落盘：{outcome.get('html') or outcome}")
        except Exception as exc:  # noqa: BLE001 投递失败不影响结果生成
            log(f"[grow] 结果单投递失败（已保留卡片）：{exc}")
        card["delivery"] = {"delivered": False, "mode": "local", "at": _today()}
        data["last_delivery"] = {"kind": "weekly", "week": card.get("week"),
                                 "at": _today()}

    # ---------- 决策 ----------

    @locked
    def decide(self, *, action: str, note: str = "", by: str = "",
               on: Optional[str] = None, dry_run: bool = False,
               data_dir=None) -> Dict[str, Any]:
        action = str(action or "").strip().lower()
        if action not in ("continue", "optimize"):
            raise GrowError("--action 只能是 continue 或 optimize")
        on = on or _today()
        data = self.load()
        proposal = None
        changes: List[Dict[str, Any]] = []
        if action == "optimize":
            proposal = self._proposal(data, Path(data_dir) if data_dir else self.path.parent.parent, on)
            changes = list(proposal.get("changes") or [])
            if not dry_run:
                changes = self._apply_proposal(data, changes, on)
                if changes and proposal.get("evidence_id"):
                    data.setdefault("applied_learning", []).append(proposal["evidence_id"])
                if changes:
                    self._write_briefs(Path(data_dir) if data_dir else self.path.parent.parent, data)
        elif dry_run:
            proposal = {"changes": [], "rationale": ["继续当前策略，不做参数调整"]}
        decision = {"at": datetime.now().isoformat(timespec="seconds"), "on": on,
                    "action": action, "note": (note or "")[:300], "by": by,
                    "changes": changes, "dry_run": bool(dry_run),
                    "snapshot": self._snapshot(data)}
        data.setdefault("decisions", []).append(decision)
        if not dry_run:
            self.save(data)
        return {"decision": decision, "proposal": proposal}

    def _snapshot(self, data: Dict[str, Any]) -> Dict[str, Any]:
        return {"plan": int((data.get("plan") or {}).get("actions_per_week") or 0),
                "supply": data.get("supply_state") or {},
                "status": data.get("status"),
                "go": bool(data.get("go"))}

    def _proposal(self, data: Dict[str, Any], data_dir: Path, on: str) -> Dict[str, Any]:
        from .grow_learning import latest_proposal
        learning = latest_proposal(data_dir, data.get("id") or "")
        if learning.get("changes"):
            evidence_id = hashlib.sha256(json.dumps(
                {k: learning.get(k) for k in ("changes", "sample", "buckets")},
                sort_keys=True, ensure_ascii=False).encode()).hexdigest()
            if evidence_id in data.get("applied_learning", []):
                return {"on": on, "changes": [], "source": "learning",
                        "rationale": ["这批证据的调整已应用；等待新的业务反馈"]}
            return {"on": on, "changes": learning["changes"], "evidence_id": evidence_id,
                    "rationale": [learning.get("recommendation", "")],
                    "observed": {"sample": learning.get("sample"),
                                 "buckets": learning.get("buckets")},
                    "rollback": {"content": "恢复原配比"}, "source": "learning"}
        plan = int((data.get("quota") or {}).get("actions_per_week") or 0)
        initial = int((data.get("quota") or {}).get("initial_actions_per_week") or plan or 1)
        supply = self._supply_state(data, data_dir, on,
                                    self._committed_targets(
                                        Ledger(data_dir / "ledger.jsonl"), data))
        staged = int(supply.get("staged") or 0)
        window_days = int((data.get("supply") or {}).get("min_cover_days") or 3)
        daily_quota = max(1, sum((data.get("quota", {}).get("daily_preview", {})
                                  .get(on, {}) or {"reply": 1}).values()))
        projected = int(round(staged / max(1, window_days) * 7)) if staged else 0
        committed_week = self._committed_in_range(data_dir, data, on, days=7)
        completion = committed_week / plan if plan else 0
        old = plan
        target = plan
        rationale: List[str] = []
        if projected < plan:
            target = max(1, projected)
            rationale.append(f"未来 {window_days} 天只有 {staged} 条内容（折合每周约 "
                             f"{projected} 条），低于计划 {plan}；先降计划对齐供给")
        elif completion >= 0.9:
            cap = round(SCALE_UP_CAP_RATIO * initial)
            target = min(cap, max(plan, round(plan * SCALE_UP_RATIO)))
            rationale.append(f"近 7 天完成率 {completion:.0%}（{committed_week}/{plan}），"
                             f"按规则加量到 {target}（上限 {cap}）")
        elif completion < QUOTA_ATTENTION_BELOW:
            target = max(1, round(plan * 0.9))
            rationale.append(f"近 7 天完成率 {completion:.0%}（{committed_week}/{plan}），"
                             f"先降 10% 找原因（{plan} → {target}）")
        else:
            rationale.append(f"近 7 天完成率 {completion:.0%}，介于 50%–90%，维持计划")
        changes = []
        if target != old:
            changes.append({"param": "actions_per_week", "from": old, "to": target,
                            "rollback": old,
                            "detail": f"周计划 {old} → {target}"})
        return {"on": on, "changes": changes, "rationale": rationale,
                "observed": {"committed_7d": committed_week, "plan": plan,
                             "staged": staged, "projected_weekly": projected,
                             "completion": round(completion, 3)},
                "rollback": {"actions_per_week": old}}

    def _committed_in_range(self, data_dir: Path, data: Dict[str, Any], on: str,
                            days: int) -> int:
        start = (date.fromisoformat(on) - timedelta(days=days - 1)).isoformat()
        ledger = Ledger(Path(data_dir) / "ledger.jsonl")
        n = 0
        for rec in ledger.all():
            if rec.get("result") != "committed":
                continue
            if not _belongs(str(rec.get("campaign") or ""), data["id"]):
                continue
            day = str(rec.get("ts") or "")[:10]
            if start <= day <= on:
                n += 1
        return n

    @staticmethod
    def _apply_proposal(data: Dict[str, Any], changes: List[Dict[str, Any]],
                        on: str) -> List[Dict[str, Any]]:
        applied = []
        for change in changes:
            if change.get("param") == "target.koc_share":
                target = change.get("to") or {}
                share = float(target.get("koc_share") or 0.7)
                share = max(0.3, min(0.9, share))
                data.setdefault("growth_strategy", {})["koc_share_target"] = share
                data.setdefault("adjustments", []).append({
                    "on": on, "kind": "decision", "param": "target.koc_share",
                    "from": change.get("from", "current"), "to": {"koc_share": share},
                    "detail": change.get("detail", ""),
                    "rollback": "恢复 KOC 占比目标 50%"})
                applied.append(change)
                continue
            if change.get("param") == "content.mix_weights":
                from . import grow_content
                target = change.get("to") or {}
                boost = str(target.get("boost") or "")
                reduce = str(target.get("reduce") or "")
                if not boost or not reduce:
                    continue
                content = data.setdefault("content", {})
                weights = grow_content.apply_weights(
                    content.get("formula_weights"), boost=boost, reduce=reduce)
                content["formula_weights"] = weights
                family = str((data.get("goal") or {}).get("family") or "")
                briefs = []
                for brief in (data.get("supply") or {}).get("briefs") or []:
                    briefs.append(grow_content.enrich_brief(
                        {k: v for k, v in brief.items() if k not in ("formulas", "mix", "rules")},
                        goal_family=family, formula_weights=weights))
                data.setdefault("supply", {})["briefs"] = briefs
                data.setdefault("adjustments", []).append({
                    "on": on, "kind": "decision", "param": "content.mix_weights",
                    "from": change.get("from", "current"), "to": target,
                    "detail": change.get("detail", ""),
                    "rollback": "恢复原配比"})
                applied.append(change)
                continue
            if change.get("param") != "actions_per_week":
                continue
            old = int((data.get("plan") or {}).get("actions_per_week") or 0)
            new = int(change.get("to") or old)
            data.setdefault("plan", {})["actions_per_week"] = new
            data.setdefault("quota", {})["actions_per_week"] = new
            data.setdefault("adjustments", []).append({
                "on": on, "kind": "decision", "param": "actions_per_week",
                "from": old, "to": new, "detail": change.get("detail", ""),
                "rollback": old})
            applied.append(change)
        return applied

    # ---------- 进度 / 状态 / 报告 ----------

    @staticmethod
    def _goal_progress(data: Dict[str, Any], data_dir: Path) -> Dict[str, Any]:
        from .attribution import OutcomeLedger as OL
        metric = (data.get("goal") or {}).get("metric")
        goal = data.get("goal") or {}
        if metric != "followers":
            return {"metric": metric, "current": None,
                    "note": "该指标暂无自动读数通道，人工核对后入账"}
        by_day = {}
        for rec in OL(Path(data_dir)).records(campaign=data.get("id")):
            if rec.get("kind") != "count" or rec.get("metric") != "x_followers":
                continue
            day = str(rec.get("window") or "")
            if day and day not in by_day:
                by_day[day] = int(rec.get("value") or 0)
        current = list(dict(sorted(by_day.items())).values())[-1] if by_day else None
        baseline = goal.get("baseline")
        target = goal.get("target")
        progress = None
        if isinstance(current, int) and isinstance(target, (int, float)) and target:
            if goal.get("target_kind") == "delta":
                if isinstance(baseline, (int, float)):
                    progress = round((current - baseline) / target * 100, 1)
            elif isinstance(baseline, (int, float)) and target > baseline:
                progress = round((current - baseline) / (target - baseline) * 100, 1)
        return {"metric": metric, "current": current, "baseline": baseline,
                "target": target, "target_kind": goal.get("target_kind"),
                "target_text": goal.get("target_text"), "progress": progress,
                "note": "" if current is not None else "还没有关注者读数（先跑 mkt x stats）"}

    def status(self, data_dir, *, on: Optional[str] = None) -> Dict[str, Any]:
        on = on or _today()
        data = self.load()
        data_dir = Path(data_dir)
        ledger = Ledger(data_dir / "ledger.jsonl")
        committed = self._committed_today(ledger, data, on)
        committed_targets = self._committed_targets(ledger, data)
        data["supply_state"] = self._supply_state(data, data_dir, on, committed_targets)
        week_start = gs.week_start_of(on, data["started"])
        daily = gs.split_week(int(data["quota"]["actions_per_week"]), week_start,
                              data["quota"].get("action_weights"))
        pending = [str(p) for p in self._discover_queues(data_dir, data["id"])]
        try:
            from .grow_ops import heartbeat_state
            heartbeat = heartbeat_state(data)
        except Exception:  # noqa: BLE001
            heartbeat = data.get("heartbeat") or {}
        decisions = [d for d in data.get("decisions") or [] if d.get("on") == on]
        return {
            "run": data["id"], "status": data["status"], "go": data.get("go"),
            "started": data["started"],
            "week": (result_card.week_bounds(data["started"], on) or [(0, "", "")])[-1][0],
            "goal": self._goal_progress(data, data_dir),
            "plan_actions_per_week": int(data["quota"]["actions_per_week"]),
            "today_quota": daily.get(on, {}),
            "committed_today": len(committed),
            "supply": data.get("supply_state") or {},
            "heartbeat": heartbeat,
            "readings": (data.get("telemetry", {}).get("days", {})
                         .get(on, {}).get("readings") or {}),
            "pending_decision": not decisions,
            "queues": len(pending),
            "result_cards": data.get("result_cards") or [],
            "daily_cards": len(data.get("daily_cards") or []),
            "adjustments": len(data.get("adjustments") or []),
            "policy_scope": self.policy_scope(data_dir),
            "automation": data.get("automation") or {},
        }

    def report(self, data_dir, *, week: Optional[int] = None,
               on: Optional[str] = None) -> str:
        data = self.load()
        cards = data.get("result_cards") or []
        if not cards:
            return (f"{data['id']}：还没有完成整周（开始于 {data['started']}，"
                    "周窗口走完自动出第一张结果单）")
        if week is None:
            latest = cards[-1]
        else:
            hit = [c for c in cards if int(c.get("week") or 0) == int(week)]
            if not hit:
                return f"{data['id']}：第 {week} 周没有结果单（现有：" + \
                       "、".join(str(c.get("week")) for c in cards) + "）"
            latest = hit[-1]
        return result_card.render(latest)

    # ---------- 目标发现 / 业务复盘 / 常驻 / 授权范围 ----------

    def targets(self, data_dir, queries, *, limit: int = 10,
                max_age_hours: float = 48, min_likes: int = 0,
                min_replies: int = 0, pages: int = 1,
                on: Optional[str] = None) -> Dict[str, Any]:
        """只读发现回复目标：排除自己、已发目标、已暂存目标。"""
        from . import grow_targets
        data = self.load()
        on = on or _today()
        data_dir = Path(data_dir)
        readers = (data.get("measurement") or {}).get("readers") or []
        own = [str(r.get("handle") or "") for r in readers if r.get("handle")]
        ledger = Ledger(data_dir / "ledger.jsonl")
        exclude_urls = self._committed_targets(ledger, data)
        for path in self._discover_queues(data_dir, data["id"]):
            queue = self._load_queue(path)
            for item in queue.get("items") or []:
                if isinstance(item, dict) and item.get("target"):
                    exclude_urls.add(str(item["target"]))
                    exclude_urls.add(str(item["target"]).removeprefix("tweet:"))
        rows = grow_targets.discover(
            list(queries or []), limit=limit, max_age_hours=max_age_hours,
            min_likes=min_likes, min_replies=min_replies, pages=pages,
            exclude_handles=own, exclude_urls=exclude_urls)
        path = grow_targets.save_candidates(data_dir, data["id"], rows, on=on)
        return {"candidates": rows, "path": str(path), "count": len(rows),
                "excluded_urls": len(exclude_urls)}

    def _engaged_handles(self, data: Dict[str, Any]) -> set:
        out = set()
        for meta in (data.get("content_index") or {}).values():
            handle = str((meta or {}).get("author_handle") or "").lstrip("@").lower()
            if handle:
                out.add(handle)
        try:
            from . import grow_fit
            for handle, row in grow_fit.load_watchlist(
                    self.path.parent.parent, data["id"]).items():
                if row.get("last_engaged_at"):
                    out.add(handle.lstrip("@").lower())
        except Exception:  # noqa: BLE001 名单缺失不影响候选
            pass
        return out

    def watchlist(self, data_dir) -> Dict[str, Any]:
        from . import grow_fit
        accounts = grow_fit.load_watchlist(Path(data_dir), self.load()["id"])
        return {"accounts": accounts, "count": len(accounts),
                "rendered": grow_fit.render_watchlist(accounts)}

    def metrics(self, data_dir, *, days: int = 7, limit: int = 20,
                on: Optional[str] = None, reader=None,
                resolve: bool = False, _data=None) -> Dict[str, Any]:
        """采集最近 N 天已 committed 帖子的公开指标（查看/喜欢/回复）。"""
        from . import grow_learning
        data = _data if _data is not None else self.load()
        data_dir = Path(data_dir)
        end = date.fromisoformat(on or _today())
        start = (end - timedelta(days=max(1, int(days)) - 1)).isoformat()
        ledger = Ledger(data_dir / "ledger.jsonl")
        if resolve:
            cursor = date.fromisoformat(start)
            while cursor <= end:
                self._resolve_post_urls(data, data_dir, cursor.isoformat(), limit=3)
                cursor += timedelta(days=1)
            self.save(data)
            ledger = Ledger(data_dir / "ledger.jsonl")
        rows = []
        for rec in ledger.all():
            if rec.get("result") != "committed":
                continue
            if not _belongs(str(rec.get("campaign") or ""), data["id"]):
                continue
            day = str(rec.get("ts") or "")[:10]
            if not (start <= day <= end.isoformat()):
                continue
            target = self._key(str(rec.get("target") or ""))
            if rec.get("channel") != "x":
                continue
            published = (data.get("post_urls") or {}).get(self._key(target)) or ""
            if published:
                rows.append({"target": target, "published_url": published, "on": day})
        if not rows:
            skipped = sum(1 for rec in ledger.all()
                          if rec.get("result") == "committed"
                          and _belongs(str(rec.get("campaign") or ""), data["id"])
                          and str(rec.get("ts") or "")[:10] >= start)
            return {"pending": 0, "written": 0, "items": [], "skipped": skipped,
                    "note": "没有可采集的自己帖子 URL：加 --resolve 让引擎回原帖解析，"
                            "或先跑 tick" if skipped else "最近没有已发布帖子"}
        return grow_learning.collect(data_dir, data["id"], rows=rows,
                                     limit=limit, reader=reader, on=end.isoformat())

    def learn(self, data_dir, *, on: Optional[str] = None) -> Dict[str, Any]:
        """按公式/窗口归因，输出样本与调参建议（样本不足只给建议）。"""
        from . import grow_learning
        data = self.load()
        data_dir = Path(data_dir)
        payload = grow_learning.analyze(data_dir, data["id"], data)
        payload["on"] = on or _today()
        path = grow_learning.write_learning(data_dir, data["id"], payload,
                                            on=payload["on"])
        payload["path"] = str(path)
        payload["rendered"] = grow_learning.render(payload)
        return payload

    def prospects(self, data_dir, queries, *, limit: int = 20, top: int = 8,
                  max_age_hours: float = 48, min_likes: int = 0,
                  pages: int = 1, on: Optional[str] = None,
                  enricher=None, koc_only: Optional[bool] = None,
                  min_followers: Optional[int] = None,
                  max_followers: Optional[int] = None,
                  include_engaged: bool = False) -> Dict[str, Any]:
        """账号级候选：先搜帖子，再读作者主页，按契合度排序。"""
        from . import grow_fit
        data = self.load()
        data_dir = Path(data_dir)
        on = on or _today()
        if koc_only is None:
            strategy = data.get("growth_strategy") or {}
            koc_only = strategy.get("focus") == "koc" and strategy.get("phase") == "early"
        profile = data.get("fit_profile") or {}
        facts_ref = str(data.get("facts_ref") or "")
        if facts_ref and Path(facts_ref).is_file():
            try:
                profile = grow_fit.product_profile(
                    json.loads(Path(facts_ref).read_text(encoding="utf-8")))
            except (json.JSONDecodeError, OSError):
                pass
        queries = [str(q).strip() for q in (queries or []) if str(q).strip()]
        if not queries:
            queries = grow_fit.auto_queries(profile)
        engaged = self._engaged_handles(data)
        target_result = self.targets(
            data_dir, queries, limit=limit, max_age_hours=max_age_hours,
            min_likes=min_likes, pages=pages, on=on)
        rows = target_result.get("candidates") or []
        # 第一段：只按帖子分挑作者，避免把主页读取配额花在无关账号。
        # KOC 只看 top 个不够：粉丝量要先读主页才知道，多读几个才有机会筛到 KOC。
        enrich_limit = min(max(1, int(limit)), max(int(top) * 2, 10))
        handles = grow_fit.preselect_handles(rows, profile, limit=enrich_limit)
        accounts = {}
        for handle in handles:
            try:
                account = (enricher(handle) if enricher is not None
                           else grow_fit.enrich_account(handle))
            except Exception as exc:  # noqa: BLE001 读主页失败只降级评分
                account = {"handle": handle, "note": f"主页读取失败：{exc}"}
            accounts[handle] = account or {"handle": handle}
        ranked = grow_fit.rank_prospects(
            rows, profile, accounts,
            exclude_handles=() if include_engaged else engaged)
        prospects = []
        for row in ranked:
            followers = (row.get("account") or {}).get("followers")
            if koc_only and not row.get("is_koc"):
                continue
            if min_followers is not None and (not isinstance(followers, int)
                                              or followers < min_followers):
                continue
            if max_followers is not None and (not isinstance(followers, int)
                                              or followers > max_followers):
                continue
            prospects.append(row)
        prospects = grow_fit.select_prospect_mix(
            prospects, limit=top, koc_only=koc_only,
            koc_share=(data.get("growth_strategy") or {}).get("koc_share_target", 0.6))
        path = grow_fit.save_prospects(data_dir, data["id"], prospects, on=on)
        watchlist_path = grow_fit.update_watchlist(
            data_dir, data["id"], ranked, on=on, engaged=engaged)
        koc_count = sum(1 for row in ranked if row.get("is_koc"))
        near = [{"handle": row["handle"], "score": row["score"],
                 "koc_tier": row.get("koc_tier"),
                 "followers": (row.get("account") or {}).get("followers"),
                 "why_not": (row.get("koc_risks") or row.get("risks") or [""])[0]}
                for row in ranked[:3]] if koc_only and not prospects else []
        return {"prospects": prospects, "path": str(path),
                "watchlist": str(watchlist_path),
                "queries": queries, "count": len(prospects),
                "targets": len(rows), "enriched": len(handles),
                "koc": koc_count, "koc_only": koc_only,
                "near_misses": near,
                "excluded_engaged": 0 if include_engaged else len(engaged)}

    def review(self, data_dir, *, days: int = 7, on: Optional[str] = None) -> Dict[str, Any]:
        """最近 N 天的业务复盘：发布、读数、供给、决策与建议。"""
        data = self.load()
        data_dir = Path(data_dir)
        on = on or _today()
        days = max(1, int(days))
        start = (date.fromisoformat(on) - timedelta(days=days - 1)).isoformat()
        ledger = Ledger(data_dir / "ledger.jsonl")
        days_data = data.get("telemetry", {}).get("days") or {}
        rows = []
        total_published = 0
        coverage = 0
        for i in range(days):
            day = (date.fromisoformat(start) + timedelta(days=i)).isoformat()
            committed = [rec for rec in ledger.all()
                         if rec.get("result") == "committed"
                         and _belongs(str(rec.get("campaign") or ""), data["id"])
                         and str(rec.get("ts") or "")[:10] == day]
            week_start = gs.week_start_of(day, data["started"])
            plan = gs.split_week(int(data["quota"]["actions_per_week"]), week_start,
                                 data["quota"].get("action_weights")).get(day, {})
            readings = ((days_data.get(day) or {}).get("readings") or {})
            counts = ((days_data.get(day) or {}).get("counts") or {})
            failed = sum(int(v.get("failed") or 0) for v in counts.values())
            duplicates = sum(int(v.get("duplicates") or 0) for v in counts.values())

            if readings.get("followers") is not None:
                coverage += 1
            total_published += len(committed)
            rows.append({"date": day, "published": len(committed),
                         "planned": sum(int(v) for v in plan.values()),
                         "followers": readings.get("followers"),
                         "impressions": readings.get("impressions"),
                         "reading_error": readings.get("error"),
                         "failed": failed, "duplicates": duplicates})
        goal = self._goal_progress(data, data_dir)
        decisions = [d for d in data.get("decisions") or []
                     if start <= str(d.get("on") or "") <= on]
        supply = data.get("supply_state") or {}
        advice = []
        if coverage < max(1, min(3, days)):
            advice.append("读数覆盖过低：先修读数（handle/CDP），不要用这张表判断策略")
        if int(supply.get("gap") or 0) > 0:
            advice.append(f"供给缺口 {supply['gap']} 条：先补内容，缺口不算完成")
        if not decisions:
            advice.append("这几天没有记录决定：看完复盘用 mkt grow decide 明确继续或优化")
        if not advice:
            advice.append("数据与供给正常：默认继续；若要加量，等完成率与读数覆盖都稳定")
        lines = [f"# 业务复盘 · {data['id']}（{start} ~ {on}）", "",
                 f"目标：{goal.get('target_text') or goal.get('metric')}"
                 f"｜当前 {goal.get('current') if goal.get('current') is not None else '取不到'}"
                 f"｜基线 {goal.get('baseline') if goal.get('baseline') is not None else '取不到'}"
                 + (f"｜{goal['progress']}%" if goal.get("progress") is not None else ""),
                 "",
                 f"汇总：发布 {total_published} 条｜读数覆盖 {coverage}/{days} 天"
                 f"｜供给 已暂存 {supply.get('staged', 0)} 条"
                 + (f"（缺 {supply['gap']}）" if supply.get("gap") else ""),
                 "",
                 "| 日期 | 发布/计划 | 关注者 | 曝光 | 失败 | 重复 |",
                 "|---|---|---|---|---|---|"]
        for row in rows:
            lines.append(f"| {row['date']} | {row['published']}/{row['planned']} | "
                         f"{row['followers'] if row['followers'] is not None else '取不到'} | "
                         f"{row['impressions'] if row['impressions'] is not None else '取不到'} | "
                         f"{row['failed']} | {row['duplicates']} |")
        lines += ["", "## 决定记录", ""]
        if decisions:
            for item in decisions:
                lines.append(f"- {item.get('on')} {item.get('action')}"
                             + (f"：{item.get('note')}" if item.get("note") else ""))
        else:
            lines.append("- 无（默认继续运行）")
        today_row = rows[-1] if rows else {}
        shortfalls: List[str] = []
        next_actions: List[str] = []
        if today_row.get("planned") and today_row.get("published", 0) < today_row["planned"]:
            shortfalls.append(f"今天只发 {today_row.get('published', 0)}/"
                              f"{today_row['planned']} 条：先查供给与窗口")
            next_actions.append("补足明天配额：mkt grow targets → 写内容 → mkt grow stage")
        if today_row.get("followers") is None:
            shortfalls.append("今天没有拿到关注者读数")
            next_actions.append("修读数：mkt grow doctor；手动补跑 mkt grow tick")
        if int(supply.get("gap") or 0) > 0:
            shortfalls.append(f"未来供给缺 {supply['gap']} 条")
            next_actions.append("按 briefs.md 生产内容，优先填最近 3 天")
        targets_file = data_dir / "grow" / data["id"] / "targets" / f"{on}.json"
        if not targets_file.is_file():
            shortfalls.append("今天没有做目标发现（没有候选清单）")
            next_actions.append('mkt grow targets %s --query "<产品相关搜索词>"' % data["id"])
        queue_scores = []
        for path in self._discover_queues(data_dir, data["id"]):
            for item in (self._load_queue(path).get("items") or []):
                if isinstance(item, dict) and item.get("content_score") is not None:
                    queue_scores.append(int(item["content_score"]))
        index = data.get("content_index") or {}
        koc_targets, koc_touches = set(), 0
        for target, meta in index.items():
            if not meta.get("koc"):
                continue
            koc_targets.add(target)
            if any(target == str(rec.get("target") or "").removeprefix("tweet:")
                   or target == str(rec.get("target") or "")
                   for rec in ledger.all()
                   if rec.get("result") == "committed"
                   and _belongs(str(rec.get("campaign") or ""), data["id"])
                   and start <= str(rec.get("ts") or "")[:10] <= on):
                koc_touches += 1
        koc_share = round(koc_touches / total_published, 2) if total_published else 0.0
        range_scores = [int(meta.get("score") or 0) for meta in index.values()
                        if start <= str(meta.get("staged_at") or "") <= on]
        content_avg = round(sum(range_scores) / len(range_scores), 1) if range_scores else None
        prospects_dir = data_dir / "grow" / data["id"] / "prospects"
        prospect_dates = sorted(p.stem for p in prospects_dir.glob("*.json")) \
            if prospects_dir.is_dir() else []
        latest_prospect = prospect_dates[-1] if prospect_dates else ""
        try:
            from . import grow_learning
            metric_rows = [row for row in grow_learning.load_metrics(data_dir, data["id"])
                           if start <= str(row.get("read_at") or "")[:10] <= on]
        except Exception:  # noqa: BLE001
            metric_rows = []
        from . import grow_fit as _fit
        watch_count = len(_fit.load_watchlist(data_dir, data["id"]))
        koc_target_share = float((data.get("growth_strategy") or {})
                                 .get("koc_share_target", 0.5))
        if total_published and koc_share < koc_target_share:
            shortfalls.append(
                f"KOC 触达占比 {int(koc_share * 100)}%，低于目标 "
                f"{int(koc_target_share * 100)}%")
            next_actions.append("先用 mkt grow prospects --koc-only 找 KOC，再写回复")
        if queue_scores and sum(queue_scores) / len(queue_scores) < 60:
            shortfalls.append(f"暂存内容结构分均值 "
                              f"{round(sum(queue_scores) / len(queue_scores))}，偏低")
            next_actions.append("按内容公式重写：mkt grow formulas；先同意具体观点再补事实")
        try:
            from . import grow_learning
            metric_count = len(grow_learning.load_metrics(data_dir, data["id"]))
        except Exception:  # noqa: BLE001
            metric_count = 0
        if metric_count == 0:
            shortfalls.append("还没有采集帖子指标，无法判断哪种内容有效")
            next_actions.append(f"mkt grow metrics {data['id']} --resolve → "
                                f"mkt grow learn {data['id']}")
        if content_avg is not None and content_avg < 60:
            shortfalls.append(f"近 {days} 天暂存内容结构分均值 {content_avg}，偏低")
            next_actions.append("按 mkt grow formulas 重写；回复结尾要带真问题")
        if not latest_prospect:
            shortfalls.append("还没有 KOC 候选名单")
            next_actions.append(f"mkt grow prospects {data['id']} --koc-only")
        if latest_prospect and latest_prospect < on:
            shortfalls.append(f"最近一次账号发现是 {latest_prospect}，名单已过期")
            next_actions.append("重新跑 prospects，更新 KOC 名单")
        stale_total = 0
        for path in self._discover_queues(data_dir, data["id"]):
            stale_total += self._count_stale(
                self._load_queue(path), on=on,
                committed_targets=self._committed_targets(ledger, data))
        if stale_total:
            shortfalls.append(f"有 {stale_total} 条内容过期未发（超过 {STALE_DAYS} 天）")
            next_actions.append("检查配额与时间窗；过期内容换新帖，不补发陈旧回复")
        if not decisions:
            shortfalls.append("今天没有记录决定（默认继续）")
            next_actions.append(f"mkt grow decide {data['id']} --action continue|optimize")
        lines += ["", "## 内容与名单", "",
                  f"- 暂存内容结构分均值："
                  f"{content_avg if content_avg is not None else '取不到'}（近 {days} 天）",
                  f"- KOC 名单：{watch_count} 人",
                  f"- 最近账号发现：{latest_prospect or '无'}",
                  f"- 已采指标：{len(metric_rows)} 条",
                  f"- 过期未发：{stale_total} 条",
                  f"- KOC 触达：{koc_touches} 位（占发布 {int(koc_share * 100)}%）"]
        lines += ["", "## 今日不足与明天动作", ""]
        if shortfalls:
            lines += [f"- 不足：{item}" for item in shortfalls]
            lines += [f"- 明天：{item}" for item in next_actions]
        else:
            lines.append("- 今天没有发现结构性缺口；按当前节奏继续，不额外加量")
        lines += ["", "## 建议", ""] + [f"- {item}" for item in advice]
        lines += ["", "边界：committed 只证明发出，不代表转化；读数 UNVERIFIED 期间只做方向参考；"
                      "建议不替代你的判断。", ""]
        text = "\n".join(lines)
        paths = {}
        try:
            from .deliver import write_card
            paths = write_card(data_dir, data["id"], "review", on, text,
                               title=f"业务复盘 {data['id']} {on}")
        except Exception:  # noqa: BLE001
            fallback = data_dir / "grow" / data["id"] / "reviews" / f"{on}.md"
            fallback.parent.mkdir(parents=True, exist_ok=True)
            fallback.write_text(text, encoding="utf-8")
            paths = {"markdown": str(fallback)}
        summary = {"days": days, "start": start, "end": on,
                   "published": total_published, "coverage": coverage,
                   "koc_touches": koc_touches, "koc_share": koc_share,
                   "content_avg": content_avg,
                   "prospect_date": latest_prospect,
                   "metrics_collected": len(metric_rows),
                   "supply": supply, "advice": advice,
                   "shortfalls": shortfalls, "next_actions": next_actions}
        data.setdefault("reviews", [])
        data["reviews"] = [row for row in data["reviews"]
                           if str(row.get("end") or "") != on]
        data["reviews"].append(summary)
        data["reviews"] = data["reviews"][-30:]
        self.save(data)
        return {"markdown": text, "paths": paths, "summary": summary}

    def policy_scope(self, data_dir) -> Dict[str, Any]:
        """授权范围 vs 本 run 需要的动作：暴露过宽授权，便于收窄。"""
        from .execution_policy import get_policy
        data = self.load()
        policy = get_policy(Path(data_dir)) or {}
        allowed = [str(a) for a in (policy.get("allow_actions")
                                    or policy.get("actions") or [])]
        needed = set(str(a) for a in data.get("actions") or [])
        extra = sorted(a for a in allowed if a not in needed)
        return {"allowed": sorted(allowed), "needed": sorted(needed),
                "extra": extra, "broad": bool(extra),
                "enabled": bool(policy.get("enabled", True)),
                "expires_at": policy.get("expires_at")}

    def write_loop_script(self, data_dir, *, interval_minutes: int = 30,
                          deliver: bool = True) -> Path:
        """生成前台常驻脚本（launchd 被 TCC 拦住时的替代方案）。"""
        data = self.load()
        data_dir = Path(data_dir).resolve()
        root = Path(__file__).resolve().parents[1]
        log_path = data_dir / "grow" / data["id"] / "tick.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        flag = " --deliver" if deliver else ""
        script = f"""#!/usr/bin/env bash
# 由 mkt grow install 生成：前台常驻 tick（Ctrl-C 停止）。
set -u
cd {shlex.quote(str(root))}
export MKT_HOME={shlex.quote(str(data_dir.resolve()))}
while true; do
  {shlex.quote(sys.executable)} {shlex.quote(str(root / "mkt.py"))} grow tick {shlex.quote(data['id'])}{flag} >> {shlex.quote(str(log_path))} 2>&1
  sleep {max(1, int(interval_minutes)) * 60}
done
"""
        path = data_dir / "grow" / data["id"] / "run-loop.command"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(script, encoding="utf-8")
        path.chmod(0o755)
        return path

    def daemon(self, data_dir, **kwargs):
        data = self.load()
        with run_lock(Path(data_dir) / "grow" / data["id"] / "daemon-instance"):
            return self._daemon(data_dir, **kwargs)

    def _daemon(self, data_dir, *, interval_minutes: int = 30, deliver: bool = True,
               once: bool = False, tick_fn=None, sleep=time.sleep, log=print) -> None:
        """前台常驻：单实例锁 + 定时 tick；Ctrl-C 退出。"""
        data = self.load()
        data_dir = Path(data_dir)
        lock = data_dir / "grow" / data["id"] / "daemon.lock"
        lock.parent.mkdir(parents=True, exist_ok=True)
        if lock.exists():
            try:
                pid = int(lock.read_text(encoding="utf-8").strip() or 0)
            except ValueError:
                pid = 0
            if pid and pid != os.getpid():
                try:
                    os.kill(pid, 0)
                    raise GrowError(f"已有 daemon 在跑（pid={pid}）；先停掉再启动")
                except ProcessLookupError:
                    lock.unlink(missing_ok=True)
        lock.write_text(str(os.getpid()), encoding="utf-8")
        tick = tick_fn or (lambda: self.tick(data_dir, deliver=deliver, log=log))
        try:
            while True:
                try:
                    outcome = tick()
                    if isinstance(outcome, dict):
                        log(f"[grow] tick 完成：status={outcome.get('status') or outcome.get('skipped')}，"
                            f"队列={len(outcome.get('ran') or [])}")
                    if self.load().get("status") == "completed":
                        return
                except Exception as exc:
                    log(f"[grow] 本轮异常，暂停执行等待检查：{type(exc).__name__}: {exc}")
                    with run_lock(self.path):
                        latest = self.load()
                        latest["status"] = "needs_attention"
                        latest["last_error"] = {"at": _now_utc().isoformat(),
                                                "detail": str(exc)[:400]}
                        self.save(latest)
                if once:
                    return
                sleep(max(1, int(interval_minutes)) * 60)
        except KeyboardInterrupt:
            log("[grow] daemon 已停止")
        finally:
            lock.unlink(missing_ok=True)

    # ---------- 读数校准 ----------

    @locked
    def calibrate(self, *, followers: Optional[int] = None, match: bool = True,
                  note: str = "", by: str = "", on: Optional[str] = None) -> Dict[str, Any]:
        """记录一次人工对数；连续 7 次一致后把读数标为 PROVEN。

        「人工对数」= 人打开 X 后台手读一次粉丝数，与当天自动读数比对。
        这是读数从 UNVERIFIED 升级的唯一路径，不接受自动判定。
        """
        on = on or _today()
        data = self.load()
        readers = (data.get("measurement") or {}).get("readers") or []
        handle = next((str(r.get("handle") or "") for r in readers), "")
        reading = ((data.get("telemetry", {}).get("days", {}).get(on, {})
                    or {}).get("readings") or {})
        auto_value = reading.get("followers")
        if followers is not None and auto_value is not None and int(followers) != int(auto_value):
            match = False
        calibration = data.setdefault("measurement", {}).setdefault(
            "calibration", {"due": "", "checks": []})
        checks = calibration.setdefault("checks", [])
        checks.append({"on": on, "handle": handle, "auto": auto_value,
                       "human": followers, "match": bool(match),
                       "note": (note or "")[:200], "by": by,
                       "at": datetime.now().isoformat(timespec="seconds")})
        streak = 0
        for check in reversed(checks):
            if check.get("match"):
                streak += 1
            else:
                break
        promoted = False
        if streak >= 7:
            data["measurement"]["verification"] = "PROVEN"
            calibration["promoted_at"] = on
            promoted = True
        calibration["streak"] = streak
        calibration["due"] = on if not promoted else ""
        data.setdefault("history", []).append(
            {"at": on, "kind": "calibrate", "by": by,
             "note": f"{'一致' if match else '不一致'}；连续 {streak} 次"
                     + ("；已升级 PROVEN" if promoted else "")})
        self.save(data)
        return {"on": on, "auto": auto_value, "human": followers,
                "match": bool(match), "streak": streak,
                "verification": data["measurement"].get("verification"),
                "promoted": promoted, "checks": len(checks)}

    # ---------- 体检 / 安装 ----------

    def doctor(self, data_dir, *, now: Optional[datetime] = None) -> Dict[str, Any]:
        data = self.load()
        try:
            from .grow_ops import doctor as _doctor
            return _doctor(Path(data_dir), data, now=now)
        except Exception as exc:  # noqa: BLE001 运维模块缺失时给最小体检
            return {"ok": False, "checks": [
                {"name": "grow_ops", "ok": False, "level": "warn",
                 "detail": f"体检模块不可用：{exc}", "fix": "检查 core/grow_ops.py"}]}

    def install(self, data_dir, *, interval_minutes: int = 30,
                load: bool = False) -> Dict[str, Any]:
        data = self.load()
        data_dir = Path(data_dir).resolve()
        try:
            from .grow_ops import crontab_line, launchd_plist
        except Exception as exc:  # noqa: BLE001
            raise GrowError(f"安装模块不可用：{exc}") from exc
        root = Path(__file__).resolve().parents[1]
        out_dir = data_dir / "grow" / data["id"]
        log_path = str(out_dir / "tick.log")
        plist = launchd_plist(
            label=f"com.growovo.{data['id']}",
            python=sys.executable, mkt_path=str(root / "mkt.py"),
            run_id=data["id"], interval_minutes=interval_minutes,
            log_path=log_path, cwd=str(root), workspace=str(data_dir.resolve()))
        plist_path = out_dir / f"com.growovo.{data['id']}.plist"
        result = {"plist": plist, "path": str(plist_path),
                  "command": crontab_line(python=sys.executable,
                                          mkt_path=str(root / "mkt.py"),
                                          run_id=data["id"],
                                          interval_minutes=interval_minutes,
                                          log_path=log_path, cwd=str(root), workspace=str(data_dir.resolve()))}
        _write_text(plist_path, plist)
        result["fallback"] = str(self.write_loop_script(
            data_dir, interval_minutes=interval_minutes))
        if load:
            try:
                from .grow_ops import install_launchd
                result["installed"] = install_launchd(
                    out_path=Path.home() / "Library" / "LaunchAgents"
                    / f"com.growovo.{data['id']}.plist",
                    plist=plist, load=True)
            except Exception as exc:  # noqa: BLE001
                result["error"] = str(exc)[:200]
            time.sleep(2)
            log_path = Path(result.get("log") or log_path)
            tail = ""
            if log_path.is_file():
                tail = log_path.read_text(encoding="utf-8", errors="replace")[-4000:]
            permission_denied = ("Operation not permitted" in tail
                                 or "Full Disk Access" in tail)
            installed = result.get("installed") or {}
            result["verified"] = False
            result["ok"] = bool(installed.get("loaded")) and not permission_denied
            result["note"] = "注册状态不代表成功运行；需检查新一轮 heartbeat 和 tick.log"
            if permission_denied:
                result["reason"] = ("launchd 读不到工作区（macOS 文档目录保护）："
                                    "给 python3 完全磁盘访问权限后重装，"
                                    f"或直接双击 {result['fallback']}")
        return result
