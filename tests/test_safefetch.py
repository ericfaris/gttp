"""The resolver's SSRF-guarded fetcher (gttp.safefetch). Every server here is
a local ThreadingHTTPServer on 127.0.0.1:0; DNS is stubbed and `connect`
reroutes to the local port, so nothing leaves the machine."""

import socket
import ssl
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from gttp import safefetch
from gttp.safefetch import FetchError, check_url, fetch, is_allowed_ip, read_body

PUBLIC_IP = "93.184.215.14"
HTML = ("text/html", "application/xhtml+xml")
JSON = ("application/json",)


class _Server(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):  # broken pipes are expected
        pass


@pytest.fixture
def server():
    routes = {}
    seen = []

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            seen.append({"path": self.path, "headers": dict(self.headers)})
            fn = routes.get(self.path.split("?")[0])
            if fn is None:
                self.send_response(404)
                self.end_headers()
                return
            fn(self)

    srv = _Server(("127.0.0.1", 0), H)
    t = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
    t.start()
    srv.routes, srv.seen, srv.port = routes, seen, srv.server_address[1]
    yield srv
    srv.shutdown()
    srv.server_close()


def dns(*ips, calls=None):
    def getaddrinfo(host, port, type=0, **kw):
        if calls is not None:
            calls.append(host)
        return [(socket.AF_INET6 if ":" in ip else socket.AF_INET, socket.SOCK_STREAM, 6, "",
                 (ip, port)) for ip in ips]
    return getaddrinfo


def rerouter(srv, dialed):
    def connect(ip, port, timeout, tls_hostname):
        dialed.append(ip)
        return socket.create_connection(("127.0.0.1", srv.port), timeout=timeout)
    return connect


def no_connect(*a, **k):
    raise AssertionError("connect must not be called")


def html_page(body=b"<html><title>x</title></html>", ctype="text/html; charset=utf-8", extra=None):
    def fn(h):
        h.send_response(200)
        h.send_header("Content-Type", ctype)
        for k, v in (extra or {}).items():
            h.send_header(k, v)
        h.send_header("Content-Length", str(len(body)))
        h.end_headers()
        h.wfile.write(body)
    return fn


def redirect(location, code=302):
    def fn(h):
        h.send_response(code)
        h.send_header("Location", location)
        h.send_header("Content-Length", "0")
        h.end_headers()
    return fn


def get(srv, url, dialed=None, getaddrinfo=None, **kw):
    dialed = [] if dialed is None else dialed
    opts = dict(accept=HTML, max_bytes=1_048_576, timeout=1.0, truncate_ok=True,
                getaddrinfo=getaddrinfo or dns(PUBLIC_IP), connect=rerouter(srv, dialed),
                allow_test_tlds=True)
    opts.update(kw)
    return fetch(url, **opts)


# -- check_url ---------------------------------------------------------------

