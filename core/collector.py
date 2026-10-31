#!/usr/bin/env python3
"""本地 collector —— 让"访问/注册"自己走回 campaign（记账自动化的入口服务）。

三种接入，全部落到同一份 outcome 台账：

1. 追踪链接跳转  GET /t/<link_id>?cid=xxx
   记录一条 signal（channel/campaign/content 从 links.jsonl 取），再 302 到带
   utm 的目标页；同主体 30 分钟内重复点击只记一次（防刷新灌水）。
2. 事件 webhook   POST /events  {"subject","event_type","campaign","channel",...}
   表单/产品后端/PostHog Webhook 都能直接 POST；Bearer token 可选但建议开。
3. CSV 导入       plausible 的聚合 CSV → count 快照；posthog 的事件 CSV → 走
   outcome import（列名映射 + 事件名映射）。

纪律：HTTP 默认只接受 signal/signup；activation/paid 必须带 source_ref 与
verification(method,ref)——价值事件不能靠一个匿名 POST 就成立。服务默认只绑
127.0.0.1，不暴露公网；跨机使用请自建隧道并自行评估风险。
"""
import csv
import hashlib
import io
import json
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .attribution import FUNNEL, OutcomeLedger
from .links import list_links

MAX_BODY = 64 * 1024
DEDUPE_SECONDS = 30 * 60


class CollectorError(RuntimeError):
    pass


