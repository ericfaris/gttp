"""Private book-submission service: a form, a queue status API, nothing else.

Runs as the compose `submit` service (same image as `app`,
`python -m gttp.submit`), bound to 127.0.0.1:8101 and reachable from outside
only through Cloudflare Access at submit-gttp.mooseflip.com. Its one mount is
`.cache/queue/`; it holds no secrets, has no git and can't run builds. A
submission only writes a `queued` entry (see gttp.jobs). The host-side
worker (gttp.worker) builds, validates, commits and pushes it.

Stdlib only. Static files are served from a fixed table built at startup,
so no request path is ever joined onto a filesystem path.
"""

from __future__ import annotations

import json
import os
import re
import signal
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from . import jobs
from .config import BOOKS_FILE, STATIC_DIR

ASSETS_DIR = Path(__file__).resolve().parent / "submit_assets"
MAX_BODY = 4096
STATUS_LIMIT = 20
DEFAULT_ORIGINS = (
    "https://submit-gttp.mooseflip.com,http://127.0.0.1:8101,http://localhost:8101"
)
_FONT_RE = re.compile(r"^[a-z0-9-]+\.woff2$")
_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
    ".woff2": "font/woff2",
}
CSP = (
    "default-src 'self'; img-src 'self' data:; script-src 'self'; "
    "style-src 'self'; connect-src 'self'; frame-ancestors 'none'; "
    "base-uri 'none'; form-action 'self'"
)
_STATUS_FIELDS = (
    "id", "title", "author", "state", "message", "created_at", "updated_at",
    "commit", "usd",
)


def build_routes(assets_dir: Path = ASSETS_DIR, static_dir: Path = STATIC_DIR) -> dict:
    """URL path -> file, for the only files this service will ever serve."""
    routes = {
        "/": assets_dir / "index.html",
        "/submit.js": assets_dir / "submit.js",
    }
    for name in ("style.css", "favicon.ico", "logo.svg", "logo-dark.svg", "mark.svg"):
        routes[f"/{name}"] = static_dir / name
    fonts = static_dir / "fonts"
    if fonts.is_dir():
        for f in fonts.iterdir():
            if f.is_file() and _FONT_RE.match(f.name):
                routes[f"/fonts/{f.name}"] = f
    return {k: v for k, v in routes.items() if v.is_file()}


class SubmitServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, addr, queue_dir, catalog_path, daily_cap, max_queued, origins):
        self.queue_dir = Path(queue_dir)
        self.catalog_path = Path(catalog_path)
        self.daily_cap = daily_cap
        self.max_queued = max_queued
        self.origins = set(origins)
        self.routes = build_routes()
        # One lock around read-check-write, so two concurrent submissions
        # can't both pass the daily cap or the duplicate check.
        self.submit_lock = threading.Lock()
        super().__init__(addr, Handler)


def _safe_for_log(text: str) -> str:
    return "".join(c if c.isprintable() else "?" for c in text)[:200]


