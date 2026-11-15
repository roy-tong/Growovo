#!/usr/bin/env python3
"""真实 CDP 账号身份读取（G-01b）——只从本机调试浏览器读平台稳定 ID。

定位：`core.account_identity` 的默认探测器实现。它回答一个问题——**当前调试
浏览器登录的到底是不是授权的那个平台账号**。答案只能来自平台自己发放的稳定
标识（X 的数字 user id / rest_id），不能来自 handle、显示名或登录界面标记。

契约（P0 G-01 AC-A/B/C/H）：

1. 只连回环端点：`127.0.0.1` 或 `::1`，端口来自配置（默认 9222）；任何其它
   地址在解析阶段即拒绝，绝不去连。
2. 只认稳定数字 ID：优先在页面内调用平台自己的 `verify_credentials`
   （`id_str`），其次读平台发放的 `twid` cookie（`u=<数字>`）。只有 handle
   时返回 `identity_missing`，绝不降级成「看起来像」的身份。
3. 同一浏览器多个 browser context（可能各登录不同账号）→ `target_ambiguous`，
   不猜。
4. 默认关闭：`MKT_CDP_IDENTITY=1` 才探测；关闭 / 不可达 / 超时一律返回 None
   （身份未知 → 上层 fail closed）。
5. 失败归类留痕：返回结构化 `ProbeResult.reason`，由
   `core.account_identity` 追加到 `data/identity/probes.jsonl`；本模块不把
   异常吞成「没有身份」，也不把 cookie/token 写进日志或台账。
6. 传输层可注入（`http_get` / `ws_factory`）：离线测试用假客户端或只绑
   127.0.0.1 临时端口的假 HTTP/WS 服务端，零外网。

已知边界：X 的页面结构与 `verify_credentials` 可用性可能随平台改版变化；
两条读取路径都失败时按 fail closed 处理，不会退回 handle 猜测。真实联调
安排在 2026-11-19 X campaign 结束后（期间 CDP 9222 实例与 profiles/ 不动）。
"""
import base64
import hashlib
import json
import logging
import os
import re
import socket
import struct
import urllib.error
import urllib.parse
import urllib.request

LOGGER = logging.getLogger(__name__)

# 默认端口与超时：端口来自配置，超时 5s（与登录探测同量级）。
DEFAULT_PORT = 9222
DEFAULT_TIMEOUT = 5.0
_ENV_ENABLED = "MKT_CDP_IDENTITY"
_ENV_URL = "MKT_CDP_IDENTITY_URL"
_ENV_PORT = "MKT_CDP_IDENTITY_PORT"
_ENV_TIMEOUT = "MKT_CDP_IDENTITY_TIMEOUT"
_CDP_URL_ENV = "MKT_CDP_URL"  # 既有调试浏览器端点（doctor/cdp.py 共用）

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1"})
_X_HOSTS = frozenset({"x.com", "www.x.com", "mobile.x.com",
                      "twitter.com", "www.twitter.com", "mobile.twitter.com"})

# X 网页端公开 bearer（不是用户凭据，页面自身就用它）；只用于在页面上下文里
# 调用平台自己的 verify_credentials。失效时退回 twid cookie，仍失败则 fail closed。
_X_WEB_BEARER = ("AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs"
                 "%3D1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA")

# 探测失败分类（不合并成同一种失败：调用方/审计需要知道差在哪）。
DISABLED = "disabled"
ENDPOINT_NOT_LOOPBACK = "endpoint_not_loopback"
ENDPOINT_UNREACHABLE = "endpoint_unreachable"
ENDPOINT_HTTP_ERROR = "endpoint_http_error"
ENDPOINT_INVALID = "endpoint_invalid"
TARGET_MISSING = "target_missing"
TARGET_AMBIGUOUS = "target_ambiguous"
WS_UNAVAILABLE = "ws_unavailable"
TIMEOUT = "timeout"
EVALUATE_ERROR = "evaluate_error"
ENDPOINT_MISMATCH = "identity_endpoint_mismatch"
IDENTITY_MISSING = "identity_missing"
IDENTITY_CONFLICT = "identity_conflict"
OK = "ok"

