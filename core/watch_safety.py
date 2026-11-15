#!/usr/bin/env python3
"""监控 URL 与响应边界（G-06）。

`core/watch.py` 负责快照与事件；本模块先回答一个更基本的问题：
**这个地址允许抓吗？** 以及抓回来的东西能不能当内容。

三件事：

1. URL 准入：只收 http/https。`file://` 只允许「显式本地模式 + 注入本地抓取器」
   （测试/离线演示），生产默认路径一律拒绝。
2. 解析后地址判定：host 是字面 IP 就按 IP 判；是主机名就先套名字规则
   （localhost 及子域、*.local、单段主机名），再做 DNS 解析，**所有**解析结果
   都必须落在公网（`is_global`）。loopback / RFC1918 / 链路本地 / 保留 / 组播 /
   IPv6 唯一本地 / IPv4 映射 IPv6 / 0.0.0.0 / 历史十进制八进制十六进制写法
   全部拒绝。判定逻辑是纯函数，测试给 host + 地址列表即可构造全矩阵。
3. 手工重定向：每一跳都重新做地址判定；跳数上限、https→http 降级、相对
   Location、非 http(s) 跳转都有明确策略；响应大小/内容类型/状态码/验证页
   边界在这里或 watch.py 里守住。

纪律：
- 失败一律 fail closed（解析失败、空地址列表、未知状态都拒绝）；
- 不用会自动跟随重定向的库默认行为；
- 不读凭据、不猜地址、不把验证页/登录墙当成内容变化；
- 注入 transport 的调用方自己负责网络边界；但没有 resolver 时，字面 IP、
  localhost、*.local、单段主机名这些静态规则仍然生效。
"""
from __future__ import annotations

import ipaddress
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, Dict, List, Optional, Sequence

MAX_BYTES = 2_000_000
MAX_REDIRECTS = 5
FETCH_TIMEOUT = 15
ALLOWED_SCHEMES = ("http", "https")
ALLOWED_CONTENT_TYPES = ("text/html", "text/plain", "application/xhtml+xml")
CHALLENGE_MARKERS = (
    "just a moment", "checking your browser", "verify you are human",
    "captcha", "sign in to continue", "sign into continue",
    "please log in", "please sign in", "login to continue",
    "access denied", "are you a robot", "enable javascript",
)

_USER_AGENT = "Mozilla/5.0 (compatible; GrowovoWatch/1.0)"

#: 明确非公网、但 ipaddress 在旧版本里可能归到 is_private 的网段。
_V4_NOT_GLOBAL = tuple(ipaddress.ip_network(n) for n in (
    "100.64.0.0/10",      # CGNAT
    "192.0.0.0/24",       # IETF protocol assignments
    "192.0.2.0/24",       # TEST-NET-1（文档）
    "198.18.0.0/15",      # benchmark
    "198.51.100.0/24",    # TEST-NET-2
    "203.0.113.0/24",     # TEST-NET-3
    "192.88.99.0/24",     # 6to4 relay anycast
))
_V6_NOT_GLOBAL = tuple(ipaddress.ip_network(n) for n in (
    "2001:db8::/32",      # 文档
    "2001:10::/28",       # ORCHID
    "2001:20::/28",       # ORCHIDv2
    "3fff::/20",          # 文档（RFC 9637）
))

_LOCAL_SUFFIXES = (".local", ".localdomain", ".internal", ".intranet",
                   ".home.arpa", ".lan")
_LEGACY_V4_RE = re.compile(r"^[0-9a-fA-FxX.]+$")


class UrlPolicyError(RuntimeError):
    """带机器码的拒绝；code 供调用方/CLI 分类（不是自然语言同义词）。"""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


# ---------------------------------------------------------------------------
# 地址 / 主机名判定（纯函数，不联网）
# ---------------------------------------------------------------------------