@pytest.mark.parametrize("url, code", [
    ("ftp://goodreads.com/", "blocked-scheme"),
    ("file:///etc/passwd", "blocked-scheme"),
    ("javascript:alert(1)", "blocked-scheme"),
    ("data:text/html,hi", "blocked-scheme"),
    ("gopher://goodreads.com/", "blocked-scheme"),
    ("https://u:p@goodreads.com/", "blocked-userinfo"),
    ("https://u@goodreads.com/", "blocked-userinfo"),
    ("http://evil.com\\@goodreads.com/", "blocked-userinfo"),
    ("https://goodreads.com:8443/", "blocked-port"),
    ("https://goodreads.com:80/", "blocked-port"),
    ("http://goodreads.com:443/", "blocked-port"),
    ("http://goodreads.com:99999/", "blocked-port"),
    ("http://2130706433/", "blocked-host"),
    ("http://0177.0.0.1/", "blocked-host"),
    ("http://0x7f.1/", "blocked-host"),
    ("http://127.1/", "blocked-host"),
    ("http://127.0.0.1/", "blocked-host"),
    ("http://[::1]/", "blocked-host"),
    ("http://[::ffff:127.0.0.1]/", "blocked-host"),
    ("http://localhost/", "blocked-host"),
    ("http://foo.localhost/", "blocked-host"),
    ("http://metadata.google.internal/", "blocked-host"),
    ("http://host.docker.internal/", "blocked-host"),
    ("http://box.tail1234.ts.net/", "blocked-host"),
    ("http://printer.local/", "blocked-host"),
    ("http://app/", "blocked-host"),
    ("http://gttp-resolver/", "blocked-host"),
    ("http://%31%32%37.0.0.1/", "blocked-host"),
    ("http://169.254.169.254/latest/meta-data/", "blocked-host"),
    ("http://books.test.example/", "blocked-host"),  # test TLDs only with the test flag
    ("https://good reads.com/", "blocked-url"),
    ("https://goodreads.com/a\nb", "blocked-url"),
    ("https://goodreads.com/\x00", "blocked-url"),
    ("https://goodreads.com/a\\b", "blocked-url"),
    ("https://goodreads.com/" + "a" * 2048, "blocked-url"),
    ("", "blocked-url"),
])
def test_check_url_rejects(url, code):
    with pytest.raises(FetchError) as e:
        check_url(url)
    assert e.value.code == code


def test_check_url_accepts():
    assert check_url("https://goodreads.com:443/x") == ("https", "goodreads.com", 443, "/x")
    assert check_url("https://www.goodreads.com/book/show/1")[1] == "www.goodreads.com"
    assert check_url("https://goodreads.com./x")[1] == "goodreads.com"
    assert check_url("http://Example.COM")[1:] == ("example.com", 80, "/")
    assert check_url("https://bücher.de/ä?q=é#frag")[1:] == (
        "xn--bcher-kva.de", 443, "/%C3%A4?q=%C3%A9")
    assert check_url("http://books.test.example/", allow_test_tlds=True)[1] == "books.test.example"


# -- is_allowed_ip -----------------------------------------------------------

@pytest.mark.parametrize("ip", [
    "0.0.0.0", "10.1.2.3", "100.64.0.1", "100.100.100.100", "100.88.10.29", "127.0.0.1",
    "169.254.169.254", "172.16.0.1", "172.24.0.1", "192.0.0.8", "192.168.1.1", "198.18.0.1",
    "224.0.0.1", "240.0.0.1", "255.255.255.255", "::", "::1", "fe80::1", "fe80::1%eth0",
    "fc00::1", "fd7a:115c:a1e0::1", "fd00:ec2::254", "::ffff:127.0.0.1", "::ffff:10.0.0.1",
    "::ffff:169.254.169.254", "::127.0.0.1", "64:ff9b::7f00:1", "2002:7f00:1::1", "2001::1",
    "2001:db8::1", "ff02::1", "not-an-ip", "",
])
def test_blocked_ips(ip):
    assert is_allowed_ip(ip) is False


@pytest.mark.parametrize("ip", ["93.184.215.14", "151.101.1.69", "2606:4700:4700::1111"])
def test_allowed_ips(ip):
    assert is_allowed_ip(ip) is True


# -- DNS -----------------------------------------------------------------------

def _code(fn):
    with pytest.raises(FetchError) as e:
        fn()
    return e.value.code


def test_private_dns_answer_is_blocked_before_connect(server):
    for ips in [("10.0.0.1",), ("127.0.0.1",), (PUBLIC_IP, "192.168.1.5"), ("::1",)]:
        assert _code(lambda: fetch(
            "http://books.test.example/", accept=HTML, max_bytes=1000, timeout=1,
            truncate_ok=True, getaddrinfo=dns(*ips), connect=no_connect,
            allow_test_tlds=True)) == "blocked-address"


def test_dns_failures(server):
    def boom(*a, **k):
        raise socket.gaierror("nope")
    assert _code(lambda: get(server, "http://books.test.example/", getaddrinfo=boom)) == "dns-failed"
    assert _code(lambda: get(server, "http://books.test.example/",
                             getaddrinfo=lambda *a, **k: [])) == "dns-failed"