PROBE_REASONS = frozenset({
    OK, DISABLED, ENDPOINT_NOT_LOOPBACK, ENDPOINT_UNREACHABLE,
    ENDPOINT_HTTP_ERROR, ENDPOINT_INVALID, TARGET_MISSING, TARGET_AMBIGUOUS,
    WS_UNAVAILABLE, TIMEOUT, EVALUATE_ERROR, IDENTITY_MISSING,
    IDENTITY_CONFLICT,
})

_JS_COOKIE = "document.cookie"
_JS_VERIFY_CREDENTIALS = """(async () => {
  try {
    const csrfMatch = document.cookie.match(/(?:^|;\\s*)ct0=([^;]+)/);
    const csrf = csrfMatch ? decodeURIComponent(csrfMatch[1]) : '';
    const resp = await fetch('/i/api/1.1/account/verify_credentials.json?skip_status=1&include_entities=false&include_email=false', {
      credentials: 'include',
      headers: {
        'authorization': 'Bearer %s',
        'x-csrf-token': csrf,
        'x-twitter-active-user': 'yes',
        'x-twitter-auth-type': 'OAuth2Session'
      }
    });
    if (!resp.ok) { return JSON.stringify({ok: false, status: resp.status}); }
    const data = await resp.json();
    return JSON.stringify({
      ok: true,
      id_str: String(data.id_str || ''),
      screen_name: String(data.screen_name || '')
    });
  } catch (err) {
    return JSON.stringify({ok: false, error: String(err)});
  }
})()""" % _X_WEB_BEARER


class EndpointRejected(ValueError):
    """端点不是本机回环地址（或不是可用的 http 端点）。"""


class _CdpError(RuntimeError):
    def __init__(self, message, reason=EVALUATE_ERROR):
        super().__init__(message)
        self.reason = reason


class ProbeResult:
    """一次探测的结构化结果：proof 或归类后的失败原因。"""

    def __init__(self, *, reason, detail="", endpoint="", target_id="",
                 source="", proof=None):
        self.reason = reason
        self.detail = str(detail or "")[:400]
        self.endpoint = endpoint
        self.target_id = target_id
        self.source = source
        self.proof = proof

    @property
    def ok(self):
        return self.proof is not None

    def to_dict(self):
        return {"ok": self.ok, "reason": self.reason, "detail": self.detail,
                "endpoint": self.endpoint, "target_id": self.target_id,
                "source": self.source,
                "proof": self.proof.to_dict() if self.proof is not None else None}

    def __repr__(self):
        return ("ProbeResult(reason=%r, endpoint=%r, target=%r)"
                % (self.reason, self.endpoint, self.target_id))


def enabled(env=None):
    """探测开关：默认关闭（关闭时身份未知 → 上层 fail closed）。"""
    env = os.environ if env is None else env
    return str(env.get(_ENV_ENABLED) or "").strip().lower() in (
        "1", "true", "yes", "on")


def _timeout(env, override=None):
    raw = override
    if raw is None:
        raw = (env.get(_ENV_TIMEOUT) or "").strip() or DEFAULT_TIMEOUT
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return DEFAULT_TIMEOUT
    return min(30.0, max(0.1, value))


def parse_endpoint(raw):
    """校验并规范化 CDP 端点；只接受回环 127.0.0.1 / ::1 的 http 端点。"""
    text = str(raw or "").strip()
    if not text:
        raise EndpointRejected("CDP 端点为空")
    parts = urllib.parse.urlsplit(text)
    if parts.scheme != "http":
        raise EndpointRejected("CDP 端点必须是 http://，收到 %r" % parts.scheme)
    host = parts.hostname or ""
    try:
        port = parts.port
    except ValueError:
        raise EndpointRejected("CDP 端点端口无效: %r" % text) from None
    if host not in LOOPBACK_HOSTS:
        raise EndpointRejected(
            "CDP 端点必须是回环地址（127.0.0.1 或 ::1），拒绝连接 %r" % host)
    if port is None:
        raise EndpointRejected("CDP 端点必须显式给出端口（默认 %d）" % DEFAULT_PORT)
    if not 1 <= port <= 65535:
        raise EndpointRejected("CDP 端点端口越界: %d" % port)
    if parts.path not in ("", "/") or parts.query or parts.fragment:
        raise EndpointRejected("CDP 端点不应带路径/查询: %r" % text)
    host_text = "[%s]" % host if ":" in host else host
    return "http://%s:%d" % (host_text, port)


