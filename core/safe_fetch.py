#!/usr/bin/env python3
"""受控抓取：DNS 校验与连接固定（G-09）。

``core/watch_safety.py`` 回答「这个地址能不能抓」；本模块回答「抓的时候到底
连到了谁」。G-06 的缺口在这里闭合：判定用的解析结果必须就是连接用的地址，
否则校验与连接之间还有一次独立的域名解析，可以被 DNS rebinding 换掉。

每一跳都走同一套流程（五步，顺序固定）：

1. **解析一次**：``watch_safety.check_url`` 用同一个 resolver 解析，拿到全部
   地址；连接阶段绝不重新解析域名。
2. **全量判定**：每个地址都过 ``watch_safety.classify_address``，任一私网/回环/
   链路本地/保留/组播 → 整次抓取拒绝（任一不过即拒，不是「挑一个能用的」）。
3. **选一个已判定的地址**：取第一个通过判定的地址作为 ``pinned_ip``；本次抓取
   只连它，不做多 IP 回退（回退会放大「已校验集合」和「实际连接」的分歧面）。
4. **连接该 IP**：HTTP 用 ``http.client.HTTPConnection(host=ip)`` 并显式发送
   原域名的 ``Host`` 头；HTTPS 用默认校验的 ``ssl.SSLContext`` +
   ``wrap_socket(server_hostname=原域名)``，SNI 与证书主机名校验都针对原域名，
   不会因为连 IP 就把校验关掉或改成校验 IP。连接建立后再用 ``getpeername``
   核对落地地址，与 ``pinned_ip`` 不符（含非公网）即拒绝读取响应。
5. **校验响应**：状态码、content-type、声明长度与实际字节都封顶；超界明确失败。

重定向：不用自动跟随的库默认行为；每个 3xx 的 Location 都作为下一跳重新走
1–5 步（解析 → 判定 → 固定连接），跳数、降级（https→http）、成环、非 http(s)
都在这里拒绝。

可观测：返回值和 ``record`` 都带 ``resolved_ips`` / ``pinned_ip`` /
``redirect_chain`` / ``bytes`` / ``allowed`` / ``reason`` / ``mode`` /
``connector``，供 ``core/watch.py`` 落盘复核。

已知边界（不为兼容性放宽，写清楚）：

- **单 IP，不回退**：解析出多个公网地址时只用第一个；首个地址不可达就整次
  失败，不会换第二个再试。CDN 任何播/多 IP 容灾需要另立工单。
- **不走系统代理**：连接是直接到 IP 的 socket，``http_proxy``/``https_proxy``
  不生效；需要代理的环境得先有「代理也算连接目标」的设计，本模块不提供。
- **不做 HTTP/2 / ALPN**：只发 HTTP/1.1；要求 HTTP/2 的站点会失败。
- **不复用连接**：每次抓取新建连接、抓完关闭，没有连接池，因此也没有
  「复用到别的域名」的窗口。
- **注入连接层要自己报告落地地址**：``connector`` 注入点把已判定的 IP 交给
  调用方，默认实现用 ``getpeername`` 强制核对；注入实现若不回传 ``peer_ip``，
  本模块无法复核它实际连了谁（测试/离线演示专用，不是生产默认）。
- **证书信任锚**：默认用系统信任库；``tls_context`` 只允许换信任锚，仍必须是
  ``CERT_REQUIRED`` + ``check_hostname``，否则拒绝连接。
- **IPv6**：已固定的 IPv6 地址用 ``Host: [addr]`` 形式发送；解析器给出 IPv6
  但本机没有 IPv6 出口时整次失败（不换 IPv4 重试）。
- **字面 IP URL**：没有域名可校验，证书按那个 IP 的 SAN 校验
  （``server_hostname`` 就是 IP）；站点证书不含该 IP 时握手失败，这是预期，
  不是为兼容性放宽。
"""
from __future__ import annotations

import http.client
import ipaddress
import socket
import ssl
import time
import urllib.parse
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from . import watch_safety as safety

#: 建连超时（秒）：TCP 连接与 TLS 握手共用。
CONNECT_TIMEOUT = 10.0
#: 读超时（秒）：单次 socket 读写。
READ_TIMEOUT = 15.0
#: 单次抓取（含全部重定向跳）的总时长上限（秒）。
MAX_SECONDS = 45.0
MAX_REDIRECTS = safety.MAX_REDIRECTS
MAX_BYTES = safety.MAX_BYTES