def _reason(ip) -> str:
    """给出可读且可断言的拒绝理由；顺序即安全优先级。"""
    if ip.version == 4:
        if ip.is_unspecified:
            return "unspecified (0.0.0.0)"
        if ip.is_loopback:
            return "loopback (127.0.0.0/8)"
        if ip.is_link_local:
            return "link_local (169.254.0.0/16)"
        if ip.is_multicast:
            return "multicast"
        if ip.is_reserved:
            return "reserved"
        if any(ip in net for net in _V4_NOT_GLOBAL):
            return "not_global (reserved/benchmark/documentation)"
        if ip.is_private:
            return "private (RFC1918)"
        if not ip.is_global:
            return "not_global"
        return ""
    if ip.is_unspecified:
        return "unspecified (::)"
    if ip.is_loopback:
        return "loopback (::1)"
    if ip.is_link_local:
        return "link_local (fe80::/10)"
    if ip.is_multicast:
        return "multicast (ff00::/8)"
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None:
        inner = _reason(mapped)
        return ("ipv4_mapped " + inner) if inner else ""
    for embedded in (getattr(ip, "sixtofour", None),
                     getattr(ip, "teredo", None)):
        if embedded is None:
            continue
        for part in (embedded if isinstance(embedded, tuple) else (embedded,)):
            if part is None:
                continue
            inner = _reason(part)
            if inner:
                return "embedded " + inner
    if ip.is_reserved:
        return "reserved"
    if any(ip in net for net in _V6_NOT_GLOBAL):
        return "not_global (documentation/protocol)"
    if ip.is_private:
        return "private (unique-local fc00::/7)"
    if not ip.is_global:
        return "not_global"
    return ""


def _legacy_ipv4(host: str) -> Optional[str]:
    """历史 shorthand（十进制/八进制/十六进制/少段）→ 点分十进制。"""
    text = host.strip()
    if not text or not _LEGACY_V4_RE.match(text):
        return None
    try:
        return socket.inet_ntoa(socket.inet_aton(text))
    except OSError:
        return None


def classify_address(address: str) -> Dict[str, Any]:
    """判断一个地址字符串能否作为抓取目标（不做 DNS，不抛异常）。"""
    text = str(address or "").strip()
    if not text:
        return {"allowed": False, "address": "", "version": 0,
                "reason": "empty address"}
    candidate = text
    try:
        ip = ipaddress.ip_address(candidate)
    except ValueError:
        legacy = _legacy_ipv4(candidate)
        if legacy is None:
            return {"allowed": False, "address": text, "version": 0,
                    "reason": "invalid address"}
        candidate = legacy
        try:
            ip = ipaddress.ip_address(candidate)
        except ValueError:
            return {"allowed": False, "address": text, "version": 0,
                    "reason": "invalid address"}
    reason = _reason(ip)
    return {"allowed": not reason, "address": str(ip),
            "version": ip.version, "reason": reason}


def _normalise_host(host: str) -> str:
    text = str(host or "").strip().lower()
    text = text.replace("\u3002", ".").replace("\uff0e", ".").replace("\uff61", ".")
    return text.rstrip(".")


def classify_host(host: str) -> Dict[str, Any]:
    """主机名静态规则；字面 IP / 历史写法交给 classify_address。"""
    text = _normalise_host(host)
    if not text:
        return {"allowed": False, "host": "", "normalized": "",
                "reason": "empty host"}
    if any(ch.isspace() or ord(ch) < 32 for ch in text):
        return {"allowed": False, "host": text, "normalized": text,
                "reason": "bad hostname (whitespace/control)"}
    try:
        ipaddress.ip_address(text)
        is_ip = True
    except ValueError:
        is_ip = _legacy_ipv4(text) is not None
    if is_ip:
        result = classify_address(text)
        return {"allowed": result["allowed"], "host": text,
                "normalized": text, "reason": result["reason"],
                "literal": True}
    if text == "localhost" or text.endswith(".localhost"):
        return {"allowed": False, "host": text, "normalized": text,
                "reason": "localhost"}
    if any(text.endswith(suffix) for suffix in _LOCAL_SUFFIXES):
        return {"allowed": False, "host": text, "normalized": text,
                "reason": "local suffix"}
    if "." not in text:
        return {"allowed": False, "host": text, "normalized": text,
                "reason": "single-label hostname"}
    try:
        text.encode("idna")
    except UnicodeError:
        return {"allowed": False, "host": text, "normalized": text,
                "reason": "bad hostname (idna)"}
    return {"allowed": True, "host": text, "normalized": text, "reason": "",
            "literal": False}