def _key(*parts):
    return hashlib.sha1("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()[:16]


def _anon(remote="", ua=""):
    return "anon-" + hashlib.sha1(f"{remote}|{ua}".encode("utf-8")).hexdigest()[:10]


def ingest(data_dir, payload, *, token=None, expected_token="", remote="", ua=""):
    """校验并写入一条事件/计数。返回账本记录。"""
    if expected_token and token != expected_token:
        raise CollectorError("token 无效")
    if not isinstance(payload, dict):
        raise CollectorError("body 必须是 JSON 对象")
    kind = (payload.get("kind") or "event").strip().lower()
    ledger = OutcomeLedger(data_dir)

    if kind == "count":
        metric = str(payload.get("metric") or "").strip()
        value = payload.get("value")
        window = str(payload.get("window") or "").strip()
        source_ref = str(payload.get("source_ref") or "").strip()
        if not (metric and window and source_ref):
            raise CollectorError("count 需要 metric/window/source_ref")
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise CollectorError("count.value 必须是非负整数")
        return ledger.add_count(metric=metric, value=value, window=window,
                                source_ref=source_ref,
                                approximated=bool(payload.get("approximated", False)),
                                campaign=str(payload.get("campaign") or ""),
                                import_key=payload.get("import_key") or None)

    subject = str(payload.get("subject") or "").strip() or _anon(remote, ua)
    etype = str(payload.get("event_type") or payload.get("type") or "").strip().lower()
    if etype not in FUNNEL:
        raise CollectorError(f"event_type 只允许 {FUNNEL}")
    source_ref = str(payload.get("source_ref") or "").strip()
    verification = payload.get("verification")
    if not verification and payload.get("verify_method") and payload.get("verify_ref"):
        verification = {"method": payload["verify_method"], "ref": payload["verify_ref"]}
    if etype in ("activation", "paid"):
        if not source_ref:
            raise CollectorError(f"{etype} 必须带 source_ref")
        if not verification:
            raise CollectorError(f"{etype} 必须带 verification(method,ref)")
    ts = str(payload.get("ts") or "").strip() or None
    return ledger.add_event(
        subject=subject, event_type=etype,
        channel=str(payload.get("channel") or ""),
        touch=str(payload.get("touch") or ""),
        source_ref=source_ref,
        approximated=bool(payload.get("approximated", False)),
        verification=verification if isinstance(verification, dict) else None,
        campaign=str(payload.get("campaign") or ""),
        import_key=payload.get("import_key") or None,
        ts=ts)


def record_click(data_dir, link_id, *, cid="", remote="", ua=""):
    """记录一次追踪链接点击（signal），返回 (link, record|None)。"""
    link = next((l for l in list_links(data_dir) if l.get("id") == link_id), None)
    if link is None:
        raise CollectorError(f"未知 link id: {link_id}")
    subject = (cid or "").strip() or _anon(remote, ua)
    ledger = OutcomeLedger(data_dir)
    now = time.time()
    for rec in ledger.records(link.get("campaign")):
        if rec.get("kind") != "event" or rec.get("event_type") != "signal":
            continue
        if rec.get("subject") != subject or rec.get("source_ref") != f"link:{link_id}":
            continue
        try:
            age = now - datetime.fromisoformat(rec.get("ts", "")).timestamp()
        except (ValueError, TypeError):
            age = DEDUPE_SECONDS + 1
        if age < DEDUPE_SECONDS:
            return link, None  # 刷新/重复点击不灌水
    rec = ledger.add_event(subject=subject, event_type="signal",
                           channel=link.get("channel", ""),
                           touch=link.get("content", ""),
                           source_ref=f"link:{link_id}",
                           campaign=link.get("campaign", ""))
    return link, rec


def import_plausible(data_dir, path, *, campaign="", dry_run=False):
    """Plausible 聚合导出（visitors 计数）→ count 快照，不伪造漏斗事件。"""
    text = Path(path).read_text(encoding="utf-8-sig")
    rows = list(csv.DictReader(io.StringIO(text)))
    ledger = OutcomeLedger(data_dir)
    existing = {(r.get("campaign"), r.get("metric"), r.get("window"))
                for r in ledger.records() if r.get("kind") == "count"}
    out = {"total": 0, "created": 0, "skipped": 0, "errors": [],
           "dry_run": bool(dry_run), "source": "plausible"}
    for i, row in enumerate(rows, 2):
        out["total"] += 1
        low = {str(k).strip().lower().replace("-", "_"): (v or "").strip()
               for k, v in row.items() if k}
        raw = (low.get("visitors") or low.get("value") or low.get("count") or "")
        try:
            value = int(float(raw))
        except ValueError:
            out["errors"].append({"row": i, "error": f"visitors 不是数字: {raw!r}"})
            continue
        camp = low.get("utm_campaign") or low.get("campaign") or campaign
        window = low.get("date") or low.get("window") or "all"
        if (camp, "visits", window) in existing:
            out["skipped"] += 1
            continue
        if not dry_run:
            ledger.add_count(metric="visits", value=value, window=window,
                             source_ref=f"plausible:{Path(path).name}",
                             approximated=False, campaign=camp,
                             import_key=_key("plausible", camp, window, value))
            existing.add((camp, "visits", window))
        out["created"] += 1
    return out


def make_server(data_dir, host="127.0.0.1", port=8787, token=""):
    """构造 HTTP server（不启动）。测试用 port=0 取随机端口。"""
    data = Path(data_dir)

    class Handler(BaseHTTPRequestHandler):
        server_version = "GrowovoCollector/0.1"

        def log_message(self, fmt, *args):  # 安静：不把每条请求打进 stderr
            return

        def _send_json(self, code, obj):
            body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _authorized(self, query):
            expected = getattr(self.server, "token", "")
            if not expected:
                return True
            auth = self.headers.get("Authorization", "")
            tok = auth[7:] if auth.lower().startswith("bearer ") else ""
            return (tok or query.get("token", [""])[0]) == expected

        def do_GET(self):
            parsed = urlparse(self.path)
            q = parse_qs(parsed.query)
            if parsed.path == "/health":
                return self._send_json(200, {"ok": True, "service": "growovo-collector"})
            if parsed.path.startswith("/t/"):
                link_id = parsed.path[3:].strip("/")
                try:
                    link, rec = record_click(
                        data, link_id, cid=q.get("cid", [""])[0],
                        remote=self.client_address[0],
                        ua=self.headers.get("User-Agent", ""))
                except CollectorError as exc:
                    return self._send_json(404, {"ok": False, "error": str(exc)})
                self.send_response(302)
                self.send_header("Location", link.get("tagged_url") or link.get("url"))
                self.send_header("Content-Length", "0")
                self.end_headers()
                return None
            return self._send_json(404, {"ok": False, "error": "not found"})

        def do_POST(self):
            parsed = urlparse(self.path)
            q = parse_qs(parsed.query)
            if parsed.path != "/events":
                return self._send_json(404, {"ok": False, "error": "not found"})
            if not self._authorized(q):
                return self._send_json(401, {"ok": False, "error": "unauthorized"})
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0 or length > MAX_BODY:
                return self._send_json(413, {"ok": False, "error": "body 大小不合法"})
            raw = self.rfile.read(length)
            try:
                payload = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                return self._send_json(400, {"ok": False, "error": "body 不是合法 JSON"})
            auth = self.headers.get("Authorization", "")
            tok = auth[7:] if auth.lower().startswith("bearer ") else q.get("token", [""])[0]
            try:
                rec = ingest(data, payload, token=tok,
                             expected_token=getattr(self.server, "token", ""),
                             remote=self.client_address[0],
                             ua=self.headers.get("User-Agent", ""))
            except CollectorError as exc:
                return self._send_json(400, {"ok": False, "error": str(exc)})
            return self._send_json(200, {"ok": True, "record": rec})

    srv = ThreadingHTTPServer((host, port), Handler)
    srv.data_dir = data
    srv.token = token or ""
    return srv


def serve(data_dir, host="127.0.0.1", port=8787, token="", log=print):
    """阻塞式本地服务。Ctrl-C 退出。返回 0。"""
    srv = make_server(data_dir, host=host, port=port, token=token)
    actual = srv.server_address
    log(f"collector 监听 http://{actual[0]}:{actual[1]}  data={data_dir}")
    log("路由：POST /events · GET /t/<link_id>?cid=… · GET /health")
    log("鉴权：Bearer token 已启用" if token else "鉴权：未设置 token（仅建议本机使用）")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        log("collector 停止")
    finally:
        srv.server_close()
    return 0