USER_AGENT = "Mozilla/5.0 (compatible; GrowovoWatch/1.1)"
ACCEPT = "text/html,application/xhtml+xml,text/plain"
DEFAULT_PORTS = {"http": 80, "https": 443}

#: 机器码 → C-01 错误码家族（``core.grow_runbook.C01_ERROR_CODES``）。
#: 具体码保留可断言性；家族码只回答「这属于哪类失败」。
C01_BY_CODE: Dict[str, str] = {
    "BAD_URL": "INPUT_REQUIRED",
    "UNSUPPORTED_SCHEME": "POLICY_DENIED",
    "FILE_DISABLED": "POLICY_DENIED",
    "BLOCKED_HOST": "POLICY_DENIED",
    "BLOCKED_ADDRESS": "POLICY_DENIED",
    "BLOCKED_PINNED_ADDRESS": "POLICY_DENIED",
    "REDIRECT_BLOCKED": "POLICY_DENIED",
    "REDIRECT_DOWNGRADE": "POLICY_DENIED",
    "REDIRECT_LOOP": "POLICY_DENIED",
    "BLOCKED_CONTENT_TYPE": "POLICY_DENIED",
    "TLS_FAILED": "POLICY_DENIED",
    "RESOLUTION_FAILED": "STALE_SOURCE",
    "BLOCKED_STATUS": "STALE_SOURCE",
    "FETCH_FAILED": "STALE_SOURCE",
    "FETCH_TIMEOUT": "RATE_LIMITED",
    "TOO_LARGE": "RATE_LIMITED",
    "TOO_MANY_REDIRECTS": "RATE_LIMITED",
    "RATE_LIMITED": "RATE_LIMITED",
}


class SafeFetchError(safety.UrlPolicyError):
    """带机器码的抓取失败；``c01`` 给出 C-01 家族码，``hop`` 标出错的重定向跳。"""

    def __init__(self, code: str, message: str, *, hop: Optional[int] = None,
                 c01: Optional[str] = None):
        super().__init__(code, message)
        self.c01 = c01 or C01_BY_CODE.get(code, "POLICY_DENIED")
        self.hop = hop


# ---------------------------------------------------------------------------
# 地址工具：比较落地地址时用规范形式（IPv4-mapped IPv6 与 IPv4 视为同一地址）
# ---------------------------------------------------------------------------