def test_slow_dns_times_out(server):
    def slow(*a, **k):
        time.sleep(1.0)
        return dns(PUBLIC_IP)(*a, **k)
    t0 = time.monotonic()
    assert _code(lambda: get(server, "http://books.test.example/", getaddrinfo=slow,
                             timeout=0.3)) == "timeout"
    assert time.monotonic() - t0 < 0.9


def test_dns_is_pinned_once_per_hop(server):
    server.routes["/"] = html_page()
    calls, dialed = [], []

    def flipping(host, port, type=0, **kw):
        calls.append(host)
        ip = PUBLIC_IP if len(calls) == 1 else "127.0.0.1"
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port))]

    r = get(server, "http://books.test.example/", dialed=dialed, getaddrinfo=flipping)
    assert r.status == 200
    assert calls == ["books.test.example"]
    assert dialed == [PUBLIC_IP]
    assert server.seen[-1]["headers"]["Host"] == "books.test.example"


# -- redirects -------------------------------------------------------------------

def test_redirect_to_private_host_is_blocked(server):
    server.routes["/"] = redirect("http://internal.test.example/")
    dialed = []

    def by_host(host, port, type=0, **kw):
        ip = "10.0.0.1" if host.startswith("internal") else PUBLIC_IP
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port))]

    assert _code(lambda: get(server, "http://books.test.example/", dialed=dialed,
                             getaddrinfo=by_host)) == "blocked-address"
    assert dialed == [PUBLIC_IP]  # only the first hop was dialed


@pytest.mark.parametrize("location, code", [
    ("http://127.0.0.1:8102/", "blocked-port"),
    ("http://127.0.0.1/", "blocked-host"),
    ("file:///etc/passwd", "blocked-scheme"),
    ("https://u:p@x.example/", "blocked-userinfo"),
])
def test_redirect_targets_are_rechecked(server, location, code):
    server.routes["/"] = redirect(location)
    assert _code(lambda: get(server, "http://books.test.example/")) == code


def test_redirect_cap(server):
    for i in range(4):
        server.routes[f"/r{i}"] = redirect(f"http://books.test.example/r{i + 1}")
    server.routes["/r4"] = html_page()
    assert _code(lambda: get(server, "http://books.test.example/r0")) == "too-many-redirects"
    r = get(server, "http://books.test.example/r1")  # 3 redirects is fine
    assert r.url == "http://books.test.example/r4" and r.status == 200


def test_relative_redirect_is_followed(server):
    server.routes["/a/b"] = redirect("../final", 301)
    server.routes["/final"] = html_page()
    assert get(server, "http://books.test.example/a/b").url == "http://books.test.example/final"


def test_missing_location(server):
    def fn(h):
        h.send_response(302)
        h.send_header("Content-Length", "0")
        h.end_headers()
    server.routes["/"] = fn
    assert _code(lambda: get(server, "http://books.test.example/")) == "bad-redirect"


# -- body size, content type, status ------------------------------------------------

def test_big_html_is_truncated(server):
    server.routes["/"] = html_page(b"x" * (2 * 1024 * 1024))
    r = get(server, "http://books.test.example/")
    assert r.truncated is True and len(r.body) == 1_048_576


def test_big_json_is_rejected(server):
    server.routes["/sized"] = html_page(b"x" * 300_000, ctype="application/json")

    def unsized(h):  # no Content-Length: read until the cap
        h.send_response(200)
        h.send_header("Content-Type", "application/json")
        h.end_headers()
        h.wfile.write(b"x" * 300_000)
    server.routes["/unsized"] = unsized
    for path in ("/sized", "/unsized"):
        assert _code(lambda: get(server, "http://books.test.example" + path, accept=JSON,
                                 max_bytes=262_144, truncate_ok=False)) == "too-large"