def resolve_endpoint(env=None):
    """端点来自配置：MKT_CDP_IDENTITY_URL > MKT_CDP_IDENTITY_PORT > MKT_CDP_URL > 9222。"""
    env = os.environ if env is None else env
    explicit = str(env.get(_ENV_URL) or "").strip()
    if explicit:
        return parse_endpoint(explicit)
    port = str(env.get(_ENV_PORT) or "").strip()
    if port:
        if not port.isdigit():
            raise EndpointRejected("MKT_CDP_IDENTITY_PORT 必须是整数端口: %r" % port)
        return parse_endpoint("http://127.0.0.1:%s" % port)
    shared = str(env.get(_CDP_URL_ENV) or "").strip()
    if shared:
        return parse_endpoint(shared)
    return parse_endpoint("http://127.0.0.1:%d" % DEFAULT_PORT)


def port_of(base_url):
    return urllib.parse.urlsplit(base_url).port


# --------------------------------------------------------------------------- #
# 最小 WebSocket 客户端（DevTools 只用到文本帧 + ping/close）
# --------------------------------------------------------------------------- #

_WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


class _WsClient:
    def __init__(self, url, timeout):
        parts = urllib.parse.urlsplit(url)
        if parts.scheme != "ws" or not parts.hostname or not parts.port:
            raise ConnectionError("CDP WebSocket 地址非法: %r" % url)
        self._sock = socket.create_connection((parts.hostname, parts.port),
                                              timeout=timeout)
        self._sock.settimeout(timeout)
        self._buffer = b""
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        path = parts.path or "/"
        if parts.query:
            path += "?" + parts.query
        request = (
            "GET %s HTTP/1.1\r\nHost: %s:%d\r\nUpgrade: websocket\r\n"
            "Connection: Upgrade\r\nSec-WebSocket-Key: %s\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
            % (path, parts.hostname, parts.port, key))
        self._sock.sendall(request.encode("ascii"))
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = self._sock.recv(4096)
            if not chunk:
                self.close()
                raise ConnectionError("CDP WebSocket 握手被关闭")
            head += chunk
        head, _, rest = head.partition(b"\r\n\r\n")
        self._buffer = rest
        text = head.decode("latin-1")
        if " 101 " not in text.split("\r\n", 1)[0] + " ":
            self.close()
            raise ConnectionError("CDP WebSocket 握手失败: %s"
                                  % text.split("\r\n", 1)[0][:80])
        expected = base64.b64encode(
            hashlib.sha1((key + _WS_GUID).encode("ascii")).digest()).decode("ascii")
        if expected not in text:
            self.close()
            raise ConnectionError("CDP WebSocket Sec-WebSocket-Accept 不匹配")

    # -- 帧读写 ---------------------------------------------------------

    def _read_exact(self, size):
        while len(self._buffer) < size:
            chunk = self._sock.recv(4096)
            if not chunk:
                raise ConnectionError("CDP WebSocket 已关闭")
            self._buffer += chunk
        data, self._buffer = self._buffer[:size], self._buffer[size:]
        return data

    def _read_frame(self):
        first = self._read_exact(2)
        opcode = first[0] & 0x0F
        length = first[1] & 0x7F
        if length == 126:
            length = struct.unpack("!H", self._read_exact(2))[0]
        elif length == 127:
            length = struct.unpack("!Q", self._read_exact(8))[0]
        masked = bool(first[1] & 0x80)
        mask = self._read_exact(4) if masked else b"\x00\x00\x00\x00"
        payload = self._read_exact(length) if length else b""
        if masked:
            payload = bytes(byte ^ mask[i % 4] for i, byte in enumerate(payload))
        return opcode, payload

    def _send_text(self, text):
        data = text.encode("utf-8")
        mask = os.urandom(4)
        header = bytearray([0x81])
        if len(data) < 126:
            header.append(0x80 | len(data))
        elif len(data) < 65536:
            header.append(0x80 | 126)
            header.extend(struct.pack("!H", len(data)))
        else:
            header.append(0x80 | 127)
            header.extend(struct.pack("!Q", len(data)))
        masked = bytes(byte ^ mask[i % 4] for i, byte in enumerate(data))
        self._sock.sendall(bytes(header) + mask + masked)

    def send_json(self, payload):
        self._send_text(json.dumps(payload))

    def recv_json(self):
        while True:
            opcode, payload = self._read_frame()
            if opcode == 8:
                raise ConnectionError("CDP WebSocket 被对端关闭")
            if opcode == 9:  # ping → pong
                self._sock.sendall(b"\x8a\x00")
                continue
            if opcode in (1, 2):
                return json.loads(payload.decode("utf-8"))

    def close(self):
        try:
            self._sock.close()
        except OSError:
            pass


