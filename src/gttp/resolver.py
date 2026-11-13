"""Link lookup + Open Library check for the submit page.

Runs as the compose `resolver` service (same image, `python -m gttp.resolver`,
port 8102). It is the only component that fetches arbitrary URLs or reaches
openlibrary.org, and it holds no secrets and has no mounts. It sits on an
internal network shared only with `submit`: it binds to its IP on that
network and `verify_request` drops every peer except `submit` (and itself,
for the healthcheck).

Given {url?, title?, author?} it returns validated candidate strings plus
the list of changes it made and any warnings. Page and Open Library text can
only ever become those strings: nothing here writes a file or touches the
queue. Typed values win over the link, and a URL is only fetched when the
title or the author is still empty.

Stdlib only. Logs carry method, path, status and outcome codes, never URLs,
page text, titles or authors.
"""

from __future__ import annotations

import http.client
import json
import os
import signal
import socket
import sys
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

from . import casing, jobs, linkparse, openlibrary, safefetch
from .safefetch import FetchError

MAX_BODY = 4096
MAX_LIST = 6
MAX_TEXT = 300

W_NOT_FOUND = "Not found on Open Library; check the spelling."
W_UNVERIFIED = "Couldn't reach Open Library, so this isn't verified (capitalization only)."
W_AUTHOR = 'Open Library lists "{title}" by {authors}; check the author.'
W_LINK_FAILED = "Couldn't read that page; type the title and author."
W_LINK_BLOCKED = "That link can't be looked up (private or unsupported address)."
W_AMAZON_SLUG = ("Amazon didn't show the page, so the title was guessed from the link. "
                 "Check it and add the author.")
W_NO_AUTHOR = "The page didn't name an author; type it."
W_AUTHOR_GUESSED = "The author was guessed from the page; check it."
W_BAD_CANDIDATE = "The page's {field} couldn't be used; type it."

_MAX = {"title": jobs.TITLE_MAX, "author": jobs.AUTHOR_MAX}


class BadRequest(Exception):
    pass


def _valid(field: str, value: str) -> bool:
    return value == "" or jobs.validate_field(field, value, _MAX[field]) is None


def _clip(s: str) -> str:
    return s if len(s) <= MAX_TEXT else s[: MAX_TEXT - 1] + "…"


def check(req: dict, fetch_page, fetch_json, outcomes: list | None = None) -> dict:
    """The resolver's whole job. `fetch_page(url)` and `fetch_json(url,
    timeout)` are safefetch.fetch with settings bound (stubs in tests).
    Raises BadRequest on invalid input, never anything else on bad pages or
    Open Library trouble."""
    log = outcomes if outcomes is not None else []
    err, url, title, author = jobs.validate_check(req)
    if err:
        raise BadRequest(err)
    fields = {"title": title, "author": author}
    changes: list[dict] = []
    warnings: list[str] = []
    source = "typed"
    slug_mode = False

    def change(field, old, new, reason):
        changes.append({"field": field, "from": _clip(old), "to": _clip(new), "reason": reason})

    def warn(text):
        if text not in warnings:
            warnings.append(text)

    # 1. The link fills only the fields that are still empty.
    if url and (not title or not author):
        cand = linkparse.resolve_link(url, fetch_page)
        for note in cand.notes:
            log.append(f"link:{note}")
            if note == "blocked":
                warn(W_LINK_BLOCKED)
            elif note.startswith("fetch-failed"):
                warn(W_LINK_FAILED)
            elif note == "amazon-slug":
                warn(W_AMAZON_SLUG)
                slug_mode = True
        if not cand.notes:
            log.append("link:parsed")
        if not title and cand.title_raw:
            raw = cand.title_raw
            short = raw if slug_mode else linkparse.shorten_title(raw)
            if _valid("title", short) and short:
                fields["title"] = short
                source = "link"
                if short != raw:
                    change("title", raw, short, "link")
            else:
                slug_mode = False
                warn(W_BAD_CANDIDATE.format(field="title"))
        if not author:
            if cand.author_raw and _valid("author", cand.author_raw):
                fields["author"] = cand.author_raw
                if "author-guessed" in cand.notes:
                    warn(W_AUTHOR_GUESSED)
            elif cand.author_raw:
                warn(W_BAD_CANDIDATE.format(field="author"))
            elif fields["title"] and not slug_mode and not any(
                    n == "blocked" or n.startswith("fetch-failed") for n in cand.notes):
                warn(W_NO_AUTHOR)  # the page was read but named no author

    # 2. Deterministic casing.
    for field in ("title", "author"):
        if fields[field]:
            new, ch = casing.apply_casing(field, fields[field])
            if ch and _valid(field, new):
                fields[field] = new
                change(field, ch["from"], ch["to"], "capitalization")

    # 3. Open Library.
    if fields["title"]:
        typed_author = fields["author"]
        o = openlibrary.lookup(fields["title"], typed_author, fetch_json, slug_mode=slug_mode)
        log.append(f"ol:{o.kind}" + (f":{o.code}" if o.code else ""))
        if o.kind == "matched":
            proposed = {"title": o.title, "author": o.author}
            if slug_mode and not typed_author:
                proposed["author"] = ""  # the slug gives a title guess only
            for field in ("title", "author"):
                new = proposed[field]
                if new and new != fields[field] and _valid(field, new):
                    change(field, fields[field], new, "open-library")
                    fields[field] = new
        elif o.kind == "author_mismatch":
            if (openlibrary.normalize(o.ol_title) == openlibrary.normalize(fields["title"])
                    and o.ol_title != fields["title"] and _valid("title", o.ol_title)):
                change("title", fields["title"], o.ol_title, "open-library")
                fields["title"] = o.ol_title
            warn(W_AUTHOR.format(title=o.ol_title, authors=" and ".join(o.ol_authors[:2])))
        elif o.kind == "not_found":
            warn(W_NOT_FOUND)
        else:
            warn(W_UNVERIFIED)

    # 4. Final gate: "" or a valid value, nothing else leaves.
    for field in ("title", "author"):
        if not _valid(field, fields[field]):
            fields[field] = ""
            warn(W_BAD_CANDIDATE.format(field=field))

    return {
        "title": fields["title"],
        "author": fields["author"],
        "source": source,
        "changes": changes[:MAX_LIST],
        "warnings": [_clip(w) for w in warnings[:MAX_LIST]],
    }