def test_read_body_never_reads_past_the_cap():
    class Resp:
        delivered = 0

        def read(self, n):
            Resp.delivered += n
            return b"x" * n

    body, truncated = read_body(Resp(), 100_000, True)
    assert truncated and len(body) == 100_000
    assert Resp.delivered <= 100_000 + safefetch.CHUNK
    Resp.delivered = 0
    with pytest.raises(FetchError):
        read_body(Resp(), 100_000, False)
    assert Resp.delivered <= 100_000 + safefetch.CHUNK


def test_content_type_and_encoding(server):
    server.routes["/png"] = html_page(b"\x89PNG", ctype="image/png")
    server.routes["/gz"] = html_page(extra={"Content-Encoding": "gzip"})
    server.routes["/latin"] = html_page(ctype="text/html; charset=ISO-8859-1")
    server.routes["/json-as-html"] = html_page(ctype="text/html")

    def no_type(h):
        h.send_response(200)
        h.send_header("Content-Length", "2")
        h.end_headers()
        h.wfile.write(b"hi")
    server.routes["/none"] = no_type
    base = "http://books.test.example"
    assert _code(lambda: get(server, base + "/png")) == "not-html"
    assert _code(lambda: get(server, base + "/none")) == "not-html"
    assert _code(lambda: get(server, base + "/gz")) == "bad-encoding"
    assert get(server, base + "/latin").charset == "iso-8859-1"
    assert _code(lambda: get(server, base + "/json-as-html", accept=JSON,
                             truncate_ok=False)) == "not-json"


def test_http_errors(server):
    server.routes["/gone"] = lambda h: (h.send_response(503), h.end_headers())
    assert _code(lambda: get(server, "http://books.test.example/missing")) == "http-404"
    assert _code(lambda: get(server, "http://books.test.example/gone")) == "http-503"


# -- timeouts -------------------------------------------------------------------

def test_slow_server_times_out(server):
    def slow(h):
        time.sleep(2)
    server.routes["/"] = slow
    t0 = time.monotonic()
    assert _code(lambda: get(server, "http://books.test.example/", timeout=0.5)) == "timeout"
    assert time.monotonic() - t0 < 1.5


def test_dripping_headers_hit_the_watchdog(server):
    def drip(h):
        for b in b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nX-Pad: " + b"a" * 100:
            h.wfile.write(bytes([b]))
            h.wfile.flush()
            time.sleep(0.1)
    server.routes["/"] = drip
    t0 = time.monotonic()
    assert _code(lambda: get(server, "http://books.test.example/", timeout=0.5)) == "timeout"
    assert time.monotonic() - t0 < 1.5


def test_connect_failure(server):
    def refuse(*a, **k):
        raise ConnectionRefusedError()
    assert _code(lambda: get(server, "http://books.test.example/",
                             connect=refuse)) == "connect-failed"


# -- request shape, budget, TLS ----------------------------------------------------

def test_request_headers(server):
    server.routes["/"] = html_page()
    get(server, "http://books.test.example/?q=1")
    h = {k.lower(): v for k, v in server.seen[-1]["headers"].items()}
    assert "cookie" not in h and "authorization" not in h
    assert h["accept-encoding"] == "identity"
    assert h["user-agent"].startswith("gttp-resolver/")
    assert server.seen[-1]["path"] == "/?q=1"


def test_budget_exhausted_makes_no_connection(server):
    assert _code(lambda: fetch(
        "http://books.test.example/", accept=HTML, max_bytes=1000, timeout=1,
        truncate_ok=True, getaddrinfo=dns(PUBLIC_IP), connect=no_connect,
        budget=lambda: False, allow_test_tlds=True)) == "limit"


def test_budget_is_charged_per_hop(server):
    server.routes["/a"] = redirect("/b")
    server.routes["/b"] = html_page()
    spent = []
    get(server, "http://books.test.example/a", budget=lambda: spent.append(1) or True)
    assert len(spent) == 2


def test_tls_context_verifies():
    ctx = safefetch.make_tls_context()
    assert ctx.check_hostname is True and ctx.verify_mode == ssl.CERT_REQUIRED