def _canonical_ip(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        ip = ipaddress.ip_address(text)
    except ValueError:
        return text.lower()
    mapped = getattr(ip, "ipv4_mapped", None)
    return str(mapped if mapped is not None else ip)


def _same_ip(left: Any, right: Any) -> bool:
    return _canonical_ip(left) == _canonical_ip(right)


def _peer_ip(sock) -> str:
    try:
        return str(sock.getpeername()[0])
    except (OSError, IndexError, TypeError):
        return ""


def _host_header(host: str, port: int, scheme: str) -> str:
    """Host 头：IPv6 加方括号；只有非默认端口才带端口。"""
    text = f"[{host}]" if ":" in str(host) else str(host)
    default = DEFAULT_PORTS.get(str(scheme).lower(), 80)
    if int(port) != int(default):
        text = f"{text}:{int(port)}"
    return text


def _request_target(url: str) -> str:
    parts = urllib.parse.urlsplit(str(url or ""))
    path = parts.path or "/"
    if parts.query:
        path = f"{path}?{parts.query}"
    return urllib.parse.quote(path, safe="/?&=%:@!$'()*+,;~-._")


def _lower_headers(headers) -> Dict[str, str]:
    out: Dict[str, str] = {}
    if headers is None:
        return out
    items = None
    try:
        items = headers.items()
    except AttributeError:
        try:
            items = list(headers)
        except TypeError:
            return out
    try:
        for key, value in items:
            out[str(key).strip().lower()] = str(value).strip()
    except (TypeError, ValueError):
        return out
    return out


def _as_bytes(raw) -> Tuple[bytes, int]:
    if isinstance(raw, bytes):
        return raw, len(raw)
    if isinstance(raw, bytearray):
        data = bytes(raw)
        return data, len(data)
    data = str(raw).encode("utf-8")
    return data, len(data)


def _declared_length(headers: Dict[str, str]) -> Optional[int]:
    raw = str(headers.get("content-length") or "").strip()
    return int(raw) if raw.isdigit() else None


# ---------------------------------------------------------------------------
# 判定：解析一次 + 全量判定 + 选一个可连接地址
# ---------------------------------------------------------------------------

def _resolve_and_pin(url: str, *, resolver, hop: int
                     ) -> Tuple[Dict[str, Any], List[str]]:
    """解析并判定一个 URL；返回 (check_url 结果, 全部通过判定的地址)。"""
    try:
        info = safety.check_url(url, resolver=resolver)
    except safety.UrlPolicyError as exc:
        raise SafeFetchError(str(exc.code), str(exc), hop=hop) from exc
    # check_url 已经拦了 file:// 与非 http(s)；这里是纵深防御，防止调用方
    # 换掉策略层后本模块悄悄连本地文件。
    scheme = str(info.get("scheme") or "").lower()
    if scheme == "file":
        raise SafeFetchError(
            "FILE_DISABLED", "file:// 不走网络抓取；请用注入的本地抓取器", hop=hop)
    if scheme not in safety.ALLOWED_SCHEMES:
        raise SafeFetchError(
            "UNSUPPORTED_SCHEME", f"只允许 http/https：{scheme or '(无协议)'}",
            hop=hop)
    addresses = [str(a) for a in (info.get("addresses") or []) if str(a).strip()]
    if not addresses:
        raise SafeFetchError(
            "RESOLUTION_FAILED",
            f"解析没有给出可固定的地址（{info.get('host')}），拒绝连接", hop=hop)
    for address in addresses:
        verdict = safety.classify_address(address)
        if not verdict["allowed"]:
            raise SafeFetchError(
                "BLOCKED_ADDRESS", f"{address}: {verdict['reason']}", hop=hop)
    return info, addresses


def _check_peer(peer: Any, pinned: str, *, hop: Optional[int] = None,
                require_public: bool = True) -> None:
    """连接层报告的落地地址必须等于固定地址；``require_public`` 再看是否公网。"""
    text = str(peer or "").strip()
    if not text:  # 注入连接层没有报告；默认连接层在 socket 层已强制核对
        return
    if not _same_ip(text, pinned):
        raise SafeFetchError(
            "BLOCKED_PINNED_ADDRESS",
            f"连接落到了 {text}，与已校验并固定的地址 {pinned} 不符，"
            "拒绝读取响应", hop=hop)
    if not require_public:
        return
    verdict = safety.classify_address(text)
    if not verdict["allowed"]:
        raise SafeFetchError(
            "BLOCKED_PINNED_ADDRESS",
            f"连接实际落到非公网地址 {text}（{verdict['reason']}），拒绝",
            hop=hop)


# ---------------------------------------------------------------------------
# 默认连接层：连 IP、Host/SNI 用原域名、读侧封顶、落地地址核对
# ---------------------------------------------------------------------------

def _default_tls_context() -> ssl.SSLContext:
    context = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
    context.check_hostname = True
    context.verify_mode = ssl.CERT_REQUIRED
    try:
        context.minimum_version = ssl.TLSVersion.TLSv1_2
    except (AttributeError, ValueError):  # 老版本/受限构建：不额外加限制
        pass
    return context


def _assert_verifying_context(context) -> None:
    if context is None:
        raise SafeFetchError("TLS_FAILED", "没有可用的 SSLContext，拒绝连接")
    if not hasattr(context, "verify_mode") or not hasattr(context, "check_hostname"):
        raise SafeFetchError("TLS_FAILED", "SSLContext 不可用，拒绝连接")
    if context.verify_mode != ssl.CERT_REQUIRED or not context.check_hostname:
        raise SafeFetchError(
            "TLS_FAILED",
            "SSLContext 关闭了证书校验或主机名校验，拒绝连接"
            "（HTTPS 必须针对原域名验证证书）")


def _assert_peer(sock, expected: str) -> None:
    """落地地址核对：``getpeername`` 必须等于已固定的 IP。

    ``expected`` 已经过 ``classify_address``（公网）并作为唯一连接目标交给
    socket，所以这里只需要证明确实连的是它。是否公网由 ``fetch`` 在解析阶段
    全量判定，本函数不重复判定（也让只连 127.0.0.1 的离线假服务器测试不需要
    放宽生产判定）。
    """
    peer = _peer_ip(sock)
    if not peer:
        raise SafeFetchError(
            "FETCH_FAILED", "无法确认连接对端地址，拒绝读取响应")
    _check_peer(peer, expected, require_public=False)


def default_connector(target: Dict[str, Any]) -> Dict[str, Any]:
    """默认连接层：socket 直接连 ``target["ip"]``，不做任何域名解析。

    ``target`` 键：``scheme`` / ``host``（原域名）/ ``port`` / ``ip`` /
    ``path`` / ``headers`` / ``max_bytes`` / ``connect_timeout`` /
    ``read_timeout`` / ``tls_context``（可选工厂）。
    返回 ``status`` / ``headers`` / ``body`` / ``peer_ip``。
    """
    scheme = str(target.get("scheme") or "").lower()
    host = str(target.get("host") or "")
    ip = _canonical_ip(target.get("ip"))
    if not ip:
        raise SafeFetchError("BLOCKED_PINNED_ADDRESS", "连接目标没有固定 IP，拒绝")
    port = int(target.get("port") or DEFAULT_PORTS.get(scheme, 80))
    path = str(target.get("path") or "/")
    byte_limit = int(target.get("max_bytes") or MAX_BYTES)
    connect_limit = float(target.get("connect_timeout") or CONNECT_TIMEOUT)
    read_limit = float(target.get("read_timeout") or READ_TIMEOUT)
    headers = {str(key): str(value)
               for key, value in (target.get("headers") or {}).items()}
    if not any(str(key).lower() == "host" for key in headers):
        headers["Host"] = _host_header(host, port, scheme)
    context = None
    if scheme == "https":
        factory = target.get("tls_context") or _default_tls_context
        context = factory() if callable(factory) else factory
        _assert_verifying_context(context)  # 连之前就拒绝「关掉校验」的上下文
    sock = None
    connection = None
    try:
        sock = socket.create_connection((ip, port), timeout=connect_limit)
        _assert_peer(sock, ip)  # 确认真的连到了这个 IP
        if scheme == "https":
            sock = context.wrap_socket(sock, server_hostname=host)  # SNI = 原域名
            _assert_peer(sock, ip)
        sock.settimeout(read_limit)
        connection = http.client.HTTPConnection(ip, port, timeout=read_limit)
        connection.sock = sock  # 复用已固定的连接，HTTPConnection 不会再解析
        connection.request("GET", path, headers=headers)
        response = connection.getresponse()
        body = response.read(byte_limit + 1)
        return {"status": int(response.status),
                "headers": response.getheaders(), "body": body,
                "peer_ip": _peer_ip(sock)}
    except SafeFetchError:
        raise
    except ssl.SSLError as exc:
        raise SafeFetchError(
            "TLS_FAILED",
            f"TLS 握手/证书校验失败：{type(exc).__name__}: {exc}") from exc
    except (socket.timeout, TimeoutError) as exc:
        raise SafeFetchError(
            "FETCH_TIMEOUT",
            f"连接或读取超时：{type(exc).__name__}: {exc}") from exc
    except (OSError, http.client.HTTPException) as exc:
        raise SafeFetchError(
            "FETCH_FAILED", f"抓取失败：{type(exc).__name__}: {exc}") from exc
    finally:
        if connection is not None:
            try:
                connection.close()
            except Exception:  # noqa: BLE001 关闭失败不影响已得到的结论
                pass
        elif sock is not None:
            try:
                sock.close()
            except Exception:  # noqa: BLE001
                pass


# ---------------------------------------------------------------------------
# 响应边界
# ---------------------------------------------------------------------------

def _check_status(status: int, *, hop: int) -> None:
    if 400 <= status < 500:
        raise SafeFetchError(
            "BLOCKED_STATUS",
            f"访问被拒（HTTP {status}）：保留原快照，不改基线", hop=hop)
    if status < 200 or status >= 300:
        raise SafeFetchError(
            "BLOCKED_STATUS",
            f"上游异常（HTTP {status}）：保留原快照，不改基线", hop=hop)


def _check_content_type(headers: Dict[str, str], *, hop: int) -> str:
    raw = str(headers.get("content-type") or "").split(";")[0].strip().lower()
    if not raw:
        return ""  # 有些服务器不给；正文质量检查兜底
    if raw in safety.ALLOWED_CONTENT_TYPES or raw.startswith("text/"):
        return raw
    raise SafeFetchError(
        "BLOCKED_CONTENT_TYPE",
        f"不抓这种内容类型：{raw}（只接受 HTML/纯文本）", hop=hop)


# ---------------------------------------------------------------------------
# 抓取：解析 → 判定 → 固定连接 → 校验响应（每一跳重新走一遍）
# ---------------------------------------------------------------------------

def fetch(url: str, *, resolver: Optional[Callable[[str, int], Sequence[str]]] = None,
          connector: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None,
          max_redirects: Optional[int] = None, max_bytes: Optional[int] = None,
          connect_timeout: Optional[float] = None,
          read_timeout: Optional[float] = None,
          max_seconds: Optional[float] = None,
          clock: Optional[Callable[[], float]] = None,
          tls_context: Optional[Callable[[], ssl.SSLContext]] = None
          ) -> Dict[str, Any]:
    """抓一页正文：所有上限都有默认值，超界一律明确失败。

    没有任何注入时（生产默认路径）：``resolver`` 用系统 DNS，
    ``connector`` 用 ``default_connector``（socket 直连已固定的 IP）。
    """
    resolve_fn = resolver or safety.default_resolver
    connect_fn = connector or default_connector
    clock_fn = clock or time.monotonic
    hop_limit = int(MAX_REDIRECTS if max_redirects is None else max_redirects)
    byte_limit = int(MAX_BYTES if max_bytes is None else max_bytes)
    connect_limit = float(CONNECT_TIMEOUT if connect_timeout is None
                          else connect_timeout)
    read_limit = float(READ_TIMEOUT if read_timeout is None else read_timeout)
    budget_limit = float(MAX_SECONDS if max_seconds is None else max_seconds)
    dialer = "default" if connector is None else "injected"

    started = clock_fn()
    current = str(url or "").strip()
    redirects: List[str] = []
    redirect_chain: List[Dict[str, Any]] = []
    hops: List[Dict[str, Any]] = []
    visited: List[str] = []
    resolved: List[str] = []
    pinned_ip = ""

    for hop in range(hop_limit + 1):
        if clock_fn() - started > budget_limit:
            raise SafeFetchError(
                "RATE_LIMITED",
                f"抓取总时长超过 {budget_limit:g} 秒预算，已中止（第 {hop} 跳）",
                hop=hop)
        try:
            info, addresses = _resolve_and_pin(current, resolver=resolve_fn,
                                               hop=hop)
        except SafeFetchError as exc:
            if redirects:
                raise SafeFetchError(
                    "REDIRECT_BLOCKED", f"重定向目标被拒绝：{exc}",
                    hop=hop) from exc
            raise
        pinned = addresses[0]
        pinned_ip = pinned
        for address in addresses:
            if address not in resolved:
                resolved.append(address)
        if redirect_chain and redirect_chain[-1].get("location") == current:
            # 每一行描述一条重定向边：来源 url/status/location + 目标这一跳的
            # 固定 IP。目标地址的解析就是本跳的这一次解析，不额外解析。
            redirect_chain[-1]["pinned_ip"] = pinned
            redirect_chain[-1]["resolved_ips"] = list(addresses)
        port = int(info["port"])
        target = {
            "url": current, "scheme": info["scheme"], "host": info["host"],
            "port": port, "ip": pinned, "addresses": list(addresses),
            "path": _request_target(current), "hop": hop,
            "headers": {"Host": _host_header(info["host"], port, info["scheme"]),
                        "User-Agent": USER_AGENT, "Accept": ACCEPT},
            "max_bytes": byte_limit, "connect_timeout": connect_limit,
            "read_timeout": read_limit, "tls_context": tls_context,
        }
        hop_record: Dict[str, Any] = {
            "hop": hop, "url": current, "scheme": info["scheme"],
            "host": info["host"], "port": port, "pinned_ip": pinned,
            "resolved_ips": list(addresses),
        }
        response = connect_fn(target) or {}
        _check_peer(response.get("peer_ip"), pinned, hop=hop)
        status = int(response.get("status") or 0)
        headers = _lower_headers(response.get("headers"))
        body, size = _as_bytes(response.get("body", b""))
        hop_record["status"] = status
        hop_record["bytes"] = size
        hops.append(hop_record)

        declared = _declared_length(headers)
        if declared is not None and declared > byte_limit:
            raise SafeFetchError(
                "TOO_LARGE",
                f"响应声明 {declared} 字节，超过上限 {byte_limit} 字节", hop=hop)
        if size > byte_limit:
            raise SafeFetchError(
                "TOO_LARGE",
                f"页面超过 {byte_limit} 字节，拒绝无界快照", hop=hop)

        if 300 <= status < 400:
            location = str(headers.get("location") or "").strip()
            if not location:
                raise SafeFetchError(
                    "REDIRECT_BLOCKED", f"重定向缺 Location：{current}", hop=hop)
            next_url = urllib.parse.urljoin(current, location)
            next_scheme = (urllib.parse.urlsplit(next_url).scheme or "").lower()
            here_scheme = (urllib.parse.urlsplit(current).scheme or "").lower()
            if next_scheme not in safety.ALLOWED_SCHEMES:
                raise SafeFetchError(
                    "REDIRECT_BLOCKED", f"重定向到非 http(s)：{next_url}", hop=hop)
            if next_url in visited or next_url == current:
                raise SafeFetchError(
                    "REDIRECT_LOOP", f"重定向成环：{next_url}", hop=hop)
            if len(redirects) >= hop_limit:
                raise SafeFetchError(
                    "TOO_MANY_REDIRECTS", f"重定向超过 {hop_limit} 跳", hop=hop)
            if here_scheme == "https" and next_scheme == "http":
                # 降级前先看地址：目标是内网就报「重定向到内网」，而不是降级。
                try:
                    _resolve_and_pin(next_url, resolver=resolve_fn, hop=hop + 1)
                except SafeFetchError as exc:
                    raise SafeFetchError(
                        "REDIRECT_BLOCKED", f"重定向目标被拒绝：{exc}",
                        hop=hop) from exc
                raise SafeFetchError(
                    "REDIRECT_DOWNGRADE",
                    f"拒绝 https→http 降级重定向：{next_url}", hop=hop)
            visited.append(current)
            redirect_chain.append({
                "hop": hop, "url": current, "status": status,
                "location": next_url, "host": info["host"],
                "from_pinned_ip": pinned,
                "pinned_ip": "", "resolved_ips": [],
            })
            redirects.append(next_url)
            current = next_url
            continue

        _check_status(status, hop=hop)
        _check_content_type(headers, hop=hop)
        result = {
            "url": url, "final_url": current, "status": status,
            "headers": headers,
            "content_type": headers.get("content-type", ""),
            "redirects": list(redirects), "redirect_chain": redirect_chain,
            "hops": hops, "bytes": size,
            "text": body.decode("utf-8", errors="replace"),
            "resolved_ips": resolved, "pinned_ip": pinned_ip,
            "allowed": True, "reason": "", "mode": "pinned",
            "connector": dialer,
            "elapsed": round(clock_fn() - started, 3),
        }
        result["record"] = _fetch_record(result)
        return result

    raise SafeFetchError(
        "TOO_MANY_REDIRECTS", f"重定向超过 {hop_limit} 跳", hop=hop_limit)


def _fetch_record(result: Dict[str, Any]) -> Dict[str, Any]:
    """可落盘的抓取记录（``core/watch.py`` 直接存这条）。"""
    hops = result.get("hops") or []
    return {
        "mode": result.get("mode", "pinned"),
        "connector": result.get("connector", ""),
        "url": result.get("url", ""),
        "final_url": result.get("final_url", ""),
        "allowed": bool(result.get("allowed")),
        "reason": str(result.get("reason") or ""),
        "resolved_ips": list(result.get("resolved_ips") or []),
        "pinned_ip": result.get("pinned_ip") or None,
        "redirect_chain": [dict(row) for row in (result.get("redirect_chain") or [])],
        "bytes": int(result.get("bytes") or 0),
        "status": int(result.get("status") or 0),
        "host": str(hops[-1].get("host") or "") if hops else "",
        "hop_count": len(hops),
        "elapsed": result.get("elapsed", 0.0),
    }