# -- HTTP service ----------------------------------------------------------------

def _ny_date():
    return datetime.now(jobs.TZ).date()


class FetchBudget:
    """Outbound HTTP requests per America/New_York day (in memory: a
    container restart resets it)."""

    def __init__(self, cap: int, today=_ny_date):
        self.cap, self.today = cap, today
        self.day, self.used = today(), 0
        self.lock = threading.Lock()

    def __call__(self) -> bool:
        with self.lock:
            d = self.today()
            if d != self.day:
                self.day, self.used = d, 0
            if self.used >= self.cap:
                return False
            self.used += 1
            return True


class ResolverServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, addr, allowed_peers, fetch_page, fetch_json, budget, concurrency):
        self.allowed_peers = allowed_peers
        self.fetch_page = fetch_page
        self.fetch_json = fetch_json
        self.budget = budget
        self.slots = threading.BoundedSemaphore(concurrency)
        super().__init__(addr, Handler)

    def verify_request(self, request, client_address):
        try:
            ok = client_address[0] in self.allowed_peers()
        except Exception:
            ok = False
        if not ok:
            print(f"[resolver] dropped connection from {_safe_for_log(str(client_address[0]))}",
                  flush=True)
        return ok


def _safe_for_log(text: str) -> str:
    return "".join(c if c.isprintable() else "?" for c in text)[:200]