def default_resolver(host: str, port: int) -> List[str]:
    """真实 DNS；只在生产默认路径使用，测试必须注入 fake resolver。"""
    infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return sorted({str(info[4][0]) for info in infos})


def resolve_host(host: str, port: int, *,
                 resolver: Optional[Callable[[str, int], Sequence[str]]] = None
                 ) -> Dict[str, Any]:
    """解析并判定：所有地址都必须是公网，任一不合规即拒绝（DNS rebinding）。"""
    name = classify_host(host)
    if not name["allowed"]:
        return {"allowed": False, "host": host, "addresses": [],
                "reason": name["reason"]}
    if name.get("literal"):
        result = classify_address(host)
        return {"allowed": result["allowed"], "host": name["normalized"],
                "addresses": [result["address"]] if result["allowed"] else [],
                "reason": result["reason"]}
    if resolver is None:
        return {"allowed": True, "host": name["normalized"], "addresses": [],
                "resolution": "skipped", "reason": ""}
    try:
        addresses = [str(a) for a in (resolver(name["normalized"], int(port)) or [])]
    except Exception as exc:  # noqa: BLE001 解析失败 = 不能证明是公网 = 拒绝
        return {"allowed": False, "host": name["normalized"], "addresses": [],
                "reason": f"resolution failed: {type(exc).__name__}: {exc}"}
    addresses = [a for a in dict.fromkeys(addresses) if a]
    if not addresses:
        return {"allowed": False, "host": name["normalized"], "addresses": [],
                "reason": "resolution returned no addresses"}
    for address in addresses:
        verdict = classify_address(address)
        if not verdict["allowed"]:
            return {"allowed": False, "host": name["normalized"],
                    "addresses": addresses,
                    "reason": f"{address}: {verdict['reason']}"}
    return {"allowed": True, "host": name["normalized"],
            "addresses": addresses, "reason": ""}


# ---------------------------------------------------------------------------
# URL 校验
# ---------------------------------------------------------------------------

def check_url(url: str, *, resolver=None,
              allow_local_file: bool = False) -> Dict[str, Any]:
    """校验一个 URL；允许返回规范信息，拒绝抛 UrlPolicyError。"""
    raw = str(url or "").strip()
    if not raw:
        raise UrlPolicyError("BAD_URL", "URL 不能为空")
    if any(ch.isspace() or ord(ch) < 32 for ch in raw):
        raise UrlPolicyError("BAD_URL", f"URL 含空白/控制字符：{raw!r}")
    parts = urllib.parse.urlsplit(raw)
    scheme = (parts.scheme or "").lower()
    if scheme == "file":
        if not allow_local_file:
            raise UrlPolicyError(
                "FILE_DISABLED",
                "file:// 只在显式本地模式（测试/离线演示）可用；生产路径拒绝")
        if parts.netloc not in ("", "localhost"):
            raise UrlPolicyError("FILE_DISABLED",
                                 "file:// 只接受本机路径，不要带远端主机")
        path = urllib.parse.unquote(parts.path or "")
        if not path:
            raise UrlPolicyError("BAD_URL", "file:// 缺少本地路径")
        return {"allowed": True, "url": raw, "scheme": "file",
                "host": "", "port": 0, "path": path,
                "addresses": [], "resolution": "local"}
    if scheme not in ALLOWED_SCHEMES:
        raise UrlPolicyError("UNSUPPORTED_SCHEME",
                             f"只允许 http/https：{scheme or '(无协议)'}")
    if parts.username or parts.password:
        raise UrlPolicyError("BAD_URL", "URL 不能带用户名/密码")
    try:
        host = parts.hostname or ""
        port = parts.port
    except ValueError as exc:
        raise UrlPolicyError("BAD_URL", f"端口不合法：{exc}") from exc
    if not host:
        raise UrlPolicyError("BAD_URL", "URL 缺少主机名")
    if port is not None and (port <= 0 or port > 65535):
        raise UrlPolicyError("BAD_URL", f"端口超出范围：{port}")
    effective_port = int(port or (443 if scheme == "https" else 80))
    name = classify_host(host)
    if not name["allowed"]:
        raise UrlPolicyError("BLOCKED_HOST",
                             f"主机不可抓：{host}（{name['reason']}）")
    resolved = resolve_host(host, effective_port, resolver=resolver)
    if not resolved["allowed"]:
        code = ("BLOCKED_ADDRESS"
                if not resolved["reason"].startswith("resolution")
                else "RESOLUTION_FAILED")
        raise UrlPolicyError(code, f"地址不可抓：{host}（{resolved['reason']}）")
    return {"allowed": True, "url": raw, "scheme": scheme,
            "host": name["normalized"], "port": effective_port,
            "path": "", "addresses": resolved["addresses"],
            "resolution": resolved.get("resolution", "resolved")}


