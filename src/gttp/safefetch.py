"""SSRF-guarded, DNS-pinned, size- and time-capped HTTP GET.

Used only by the resolver service. Every hop (the first URL and each
redirect) goes through the same checks:

1. `check_url`: http/https only, no userinfo, only the scheme's default
   port, and a host that is a DNS name with an alphabetic TLD (so no IP
   literal in any form, dotted, decimal, octal, hex or IPv6, ever reaches
   DNS) and not an internal suffix (.local, .internal, .ts.net, ...).
2. DNS through `getaddrinfo` with a deadline; **every** returned address
   must pass `is_allowed_ip` (global, and none of the private, loopback,
   link-local, CGNAT/Tailscale, metadata, multicast, reserved, NAT64, 6to4
   or Teredo ranges; IPv4-mapped IPv6 is unwrapped first).
3. The socket dials the validated IP itself, so a second resolution can't
   swap in another address (DNS rebinding). Host header, TLS SNI and
   certificate checks still use the hostname.

`http.client` rather than urllib/requests on purpose: both of those follow
redirects on their own and honor HTTP(S)_PROXY env vars, which would bypass
the per-hop checks and the pinning.
"""

from __future__ import annotations

import http.client
import ipaddress
import re
import socket
import ssl
import threading
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass
from urllib.parse import quote, urljoin, urlsplit

from . import __version__

USER_AGENT = f"gttp-resolver/{__version__} (+https://gttp.mooseflip.com; book title lookup)"
MAX_URL = 2048
CHUNK = 16 * 1024
REDIRECTS = (301, 302, 303, 307, 308)
_DEFAULT_PORTS = {"http": 80, "https": 443}
_HOST_RE = re.compile(
    r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+(?:[a-z]{2,63}|xn--[a-z0-9-]{1,59})$"
)
_BLOCKED_SUFFIXES = (
    ".localhost", ".local", ".internal", ".lan", ".home", ".home.arpa", ".corp",
    ".ts.net", ".onion", ".test", ".invalid", ".example",
)
# Reserved for the test suite's stub DNS (check_url(allow_test_tlds=True)).
_TEST_SUFFIXES = (".test", ".example", ".invalid")
_PATH_SAFE = "/%:@!$&'()*+,;=-._~?"

_BLOCKED_NETS = [
    ipaddress.ip_network(n)
    for n in (
        "0.0.0.0/8", "10.0.0.0/8", "100.64.0.0/10", "127.0.0.0/8", "169.254.0.0/16",
        "172.16.0.0/12", "192.0.0.0/24", "192.0.2.0/24", "192.88.99.0/24",
        "192.168.0.0/16", "198.18.0.0/15", "198.51.100.0/24", "203.0.113.0/24",
        "224.0.0.0/4", "240.0.0.0/4", "255.255.255.255/32",
        "::/96", "::1/128", "64:ff9b::/96", "64:ff9b:1::/48", "100::/64", "2001::/23",
        "2001:db8::/32", "2002::/16", "fc00::/7", "fe80::/10", "fec0::/10", "ff00::/8",
    )
]

# getaddrinfo has no timeout of its own; run it here and wait with one.
_DNS_POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix="gttp-dns")