class Handler(BaseHTTPRequestHandler):
    server: ResolverServer
    server_version = "gttp-resolver"
    sys_version = ""
    timeout = 15
    outcome = ""

    def log_request(self, code="-", size="-"):
        path = urlsplit(self.path).path if getattr(self, "path", None) else "-"
        extra = f" {self.outcome}" if self.outcome else ""
        print(f"[resolver] {self.command} {_safe_for_log(path)} {code}{_safe_for_log(extra)}",
              flush=True)

    def log_message(self, format, *args):
        print(f"[resolver] {_safe_for_log(format % args)}", flush=True)

    def _send(self, code: int, body: bytes, ctype: str):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, payload: dict):
        self._send(code, json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _error(self, code: int, message: str):
        self._json(code, {"ok": False, "error": message})

    def do_GET(self):
        if urlsplit(self.path).path == "/healthz":
            return self._send(200, b"ok\n", "text/plain; charset=utf-8")
        self._error(404, "not found")

    def do_POST(self):
        if urlsplit(self.path).path != "/resolve":
            return self._error(404, "not found")
        ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if ctype != "application/json":
            return self._error(415, "Content-Type must be application/json")
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            return self._error(411, "Content-Length required")
        if length < 0:
            return self._error(400, "invalid Content-Length")
        if length > MAX_BODY:
            self.close_connection = True
            return self._error(413, f"request body over {MAX_BODY} bytes")
        try:
            data = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            return self._error(400, "body must be JSON")
        if not isinstance(data, dict):
            return self._error(400, "body must be a JSON object")
        if set(data) - jobs.CHECK_FIELDS:
            return self._error(400, "unexpected fields")
        if not self.server.slots.acquire(blocking=False):
            self.outcome = "busy"
            return self._json(429, {"ok": False, "error": "busy"})
        try:
            codes: list[str] = []
            result = check(data, self.server.fetch_page, self.server.fetch_json, codes)
            self.outcome = " ".join(codes)
            return self._json(200, {"ok": True, **result})
        except BadRequest as e:
            self.outcome = "bad-request"
            return self._error(400, str(e))
        except Exception as e:
            self.outcome = f"error:{type(e).__name__}"
            return self._error(500, "internal error")
        finally:
            self.server.slots.release()


def _budgeted(fn, budget):
    """Wrap a stub fetcher so each call is charged like one outbound request."""
    def wrapped(*args, **kw):
        if not budget():
            raise FetchError("limit")
        return fn(*args, **kw)
    return wrapped


def make_server(
    host: str,
    port: int,
    allowed_peers,
    fetch_page=None,
    fetch_json=None,
    daily_fetches: int = 200,
    concurrency: int = 2,
    fetch_opts: dict | None = None,
) -> ResolverServer:
    """fetch_page/fetch_json default to the real guarded fetcher (with
    `fetch_opts`, e.g. stub DNS, passed through); stubs given here are
    charged against the same daily budget, once per call."""
    budget = FetchBudget(daily_fetches)
    opts = dict(fetch_opts or {})

    if fetch_page is None:
        def fetch_page(url):
            return safefetch.fetch(
                url, accept=linkparse.PAGE_ACCEPT, max_bytes=linkparse.PAGE_MAX_BYTES,
                timeout=linkparse.PAGE_TIMEOUT, truncate_ok=True, budget=budget, **opts)
    else:
        fetch_page = _budgeted(fetch_page, budget)

    if fetch_json is None:
        def fetch_json(url, timeout):
            return safefetch.fetch(
                url, accept=openlibrary.ACCEPT, max_bytes=openlibrary.MAX_BYTES,
                timeout=timeout, truncate_ok=False, budget=budget, **opts)
    else:
        fetch_json = _budgeted(fetch_json, budget)

    return ResolverServer((host, port), allowed_peers, fetch_page, fetch_json, budget,
                          concurrency)


# -- entry point ---------------------------------------------------------------

def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    return int(raw) if raw else default


def _ipv4s(name: str) -> set[str]:
    try:
        return {i[4][0] for i in socket.getaddrinfo(name, None, socket.AF_INET)}
    except OSError:
        return set()


def resolve_bind(name: str, tries: int = 20, delay: float = 0.5) -> str | None:
    for i in range(tries):
        ips = sorted(_ipv4s(name))
        if ips:
            return ips[0]
        if i < tries - 1:
            time.sleep(delay)
    return None


class PeerList:
    """{bind IP} + the IPv4s of each allowed hostname, re-resolved at most
    every 30 s (so a recreated `submit` with a new IP is let in again)."""

    def __init__(self, bind_ip: str, names: list[str], ttl: float = 30.0, lookup=_ipv4s):
        self.base = {bind_ip} | ({"127.0.0.1"} if bind_ip.startswith("127.") else set())
        self.names, self.ttl, self.lookup = names, ttl, lookup
        self.peers: set[str] = set()
        self.at = float("-inf")
        self.lock = threading.Lock()

    def _refresh(self):
        peers = set()
        for n in self.names:
            peers |= self.lookup(n)
        self.peers, self.at = peers, time.monotonic()

    def __call__(self) -> set[str]:
        with self.lock:
            if time.monotonic() - self.at > self.ttl:
                self._refresh()
            return self.base | self.peers



def healthcheck() -> int:
    bind = os.environ.get("GTTP_RESOLVER_BIND") or "127.0.0.1"
    port = _int_env("GTTP_RESOLVER_PORT", 8102)
    ip = resolve_bind(bind, tries=1)
    if ip is None:
        return 1
    try:
        c = http.client.HTTPConnection(ip, port, timeout=3)
        c.request("GET", "/healthz")
        ok = c.getresponse().status == 200
        c.close()
        return 0 if ok else 1
    except (OSError, http.client.HTTPException):
        return 1


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["healthcheck"]:
        return healthcheck()
    bind = os.environ.get("GTTP_RESOLVER_BIND") or "127.0.0.1"
    port = _int_env("GTTP_RESOLVER_PORT", 8102)
    names = [n.strip() for n in (os.environ.get("GTTP_RESOLVER_ALLOW_FROM") or "").split(",")
             if n.strip()]
    bind_ip = resolve_bind(bind)
    if bind_ip is None:
        print("[resolver] could not resolve the bind address; exiting", flush=True)
        return 1
    peers = PeerList(bind_ip, names)
    server = make_server(bind_ip, port, peers,
                         daily_fetches=_int_env("GTTP_RESOLVER_DAILY_FETCHES", 200))
    print(f"[resolver] serving on {bind_ip}:{port}, {len(names)} allowed peer name(s)",
          flush=True)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    try:
        server.serve_forever()
    except KeyboardInterrupt:  # pragma: no cover
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