# ---------------------------------------------------------------------------
# 抓取：手工跟随重定向，每一跳重新判定
# ---------------------------------------------------------------------------

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # 交给调用方手工处理，绝不自动跟随


def _decode(raw) -> tuple:
    if isinstance(raw, bytes):
        return raw, len(raw)
    text = str(raw)
    return text.encode("utf-8"), len(text.encode("utf-8"))


def _headers_dict(headers) -> Dict[str, str]:
    out: Dict[str, str] = {}
    try:
        for key, value in (headers or {}).items():
            out[str(key).lower()] = str(value)
    except AttributeError:
        return out
    return out


def _default_transport(url: str) -> Dict[str, Any]:
    """生产传输层：只发一次 GET，不跟随重定向；大小在读侧封顶。"""
    request = urllib.request.Request(
        url, headers={"User-Agent": _USER_AGENT,
                      "Accept": "text/html,application/xhtml+xml,text/plain"})
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(request, timeout=FETCH_TIMEOUT) as resp:
            body = resp.read(MAX_BYTES + 1)
            return {"status": int(getattr(resp, "status", 200) or 200),
                    "headers": _headers_dict(resp.headers), "body": body}
    except urllib.error.HTTPError as exc:
        body = b""
        try:
            body = exc.read(MAX_BYTES + 1)
        except Exception:  # noqa: BLE001 错误页体不重要
            body = b""
        return {"status": int(exc.code), "headers": _headers_dict(exc.headers),
                "body": body}
    except Exception as exc:  # noqa: BLE001 超时/断网/协议错误都按抓取失败
        raise UrlPolicyError(
            "FETCH_FAILED", f"抓取失败：{type(exc).__name__}: {exc}") from exc


def _check_content_type(headers: Dict[str, str]) -> str:
    raw = str(headers.get("content-type") or "").split(";")[0].strip().lower()
    if not raw:
        return ""  # 有些服务器不给；正文质量检查兜底
    if raw in ALLOWED_CONTENT_TYPES or raw.startswith("text/"):
        return raw
    raise UrlPolicyError("BLOCKED_CONTENT_TYPE",
                         f"不抓这种内容类型：{raw}（只接受 HTML/纯文本）")