class Handler(BaseHTTPRequestHandler):
    server: SubmitServer
    server_version = "gttp-submit"
    sys_version = ""
    timeout = 15  # seconds per socket read: a stalled client can't pin a thread

    # -- logging: method, path and status only, never bodies ---------------
    def log_request(self, code="-", size="-"):
        path = urlsplit(self.path).path if getattr(self, "path", None) else "-"
        print(f"[submit] {self.command} {_safe_for_log(path)} {code}", flush=True)

    def log_message(self, format, *args):
        print(f"[submit] {_safe_for_log(format % args)}", flush=True)

    # -- responses ----------------------------------------------------------
    def _send(self, code: int, body: bytes, ctype: str, cache: str, head_only=False):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", cache)
        if ctype.startswith("text/html"):
            self.send_header("Content-Security-Policy", CSP)
        self.end_headers()
        if not head_only:
            self.wfile.write(body)

    def _json(self, code: int, payload: dict, head_only=False):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send(code, body, "application/json; charset=utf-8", "no-store", head_only)

    def _error(self, code: int, message: str):
        self._json(code, {"ok": False, "error": message})

    # -- GET / HEAD -----------------------------------------------------------
    def do_HEAD(self):
        self._get(head_only=True)

    def do_GET(self):
        self._get(head_only=False)

    def _get(self, head_only: bool):
        path = urlsplit(self.path).path
        if path == "/healthz":
            return self._send(200, b"ok\n", "text/plain; charset=utf-8", "no-store", head_only)
        if path == "/api/status":
            return self._json(200, self._status(), head_only)
        file = self.server.routes.get(path)
        if file is None:
            return self._json(404, {"ok": False, "error": "not found"}, head_only)
        try:
            body = file.read_bytes()
        except OSError:
            return self._json(404, {"ok": False, "error": "not found"}, head_only)
        ctype = _TYPES.get(file.suffix, "application/octet-stream")
        cache = "no-cache" if path == "/" else "max-age=300"
        self._send(200, body, ctype, cache, head_only)

    def _status(self) -> dict:
        entries = jobs.read_entries(self.server.queue_dir)
        recent = list(reversed(entries))[:STATUS_LIMIT]
        return {
            "entries": [{k: e.get(k) for k in _STATUS_FIELDS} for e in recent],
            "limits": {
                "daily_cap": self.server.daily_cap,
                "today": jobs.count_today(entries),
                "max_queued": self.server.max_queued,
                "active": jobs.active_count(entries),
            },
        }

    # -- POST -------------------------------------------------------------------
    def do_POST(self):
        if urlsplit(self.path).path != "/api/submit":
            return self._error(404, "not found")
        origin = self.headers.get("Origin")
        if origin is not None and origin not in self.server.origins:
            return self._error(403, "origin not allowed")
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
        if set(data) - {"title", "author"}:
            return self._error(400, "unexpected fields")
        title, author = data.get("title", ""), data.get("author", "")
        err, slug = jobs.validate_submission(title, author)
        if err:
            return self._error(400, err)

        with self.server.submit_lock:
            entries = jobs.read_entries(self.server.queue_dir)
            try:
                catalog = jobs.catalog_slugs(self.server.catalog_path)
            except Exception as exc:
                print(f"[submit] catalog unreadable: {type(exc).__name__}", flush=True)
                return self._error(500, "catalog unavailable; try again later")
            dup = jobs.find_duplicate(slug, entries, catalog, title)
            if dup:
                return self._error(409, dup)
            cap = self.server.daily_cap
            if jobs.count_today(entries) >= cap:
                return self._error(429, f"daily limit reached ({cap} per day)")
            if jobs.active_count(entries) >= self.server.max_queued:
                return self._error(429, f"queue is full ({self.server.max_queued} waiting)")
            entry = jobs.new_entry(title, author, slug)
            try:
                jobs.write_entry(self.server.queue_dir, entry)
            except OSError as exc:
                print(f"[submit] queue write failed: {type(exc).__name__}", flush=True)
                return self._error(500, "could not save the submission")
        self._json(201, {"ok": True, "entry": entry})


def make_server(
    queue_dir: Path,
    catalog_path: Path = BOOKS_FILE,
    daily_cap: int = 5,
    max_queued: int = 3,
    origins=tuple(DEFAULT_ORIGINS.split(",")),
    port: int = 8101,
    host: str = "0.0.0.0",
) -> SubmitServer:
    return SubmitServer((host, port), queue_dir, catalog_path, daily_cap, max_queued, origins)


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    return int(raw) if raw else default


def main() -> int:
    queue_dir = Path(os.environ.get("GTTP_QUEUE_DIR") or jobs.QUEUE_DIR)
    if not (queue_dir.is_dir() and os.access(queue_dir, os.W_OK | os.X_OK)):
        print(
            f"[submit] queue dir {queue_dir} is not writable by uid {os.getuid()}",
            flush=True,
        )
        return 1
    origins = [
        o.strip()
        for o in (os.environ.get("GTTP_SUBMIT_ORIGINS") or DEFAULT_ORIGINS).split(",")
        if o.strip()
    ]
    server = make_server(
        queue_dir,
        daily_cap=_int_env("GTTP_SUBMIT_DAILY_CAP", 5),
        max_queued=_int_env("GTTP_SUBMIT_MAX_QUEUED", 3),
        origins=origins,
        port=_int_env("GTTP_SUBMIT_PORT", 8101),
    )
    print(f"[submit] serving on :{server.server_address[1]}, queue {queue_dir}", flush=True)
    # As the container's PID 1, Python ignores SIGTERM unless it handles it,
    # and `docker stop` would wait out its 10 s grace period.
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    try:
        server.serve_forever()
    except KeyboardInterrupt:  # pragma: no cover
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