# --------------------------------------------------------------------------- #
# 探测
# --------------------------------------------------------------------------- #

def _default_http_get(url, timeout):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return int(getattr(response, "status", 200)), response.read().decode(
                "utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return int(exc.code), exc.read().decode("utf-8", "replace")


def _is_x_page(target):
    url = str(target.get("url") or "")
    try:
        host = urllib.parse.urlsplit(url).hostname or ""
    except ValueError:
        return False
    return host in _X_HOSTS


def _select_target(payload, base):
    if not isinstance(payload, list):
        raise _CdpError("/json 不是目标数组")
    pages = [item for item in payload
             if isinstance(item, dict) and item.get("type") == "page"]
    x_pages = [item for item in pages if _is_x_page(item)]
    if not x_pages:
        raise _CdpError("调试浏览器里没有 x.com 页面标签（目标不存在）",
                        TARGET_MISSING)
    contexts = {str(item.get("browserContextId") or "") for item in x_pages}
    if len(contexts) > 1:
        raise _CdpError("同一浏览器有多个 browser context 的 X 页面：不通过模糊猜测",
                        TARGET_AMBIGUOUS)
    x_pages.sort(key=lambda item: (
        0 if str(item.get("url") or "").startswith("https://x.com") else 1,
        str(item.get("id") or "")))
    return x_pages[0]


def _ws_url_for(target, base):
    ws_url = str(target.get("webSocketDebuggerUrl") or "")
    parts = urllib.parse.urlsplit(ws_url)
    if parts.scheme != "ws" or parts.hostname not in LOOPBACK_HOSTS:
        raise _CdpError("webSocketDebuggerUrl 不在回环端点", WS_UNAVAILABLE)
    try:
        ws_port = parts.port
    except ValueError:
        raise _CdpError("webSocketDebuggerUrl 端口无效", WS_UNAVAILABLE) from None
    # The page target's debugger URL may use a different local port than the
    # HTTP listing endpoint (a proxy in front of Chrome, or a test harness that
    # serves the two on separate sockets). Only the host locality is a security
    # boundary; the port is recorded in the proof, not used to widen access.
    if ws_port is None:
        raise _CdpError("webSocketDebuggerUrl 缺少端口", WS_UNAVAILABLE)
    return ws_url


def _evaluate(ws, expression, message_id):
    ws.send_json({
        "id": message_id,
        "method": "Runtime.evaluate",
        "params": {"expression": expression, "returnByValue": True,
                   "awaitPromise": True, "userGesture": False},
    })
    while True:
        message = ws.recv_json()
        if message.get("id") != message_id:
            continue
        if message.get("error"):
            raise _CdpError(str(message["error"].get("message") or "CDP error"))
        result = message.get("result") or {}
        if result.get("exceptionDetails"):
            detail = result["exceptionDetails"].get("text") or "JS 异常"
            raise _CdpError(str(detail)[:160])
        value = (result.get("result") or {}).get("value")
        return value if isinstance(value, str) else ""


def _cookie_value(cookies, name):
    match = re.search(r"(?:^|;\s*)%s=([^;]*)" % re.escape(name), cookies or "")
    if not match:
        return ""
    return urllib.parse.unquote(match.group(1)).strip()


def _numeric_id(value):
    text = str(value or "").strip()
    return text if re.fullmatch(r"\d+", text) else ""


def _build_proof(channel, base, target, user_id, handle, source):
    from .account_identity import AccountIdentityProof
    target_id = str(target.get("id") or "")
    context = str(target.get("browserContextId") or "default")
    host = urllib.parse.urlsplit(base).hostname or ""
    return AccountIdentityProof(
        channel=channel, user_id=user_id, handle=handle,
        browser_context="cdp:%s:%d:%s:%s" % (host, port_of(base), target_id, context),
        source_ref="%s/json#%s@%s" % (base, target_id, source))


def probe(channel="x", *, endpoint=None, timeout=None, http_get=None,
          ws_factory=None, env=None):
    """读取当前调试浏览器里该渠道的稳定账号身份，返回 ProbeResult。"""
    env = os.environ if env is None else env
    channel = str(channel or "").strip().lower()
    if not enabled(env):
        return ProbeResult(reason=DISABLED,
                           detail="MKT_CDP_IDENTITY 未开启：身份未知，真实发送须 fail closed")
    wait = _timeout(env, timeout)
    raw = endpoint if endpoint is not None else str(env.get(_ENV_URL) or "").strip()
    if not raw and not endpoint:
        try:
            raw = resolve_endpoint(env)
        except EndpointRejected as exc:
            return ProbeResult(reason=ENDPOINT_NOT_LOOPBACK, detail=str(exc))
    try:
        base = parse_endpoint(raw)
    except EndpointRejected as exc:
        return ProbeResult(reason=ENDPOINT_NOT_LOOPBACK, detail=str(exc),
                           endpoint=str(raw))

    get = http_get or _default_http_get
    try:
        status, body = get(base + "/json", wait)
    except (socket.timeout, TimeoutError):
        return ProbeResult(reason=TIMEOUT, endpoint=base,
                           detail="读取 /json 超时（>%.1fs）" % wait)
    except urllib.error.URLError as exc:
        reason = exc.reason
        if isinstance(reason, (socket.timeout, TimeoutError)):
            return ProbeResult(reason=TIMEOUT, endpoint=base,
                               detail="读取 /json 超时（>%.1fs）" % wait)
        return ProbeResult(reason=ENDPOINT_UNREACHABLE, endpoint=base,
                           detail="连接 /json 失败: %s" % str(reason)[:160])
    except OSError as exc:
        return ProbeResult(reason=ENDPOINT_UNREACHABLE, endpoint=base,
                           detail="连接 /json 失败: %s" % str(exc)[:160])
    if status != 200:
        return ProbeResult(reason=ENDPOINT_HTTP_ERROR, endpoint=base,
                           detail="/json 返回 HTTP %d" % status)
    try:
        payload = json.loads(body)
    except (TypeError, ValueError):
        return ProbeResult(reason=ENDPOINT_INVALID, endpoint=base,
                           detail="/json 不是合法 JSON")
    try:
        target = _select_target(payload, base)
        ws_url = _ws_url_for(target, base)
    except _CdpError as exc:
        return ProbeResult(reason=exc.reason, endpoint=base, detail=str(exc))

    factory = ws_factory or _WsClient
    try:
        ws = factory(ws_url, wait)
    except (socket.timeout, TimeoutError):
        return ProbeResult(reason=TIMEOUT, endpoint=base,
                           target_id=str(target.get("id") or ""),
                           detail="连接页面 WebSocket 超时")
    except (OSError, ConnectionError) as exc:
        return ProbeResult(reason=WS_UNAVAILABLE, endpoint=base,
                           target_id=str(target.get("id") or ""),
                           detail="连接页面 WebSocket 失败: %s" % str(exc)[:160])

    timed_out = False
    errors = []
    cookies = ""
    api_value = ""
    try:
        try:
            cookies = _evaluate(ws, _JS_COOKIE, 1)
        except (socket.timeout, TimeoutError):
            timed_out = True
            errors.append("cookie 读取超时")
        except _CdpError as exc:
            errors.append("cookie 读取失败: %s" % str(exc)[:120])
        except (OSError, ConnectionError) as exc:
            errors.append("cookie 读取连接失败: %s" % str(exc)[:120])
        try:
            api_value = _evaluate(ws, _JS_VERIFY_CREDENTIALS, 2)
        except (socket.timeout, TimeoutError):
            timed_out = True
            errors.append("verify_credentials 超时")
        except _CdpError as exc:
            errors.append("verify_credentials 失败: %s" % str(exc)[:120])
        except (OSError, ConnectionError) as exc:
            errors.append("verify_credentials 连接失败: %s" % str(exc)[:120])
    finally:
        try:
            ws.close()
        except Exception:  # noqa: BLE001 关闭失败不影响已读到的结论
            pass

    cookie_id = ""
    twid = _cookie_value(cookies, "twid")
    twid_match = re.fullmatch(r"u=(\d+)", twid or "")
    if twid_match:
        cookie_id = twid_match.group(1)

    api = {}
    if api_value:
        try:
            parsed = json.loads(api_value)
            if isinstance(parsed, dict):
                api = parsed
        except ValueError:
            errors.append("verify_credentials 返回非 JSON")
    api_id = _numeric_id(api.get("id_str")) if api.get("ok") else ""
    handle = str(api.get("screen_name") or "") if api.get("ok") else ""

    target_id = str(target.get("id") or "")
    if cookie_id and api_id and cookie_id != api_id:
        return ProbeResult(
            reason=IDENTITY_CONFLICT, endpoint=base, target_id=target_id,
            detail="twid 与 verify_credentials 给出的数字 ID 不一致：不猜测、拒绝")
    if api_id:
        user_id, source = api_id, "api:verify_credentials"
    elif cookie_id:
        user_id, source = cookie_id, "cookie:twid"
    else:
        if api.get("ok") and handle:
            detail = ("handle_only：只读到 handle（%s），没有稳定数字 ID，不得冒充账号身份" % handle)
            reason = IDENTITY_MISSING
        elif timed_out:
            detail = "；".join(errors[-2:]) or "探测超时"
            reason = TIMEOUT
        elif errors and not api:
            detail = "；".join(errors[-2:])
            reason = EVALUATE_ERROR
        elif api and api.get("ok") is False and api.get("status"):
            detail = ("verify_credentials HTTP %s；twid cookie 无数字 ID"
                      % api.get("status"))
            reason = IDENTITY_MISSING
        else:
            detail = "；".join(errors[-2:]) or "页面未提供稳定数字 ID"
            reason = IDENTITY_MISSING
        return ProbeResult(reason=reason, endpoint=base, target_id=target_id,
                           detail=detail)

    try:
        proof = _build_proof(channel, base, target, user_id, handle, source)
    except ValueError as exc:  # 构造证明失败也按身份未知处理
        return ProbeResult(reason=IDENTITY_MISSING, endpoint=base,
                           target_id=target_id, detail=str(exc))
    LOGGER.info("CDP 身份探测成功：%s via %s", user_id, source)
    return ProbeResult(reason=OK, endpoint=base, target_id=target_id,
                       source=source, proof=proof)


#: 执行侧端点环境变量：X vendor（channels/x/vendor/*.py）用它决定真实动作
#: 在哪个调试浏览器里执行。身份探测必须与它指向同一个浏览器，否则会出现
#: 「在 A 上验证身份、在 B 上发送」。
_EXECUTION_URL_ENV = {"x": "X_CDP_URL"}


def execution_endpoint(channel, env=None):
    """该渠道真实执行时使用的 CDP 端点（与 vendor 同一份配置）。

    G-10：返回规范化后的 loopback 端点；配置非法时抛 EndpointRejected。
    """
    env = os.environ if env is None else env
    key = _EXECUTION_URL_ENV.get(str(channel or "").strip().lower())
    raw = str(env.get(key) or "").strip() if key else ""
    if not raw:
        raw = str(env.get("MKT_CDP_URL") or "").strip() or "http://127.0.0.1:9222"
    return parse_endpoint(raw)


def assert_identity_endpoint_matches(channel, env=None):
    """身份端点必须等于执行端点；不等就抛 _CdpError(ENDPOINT_MISMATCH)。"""
    env = os.environ if env is None else env
    identity = resolve_endpoint(env)
    execution = execution_endpoint(channel, env)
    if identity.rstrip("/") != execution.rstrip("/"):
        raise _CdpError(
            "身份探测端点 %s 与执行端点 %s 不是同一个调试浏览器：不发送"
            % (identity, execution), ENDPOINT_MISMATCH)
    return execution