def fetch_page(url: str, *, transport=None, resolver=None,
               allow_local_file: bool = False,
               max_redirects: int = MAX_REDIRECTS,
               max_bytes: int = MAX_BYTES) -> Dict[str, Any]:
    """抓一页正文：手工重定向 + 逐跳判定 + 状态/类型/大小边界。"""
    current = str(url or "").strip()
    redirects: List[str] = []
    visited: List[str] = []
    do_transport = transport or _default_transport
    for _hop in range(int(max_redirects) + 1):
        try:
            info = check_url(current, resolver=resolver,
                             allow_local_file=allow_local_file)
        except UrlPolicyError as exc:
            if redirects:
                raise UrlPolicyError(
                    "REDIRECT_BLOCKED",
                    f"重定向目标被拒绝：{exc}") from exc
            raise
        if info["scheme"] == "file":
            raise UrlPolicyError("FILE_DISABLED",
                                 "fetch_page 不读本地文件；请用注入抓取器")
        response = do_transport(current) or {}
        status = int(response.get("status") or 0)
        headers = _headers_dict(response.get("headers"))
        if 300 <= status < 400:
            location = str(headers.get("location") or "").strip()
            if not location:
                raise UrlPolicyError("REDIRECT_BLOCKED",
                                     f"重定向缺 Location：{current}")
            next_url = urllib.parse.urljoin(current, location)
            next_scheme = (urllib.parse.urlsplit(next_url).scheme or "").lower()
            here_scheme = (urllib.parse.urlsplit(current).scheme or "").lower()
            if next_scheme not in ALLOWED_SCHEMES:
                raise UrlPolicyError("REDIRECT_BLOCKED",
                                     f"重定向到非 http(s)：{next_url}")
            if next_url in visited or next_url == current:
                raise UrlPolicyError("REDIRECT_LOOP",
                                     f"重定向成环：{next_url}")
            if len(redirects) >= int(max_redirects):
                raise UrlPolicyError("TOO_MANY_REDIRECTS",
                                     f"重定向超过 {max_redirects} 跳")
            if here_scheme == "https" and next_scheme == "http":
                # 降级前先看地址：目标是内网就报「重定向到内网」，而不是降级。
                try:
                    check_url(next_url, resolver=resolver,
                              allow_local_file=allow_local_file)
                except UrlPolicyError as exc:
                    raise UrlPolicyError(
                        "REDIRECT_BLOCKED",
                        f"重定向目标被拒绝：{exc}") from exc
                raise UrlPolicyError(
                    "REDIRECT_DOWNGRADE",
                    f"拒绝 https→http 降级重定向：{next_url}")
            visited.append(current)
            redirects.append(next_url)
            current = next_url
            continue
        if status in (401, 403, 407, 429, 451) or 400 <= status < 500:
            raise UrlPolicyError(
                "BLOCKED_STATUS",
                f"访问被拒（HTTP {status}）：保留原快照，不改基线")
        if status < 200 or status >= 300:
            raise UrlPolicyError(
                "BLOCKED_STATUS",
                f"上游异常（HTTP {status}）：保留原快照，不改基线")
        _check_content_type(headers)
        body, size = _decode(response.get("body", ""))
        if size > int(max_bytes):
            raise UrlPolicyError(
                "TOO_LARGE",
                f"页面超过 {int(max_bytes)} 字节，拒绝无界快照")
        return {"url": url, "final_url": current, "status": status,
                "headers": headers, "content_type": headers.get("content-type", ""),
                "redirects": redirects, "bytes": size,
                "text": body.decode("utf-8", errors="replace")}
    raise UrlPolicyError("TOO_MANY_REDIRECTS",
                         f"重定向超过 {max_redirects} 跳")


# ---------------------------------------------------------------------------
# 噪声过滤与结构化价格提示（事件真实性）
# ---------------------------------------------------------------------------

_UUID_RE = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I)
_ISO_RE = re.compile(
    r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2})?(?:\.\d+)?"
    r"(?:Z|[+-]\d{2}:?\d{2})?\b")
_TIME_RE = re.compile(r"\b\d{1,2}:\d{2}(?::\d{2})?\b")
_HEX_RE = re.compile(r"\b[0-9a-f]{12,}\b", re.I)
_LONGNUM_RE = re.compile(r"\b\d{8,}\b")
_TOKEN_RE = re.compile(r"\b[A-Za-z0-9_-]{24,}\b")
_PRICE_RE = re.compile(
    r"(?:[$€£¥]\s?\d+(?:[.,]\d+)?"
    r"|\b\d+(?:[.,]\d+)?\s?(?:USD|EUR|CNY|RMB|JPY"
    r"|dollars?|euros?|yuan|元|美元|日元))", re.I)


def denoise(text: str) -> str:
    """把时间戳/UUID/长 hex/长数字/长 token 折成占位符，再压空白。

    价格数字（49 / 99 / 1499）不会被吃掉：只有 ≥8 位连续数字才当噪声。
    """
    out = str(text or "")
    for pattern in (_UUID_RE, _ISO_RE, _TIME_RE, _HEX_RE, _LONGNUM_RE,
                    _TOKEN_RE):
        out = pattern.sub(" <noise> ", out)
    return re.sub(r"\s+", " ", out).strip()


def is_significant_change(before: str, after: str) -> bool:
    return denoise(before) != denoise(after)


def extract_price_tokens(text: str) -> List[str]:
    return [match.group(0) for match in _PRICE_RE.finditer(str(text or ""))]


def looks_like_challenge(text: str) -> Optional[str]:
    lowered = str(text or "").lower()
    for marker in CHALLENGE_MARKERS:
        if marker in lowered:
            return marker
    return None