class FetchError(Exception):
    """A refused or failed fetch. `code` is stable (see the plan's §4.4)."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class FetchResult:
    url: str
    status: int
    content_type: str
    charset: str | None
    body: bytes
    truncated: bool


def _bad_char(c: str) -> bool:
    return c.isspace() or c == "\\" or unicodedata.category(c)[0] == "C"


def check_url(url: str, allow_test_tlds: bool = False) -> tuple[str, str, int, str]:
    """(scheme, host, port, path_and_query) for an allowed URL, else FetchError."""
    if not isinstance(url, str) or not url or len(url) > MAX_URL:
        raise FetchError("blocked-url")
    if any(_bad_char(c) for c in url if c != "\\"):
        raise FetchError("blocked-url")
    try:
        parts = urlsplit(url)
    except ValueError:
        raise FetchError("blocked-url") from None
    scheme = parts.scheme.lower()
    if scheme not in _DEFAULT_PORTS:
        raise FetchError("blocked-scheme")
    if "@" in parts.netloc or parts.username is not None or parts.password is not None:
        raise FetchError("blocked-userinfo")
    if "\\" in url:
        raise FetchError("blocked-url")
    try:
        port = parts.port
    except ValueError:
        raise FetchError("blocked-port") from None
    if port is not None and port != _DEFAULT_PORTS[scheme]:
        raise FetchError("blocked-port")
    host = (parts.hostname or "").lower()
    if host.endswith("."):
        host = host[:-1]
    if not host:
        raise FetchError("blocked-host")
    if not host.isascii():
        try:
            host = host.encode("idna").decode("ascii")
        except UnicodeError:
            raise FetchError("blocked-host") from None
    if not _HOST_RE.match(host):
        raise FetchError("blocked-host")
    test_ok = allow_test_tlds and host.endswith(_TEST_SUFFIXES)
    if not test_ok and ("." + host).endswith(_BLOCKED_SUFFIXES):
        raise FetchError("blocked-host")
    path = quote(parts.path or "/", safe=_PATH_SAFE)
    if parts.query:
        path += "?" + quote(parts.query, safe=_PATH_SAFE)
    return scheme, host, _DEFAULT_PORTS[scheme], path


def is_allowed_ip(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(str(ip).split("%")[0])
    except ValueError:
        return False
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None:
        addr = addr.ipv4_mapped
    if not addr.is_global:
        return False
    return not any(addr.version == n.version and addr in n for n in _BLOCKED_NETS)


def make_tls_context() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    ctx.check_hostname = True
    ctx.verify_mode = ssl.CERT_REQUIRED
    return ctx


def _default_connect(ip: str, port: int, timeout: float, tls_hostname: str | None):
    family = socket.AF_INET6 if ":" in ip else socket.AF_INET
    sock = socket.socket(family, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect((ip, port))
        if tls_hostname:
            sock = make_tls_context().wrap_socket(sock, server_hostname=tls_hostname)
    except BaseException:
        sock.close()
        raise
    return sock


class _PinnedConnection(http.client.HTTPConnection):
    """An HTTPConnection whose socket comes from us (already dialed to the
    validated IP, TLS already wrapped), so http.client never resolves."""

    def __init__(self, host: str, port: int, sock):
        super().__init__(host, port)
        self.default_port = port  # Host header without ":443" / ":80"
        self._pinned_sock = sock

    def connect(self):
        self.sock = self._pinned_sock


def _remaining(deadline: float) -> float:
    left = deadline - time.monotonic()
    if left <= 0:
        raise FetchError("timeout")
    return left


def _resolve(host: str, port: int, getaddrinfo, deadline: float) -> list[str]:
    fut = _DNS_POOL.submit(getaddrinfo, host, port, type=socket.SOCK_STREAM)
    try:
        infos = fut.result(timeout=_remaining(deadline))
    except FutureTimeout:
        raise FetchError("timeout") from None
    except FetchError:
        raise
    except (OSError, UnicodeError, ValueError):
        raise FetchError("dns-failed") from None
    addrs: list[str] = []
    for info in infos or []:
        try:
            a = str(info[4][0])
        except (IndexError, TypeError):
            continue
        if a not in addrs:
            addrs.append(a)
    if not addrs:
        raise FetchError("dns-failed")
    if not all(is_allowed_ip(a) for a in addrs):
        raise FetchError("blocked-address")
    return sorted(addrs, key=lambda a: ":" in a)  # IPv4 first


def _media_type(header: str | None) -> tuple[str, str | None]:
    if not header:
        return "", None
    parts = [p.strip() for p in header.split(";")]
    charset = None
    for p in parts[1:]:
        k, _, v = p.partition("=")
        if k.strip().lower() == "charset" and v.strip():
            charset = v.strip().strip('"').lower()
    return parts[0].lower(), charset


def read_body(resp, max_bytes: int, truncate_ok: bool) -> tuple[bytes, bool]:
    """Read in CHUNK pieces; never more than max_bytes + CHUNK."""
    buf = bytearray()
    while True:
        chunk = resp.read(CHUNK)
        if not chunk:
            return bytes(buf), False
        buf += chunk
        if len(buf) > max_bytes:
            if truncate_ok:
                return bytes(buf[:max_bytes]), True
            raise FetchError("too-large")


def _one_hop(scheme, host, port, path, ip_list, *, accept, max_bytes, truncate_ok,
             user_agent, connect, deadline):
    """GET one URL from the first validated IP that connects. Returns
    (status, location, FetchResult-or-None)."""
    tls_host = host if scheme == "https" else None
    sock = None
    last: Exception | None = None
    for ip in ip_list:
        try:
            sock = connect(ip, port, _remaining(deadline), tls_host)
            break
        except FetchError:
            raise
        except (OSError, ssl.SSLError) as exc:
            last = exc
    if sock is None:
        if isinstance(last, TimeoutError) or time.monotonic() >= deadline:
            raise FetchError("timeout")
        raise FetchError("connect-failed")

    fired = threading.Event()

    def _kill():
        fired.set()
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass

    watchdog = threading.Timer(max(0.0, deadline - time.monotonic()), _kill)
    watchdog.daemon = True
    watchdog.start()
    conn = _PinnedConnection(host, port, sock)
    try:
        try:
            sock.settimeout(_remaining(deadline))
            conn.putrequest("GET", path, skip_accept_encoding=True)
            conn.putheader("User-Agent", user_agent)
            conn.putheader("Accept", ", ".join(accept) + ", */*;q=0.1")
            conn.putheader("Accept-Language", "en-US,en;q=0.8")
            conn.putheader("Accept-Encoding", "identity")
            conn.putheader("Connection", "close")
            conn.endheaders()
            resp = conn.getresponse()
            status = resp.status
            if status in REDIRECTS:
                return status, resp.getheader("Location"), None
            if status != 200:
                raise FetchError(f"http-{status}")
            ctype, charset = _media_type(resp.getheader("Content-Type"))
            if ctype not in accept:
                raise FetchError("not-json" if "application/json" in accept else "not-html")
            enc = (resp.getheader("Content-Encoding") or "identity").strip().lower()
            if enc not in ("", "identity"):
                raise FetchError("bad-encoding")
            length = resp.getheader("Content-Length")
            if not truncate_ok and length and length.isdigit() and int(length) > max_bytes:
                raise FetchError("too-large")
            body, truncated = read_body(resp, max_bytes, truncate_ok)
            if fired.is_set():
                raise FetchError("timeout")
            return status, None, (ctype, charset, body, truncated)
        except FetchError:
            if fired.is_set():
                raise FetchError("timeout") from None
            raise
        except (TimeoutError, socket.timeout):
            raise FetchError("timeout") from None
        except (OSError, http.client.HTTPException, ValueError):
            if fired.is_set() or time.monotonic() >= deadline:
                raise FetchError("timeout") from None
            raise FetchError("bad-response") from None
    finally:
        watchdog.cancel()
        try:
            conn.close()
        except Exception:
            pass
        try:
            sock.close()
        except Exception:
            pass


def fetch(
    url: str,
    *,
    accept: tuple[str, ...],
    max_bytes: int,
    timeout: float,
    truncate_ok: bool,
    max_redirects: int = 3,
    user_agent: str = USER_AGENT,
    getaddrinfo=socket.getaddrinfo,
    connect=None,
    budget=None,
    allow_test_tlds: bool = False,
) -> FetchResult:
    """GET `url` under the guard. Raises FetchError with a stable code."""
    connect = connect or _default_connect
    deadline = time.monotonic() + timeout
    current = url
    hops = 0
    while True:
        scheme, host, port, path = check_url(current, allow_test_tlds)
        ips = _resolve(host, port, getaddrinfo, deadline)
        if budget is not None and not budget():
            raise FetchError("limit")
        status, location, got = _one_hop(
            scheme, host, port, path, ips, accept=accept, max_bytes=max_bytes,
            truncate_ok=truncate_ok, user_agent=user_agent, connect=connect,
            deadline=deadline,
        )
        if got is not None:
            ctype, charset, body, truncated = got
            return FetchResult(current, status, ctype, charset, body, truncated)
        if not location:
            raise FetchError("bad-redirect")
        try:
            current = urljoin(current, location.strip())
        except ValueError:
            raise FetchError("bad-redirect") from None
        hops += 1
        if hops > max_redirects:
            raise FetchError("too-many-redirects")
