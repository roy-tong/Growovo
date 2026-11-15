#!/usr/bin/env python3
"""效果归因 —— 事件与漏斗台账（Growovo「量」的第二块）。

data/outcomes/events.jsonl 每行一条（两种记录）：
  事件（有主体）：{kind:"event", ts, subject, event_type, channel, touch,
                  source_ref, approximated:false, verification?}
  计数（无主体的聚合快照）：{kind:"count", ts, metric, value, window,
                            source_ref, approximated}

event_type 漏斗序（价值事件的口径声明在先）：
  signal（访问/曝光）→ signup（注册，允许近似口径但必须标 approximated）
  → activation（产品内关键动作）→ paid（首笔付费）

纪律：
1. 时间先后 ≠ 因果。本模块只输出 last_touch / first_touch 两种**记账口径**，
   不输出「渠道导致转化」的因果结论；
2. 计数必须带 source_ref 与 window；近似口径（如 GitHub clone 近似注册）
   必须显式 approximated=true，报告中原样展示，不得洗白成真实事件；
3. signup 近似口径不得升级为 activation/paid——价值事件层级只认产品内
   真实发生的行为；
4. activation/paid 必须带 source_ref 与 verification{method,ref}（写入侧强制）——
   结果事件必须带有效来源与核验状态；存量缺核验的价值事件读取侧单列，
   不静默剔除；
5. 漏斗主体经 contacts.jsonl（id + 各平台身份）归一后再去重——同一人的
   GitHub handle 与访客 id 不得算成两个主体；阶段间时序倒挂单列披露。
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

FUNNEL = ("signal", "signup", "activation", "paid")
VALUE_EVENTS = ("activation", "paid")


class AttributionError(ValueError):
    pass


class LedgerReadError(RuntimeError):
    """资格审计读不出来时拒绝给出已验证计数：宁可不回答，也不给错答案。"""


def read_qualification_log(path):
    """严格读取 ``outcomes/qualifications.jsonl``。

    G-11：坏行、非布尔 ``qualifies`` 都不许被跳过或按真值猜——撤销记录一旦
    读丢，被撤销的事件就会重新算成有效结果。返回 ``event_id -> 最新记录``。
    """
    path = Path(path)
    latest = {}
    if not path.is_file():
        return latest
    with path.open(encoding="utf-8") as fh:
        for number, line in enumerate(fh, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError as exc:
                raise LedgerReadError(
                    "%s 第 %d 行不是合法 JSON（%s）：资格审计损坏时不能给出已验证计数"
                    % (path, number, exc)) from exc
            qualifies = rec.get("qualifies")
            if not isinstance(qualifies, bool):
                raise LedgerReadError(
                    "%s 第 %d 行的 qualifies 不是布尔（%r）：不能按真值猜，"
                    "撤销与否必须明确" % (path, number, qualifies))
            key = str(rec.get("event_id") or "")
            if key:
                latest[key] = rec
    return latest


class OutcomeLedger:
    def __init__(self, data_dir):
        self.data_dir = Path(data_dir)
        self.path = self.data_dir / "outcomes" / "events.jsonl"
        self.contacts_path = self.data_dir / "contacts.jsonl"
        self._identity_map_cache = None



    def _qualification_state(self):
        """严格读取资格审计，并按文件状态缓存（外部追加后必须失效）。

        G-11：读不出来就抛 LedgerReadError，绝不当成「没有撤销」。
        """
        path = self.data_dir / "outcomes" / "qualifications.jsonl"
        try:
            stat = path.stat()
            stamp = (stat.st_mtime_ns, stat.st_size)
        except OSError:
            stamp = None
        if getattr(self, "_qual_stamp", "unset") == stamp:
            return self._qual_cache
        latest = read_qualification_log(path)
        self._qual_stamp = stamp
        self._qual_cache = latest
        self._revoked_cache = {"product_events:%s" % k
                               for k, rec in latest.items()
                               if not rec.get("qualifies")}
        return latest

    def _latest_qualifications(self):
        """G-07/G-11: event_id → 最新一条资格审计（严格读取，供披露）。"""
        return self._qualification_state()

    def _revoked_import_keys(self):
        """G-07: 最新资格为 False 的事件，其投影行不再进入任何业务读数。"""
        self._qualification_state()
        return self._revoked_cache

    # ---- 身份归一 ----
    def _identity_map(self):
        """contacts.jsonl → {contact_id 或平台身份值: contact_id}；无库/无文件则空表。"""
        if self._identity_map_cache is not None:
            return self._identity_map_cache
        mapping = {}
        if self.contacts_path.is_file():
            for line in self.contacts_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                cid = rec.get("id")
                if not cid:
                    continue
                mapping[str(cid)] = cid
                for value in (rec.get("platforms") or {}).values():
                    if value:
                        mapping[str(value)] = cid
        self._identity_map_cache = mapping
        return mapping

    def _canonical(self, subject):
        return self._identity_map().get(str(subject), subject)

    def canonical_subject(self, subject):
        """公开的身份归一入口：contacts.jsonl 有该平台身份时返回 contact id。

        归并展示（如 ProspectJourney）与漏斗计数必须用同一套归一口径，否则
        同一个人会在两个地方算成两个主体。"""
        return self._canonical(subject)

    # ---- 写入 ----
    def add_event(self, *, subject: str, event_type: str, channel: str = "",
                  touch: str = "", source_ref: str = "", approximated: bool = False,
                  verification: dict | None = None, campaign: str = "",
                  import_key: str | None = None, ts: str | None = None) -> dict:
        for name, value in (("subject", subject), ("event_type", event_type)):
            if not str(value or "").strip():
                raise AttributionError(f"{name} 不能为空")
        if event_type not in FUNNEL:
            raise AttributionError(f"event_type 只允许 {FUNNEL}")
        if event_type in VALUE_EVENTS and approximated:
            raise AttributionError("activation/paid 不允许近似口径——价值事件必须真实发生")
        if verification is not None:
            method = str((verification or {}).get("method") or "").strip()
            ref = str((verification or {}).get("ref") or "").strip()
            if not method or not ref:
                raise AttributionError(
                    "verification 必须同时带 method 与 ref（如何核验、核验依据）")
            verification = {"status": "verified", "method": method, "ref": ref}
        if event_type in VALUE_EVENTS:
            if not str(source_ref or "").strip():
                raise AttributionError(
                    f"{event_type} 必须带 source_ref——价值事件必须有有效来源")
            if verification is None:
                raise AttributionError(
                    f"{event_type} 必须带 verification(method,ref)——价值事件必须有核验状态")
        rec = {
            "kind": "event",
            "ts": ts or datetime.now().isoformat(timespec="seconds"),
            "subject": subject,
            "event_type": event_type,
            "channel": channel,
            "touch": touch,
            "source_ref": source_ref,
            "approximated": bool(approximated),
        }
        if campaign:
            rec["campaign"] = campaign
        if import_key:
            rec["import_key"] = import_key
        if verification is not None:
            rec["verification"] = verification
        return self._append(rec)

    def add_count(self, *, metric: str, value: int, window: str,
                  source_ref: str, approximated: bool = True, campaign: str = "",
                  import_key: str | None = None, ts: str | None = None) -> dict:
        if not str(metric or "").strip() or not str(window or "").strip():
            raise AttributionError("metric/window 不能为空")
        if not str(source_ref or "").strip():
            raise AttributionError("计数必须带 source_ref（分母口径可核查）")
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise AttributionError("value 必须是非负整数")
        rec = {
            "kind": "count",
            "ts": ts or datetime.now().isoformat(timespec="seconds"),
            "metric": metric,
            "value": value,
            "window": window,
            "source_ref": source_ref,
            "approximated": bool(approximated),
        }
        if campaign:
            rec["campaign"] = campaign
        if import_key:
            rec["import_key"] = import_key
        return self._append(rec)

    def _append(self, rec: dict) -> dict:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return rec

    # ---- 读取 ----
    def records(self, campaign=None):
        if not self.path.is_file():
            return []
        out = []
        with self.path.open(encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                rec = json.loads(line)
                if campaign is None or rec.get("campaign") == campaign:
                    out.append(rec)
        return out

    def funnel(self, campaign=None) -> dict:
        """漏斗统计：主体先经 contacts 归一再去重；时序倒挂与缺核验价值事件单列披露。

        各阶段计数是独立观察值：相邻比值是「比值口径」不是转化因果，更不是
        渠道效果证明。计数快照单列，不与事件混算。"""
        revoked = self._revoked_import_keys()
        events = [r for r in self.records(campaign)
                  if r.get("kind") == "event"
                  and str(r.get("import_key") or "") not in revoked]
        counts = [r for r in self.records(campaign) if r.get("kind") == "count"]
        by_stage = {stage: set() for stage in FUNNEL}      # canonical 主体
        raw_by_stage = {stage: set() for stage in FUNNEL}  # 原始主体串
        approx_subjects = set()
        first_ts = {}  # (canonical, stage) -> 首次 ts（时序披露用）
        unverified = []
        for rec in events:
            stage = rec["event_type"]
            subject = rec.get("subject", "")
            canon = self._canonical(subject)
            by_stage[stage].add(canon)
            raw_by_stage[stage].add(subject)
            if rec.get("approximated"):
                approx_subjects.add(canon)
            ts = str(rec.get("ts") or "")
            if ts and (canon, stage) not in first_ts:
                first_ts[(canon, stage)] = ts
            if stage in VALUE_EVENTS and not rec.get("verification"):
                unverified.append({
                    "subject": subject, "event_type": stage, "ts": ts,
                    "source_ref": rec.get("source_ref", ""),
                    "reason": "缺 verification（存量/手写记录）：价值事件未核验，结论降级",
                })
        # 时序倒挂：任一后阶段首次发生早于任一前阶段（同口供主体，中间阶段允许缺失）
        anomalies = []
        for canon in sorted(set().union(*by_stage.values())):
            hit = None
            for i in range(1, len(FUNNEL)):
                cur_ts = first_ts.get((canon, FUNNEL[i]))
                if not cur_ts:
                    continue
                for j in range(i):
                    prev_ts = first_ts.get((canon, FUNNEL[j]))
                    if prev_ts and cur_ts < prev_ts:
                        hit = f"{FUNNEL[i]}@{cur_ts} 早于 {FUNNEL[j]}@{prev_ts}"
                        break
                if hit:
                    break
            if hit:
                anomalies.append({"subject": canon, "detail": hit})
        stages = []
        prev = None
        for stage in FUNNEL:
            n = len(by_stage[stage])
            conv = None
            if prev is not None and prev["subjects"] > 0:
                conv = round(n / prev["subjects"], 4)
            stages.append({"stage": stage, "subjects": n,
                           "raw_subjects": len(raw_by_stage[stage]),
                           "conversion_from_prev": conv})
            prev = stages[-1]
        raw_per_canon = {}
        for subjects in raw_by_stage.values():
            for s in subjects:
                raw_per_canon.setdefault(self._canonical(s), set()).add(s)
        identity_merged = sorted(cid for cid, raws in raw_per_canon.items() if len(raws) > 1)
        out = {
            "funnel": stages,
            "identity": {
                "source": "contacts.jsonl",
                "merged_canonical_subjects": identity_merged,
                "note": "subjects 为身份归一后计数；raw_subjects 为原始主体串计数",
            } if self._identity_map() else {
                "source": None,
                "note": "无 contacts.jsonl——主体按原始串去重，跨平台同一人会重复计数",
            },
            "sequence_anomalies": anomalies,
            "unverified_value_events": unverified,
            "approximated_signup_subjects": sorted(
                s for s in by_stage["signup"] if s in approx_subjects
            ),
            "counts": [
                {k: rec.get(k) for k in ("ts", "metric", "value", "window", "source_ref", "approximated")}
                for rec in counts
            ],
            "note": "转化率是比值不是因果；阶段计数为独立观察值；近似口径与缺核验价值事件单列，不洗白",
        }
        if revoked:
            out["disqualified"] = [
                {"event_id": key.split(":", 1)[-1],
                 "by": row.get("by"), "note": row.get("note"),
                 "ts": row.get("ts"), "event_type": row.get("event_type"),
                 "subject_id": row.get("subject_id")}
                for key, row in sorted(
                    (("%s:%s" % ("product_events", k)), v)
                    for k, v in self._latest_qualifications().items()
                    if not v.get("qualifies"))]
        if campaign:
            out["campaign"] = campaign
        return out

    def attribution(self, campaign=None) -> dict:
        """last_touch / first_touch 两种记账口径；明确声明非因果。主体同样先归一。"""
        events = [r for r in self.records(campaign) if r.get("kind") == "event"]
        converted = {}
        for rec in events:
            if rec["event_type"] not in ("signup", "activation", "paid"):
                continue
            converted.setdefault(self._canonical(rec.get("subject", "")), []).append(rec)
        rows = []
        for subject, recs in sorted(converted.items()):
            touches = [r for r in events
                       if self._canonical(r.get("subject", "")) == subject
                       and r["event_type"] == "signal"]
            rows.append({
                "subject": subject,
                "reached": recs[-1]["event_type"],
                "first_touch": (touches[0].get("channel") if touches else "") or "unknown",
                "last_touch": (touches[-1].get("channel") if touches else "") or "unknown",
            })
        return {
            "subjects": rows,
            "models": ["last_touch", "first_touch"],
            "note": "口径≠因果：两种模型只是记账角度，渠道效果比较需要实验（core/experiments）",
        }
