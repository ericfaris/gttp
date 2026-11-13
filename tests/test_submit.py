"""The submit service (gttp.submit) against a scratch queue and catalog."""

import json
import socket
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from gttp import jobs
from gttp import resolver as resolver_mod
from gttp.submit import (
    CSP,
    W_LIMIT,
    W_RESOLVER_BUSY,
    W_RESOLVER_DOWN,
    make_server,
    validate_resolver_result,
)

CATALOG = "books:\n- title: Atomic Habits\n  author: James Clear\n"


@pytest.fixture
def svc(tmp_path):
    queue = tmp_path / "queue"
    queue.mkdir()
    catalog = tmp_path / "books.yaml"
    catalog.write_text(CATALOG)
    server = make_server(queue, catalog, daily_cap=5, max_queued=3, port=0, host="127.0.0.1")
    t = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
    t.start()
    server.base = f"http://127.0.0.1:{server.server_address[1]}"
    yield server
    server.shutdown()
    server.server_close()


def call(svc, method, path, body=None, headers=None, raw=None):
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    hdrs = {"Content-Type": "application/json"} if data is not None else {}
    hdrs.update(headers or {})
    req = urllib.request.Request(svc.base + path, data=data, method=method, headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


def submit(svc, title, author="Some Author", **kw):
    code, _, body = call(svc, "POST", "/api/submit", {"title": title, "author": author}, **kw)
    return code, json.loads(body)


def queue_files(svc):
    return sorted(p.name for p in svc.queue_dir.iterdir())


def test_valid_submit_creates_one_entry(svc):
    code, body = submit(svc, "The Power of Habit", "Charles Duhigg")
    assert code == 201 and body["ok"] is True
    (name,) = queue_files(svc)
    entry = json.loads((svc.queue_dir / name).read_text())
    assert entry == body["entry"]
    assert entry["v"] == 1 and entry["state"] == "queued"
    assert entry["slug"] == "the-power-of-habit" and entry["author"] == "Charles Duhigg"
    assert name == f"{entry['id']}.json"


@pytest.mark.parametrize(
    "payload, message",
    [
        ({"title": "", "author": "x"}, "title is required"),
        ({"author": "x"}, "title is required"),
        ({"title": "Grit"}, "author is required"),
        ({"title": " Grit", "author": "x"}, "title has leading or trailing spaces"),
        ({"title": "x" * 121, "author": "x"}, "title must be at most 120 characters"),
        ({"title": "Grit", "author": "x" * 81}, "author must be at most 80 characters"),
        ({"title": "Bad\nTitle", "author": "x"}, "title contains control or invisible characters"),
        ({"title": "Bad​Title", "author": "x"}, "title contains control or invisible characters"),
        ({"title": "-rf", "author": "x"}, "title must not start with '-'"),
        ({"title": 5, "author": "x"}, "title must be text"),
        ({"title": "Café", "author": "x"},
         "title must contain letters or digits (A-Z, 0-9) for its page address"),
        ({"title": "Grit", "author": "x", "extra": 1}, "unexpected fields"),
    ],
)
def test_invalid_input_is_rejected(svc, payload, message):
    code, _, body = call(svc, "POST", "/api/submit", payload)
    assert code == 400
    assert json.loads(body) == {"ok": False, "error": message}
    assert queue_files(svc) == []


def test_non_object_and_bad_json(svc):
    assert call(svc, "POST", "/api/submit", ["Grit"])[0] == 400
    assert call(svc, "POST", "/api/submit", raw=b"{nope")[0] == 400


def test_duplicate_of_catalog_book(svc):
    code, body = submit(svc, "atomic habits", "James Clear")
    assert code == 409 and body["error"] == "'atomic habits' is already in the catalog"


def test_catalog_is_read_per_request(svc):
    assert submit(svc, "Grit")[0] == 201
    svc.catalog_path.write_text(CATALOG + "- title: Deep Work\n")
    assert submit(svc, "Deep Work")[0] == 409


def test_duplicate_of_queued_and_done(svc):
    assert submit(svc, "Grit")[0] == 201
    code, body = submit(svc, "Grit")
    assert code == 409 and body["error"] == "'Grit' is already queued"
    (entry,) = jobs.read_entries(svc.queue_dir)
    done = jobs.transition(jobs.transition(entry, "running"), "done")
    jobs.write_entry(svc.queue_dir, done)
    code, body = submit(svc, "Grit")
    assert code == 409 and body["error"] == "'Grit' was already published"


def test_failed_entry_does_not_block(svc):
    assert submit(svc, "Grit")[0] == 201
    (entry,) = jobs.read_entries(svc.queue_dir)
    jobs.write_entry(svc.queue_dir, jobs.transition(entry, "failed", "insufficient evidence"))
    assert submit(svc, "Grit")[0] == 201


def _seed(svc, n, state="failed"):
    for i in range(n):
        e = {**jobs.new_entry(f"Seed {i}", "x", f"seed-{i}"), "state": state}
        jobs.write_entry(svc.queue_dir, e)


def test_daily_cap(svc):
    _seed(svc, 5)
    code, body = submit(svc, "Grit")
    assert code == 429 and body["error"] == "daily limit reached (5 per day)"


def test_yesterdays_entries_do_not_count(svc):
    old = datetime(2020, 1, 1, 12, 0, tzinfo=jobs.TZ)
    for i in range(5):
        e = {**jobs.new_entry(f"Old {i}", "x", f"old-{i}", now=old), "state": "failed"}
        jobs.write_entry(svc.queue_dir, e)
    assert submit(svc, "Grit")[0] == 201


def test_queue_depth_cap(svc):
    _seed(svc, 3, state="queued")
    code, body = submit(svc, "Grit")
    assert code == 429 and body["error"] == "queue is full (3 waiting)"


def test_wrong_content_type(svc):
    code, _, _ = call(svc, "POST", "/api/submit", raw=b"title=x",
                      headers={"Content-Type": "application/x-www-form-urlencoded"})
    assert code == 415


def test_body_too_large(svc):
    code, _, _ = call(svc, "POST", "/api/submit", {"title": "x" * 5000, "author": "y"})
    assert code == 413
    assert queue_files(svc) == []


def test_bad_origin(svc):
    code, body = submit(svc, "Grit", headers={"Origin": "https://evil.example"})
    assert code == 403
    assert submit(svc, "Grit", headers={"Origin": "https://submit-gttp.mooseflip.com"})[0] == 201


@pytest.mark.parametrize("path", [
    "/fonts/../config.py", "/static/../../.env", "/../books.yaml", "/submit.py",
    "/fonts/young-serif.woff2/..", "/%2e%2e/books.yaml", "/api/nope",
])
def test_no_path_traversal(svc, path):
    assert call(svc, "GET", path)[0] == 404


def test_static_assets(svc):
    code, headers, body = call(svc, "GET", "/")
    assert code == 200 and b"Add a book" in body
    assert headers["Content-Security-Policy"] == CSP
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert headers["Referrer-Policy"] == "no-referrer"
    for path in ["/submit.js", "/style.css", "/logo.svg", "/logo-dark.svg",
                 "/mark.svg", "/favicon.ico", "/fonts/young-serif.woff2"]:
        code, headers, _ = call(svc, "GET", path)
        assert code == 200, path
        assert headers["Cache-Control"] == "max-age=300", path
        assert "Content-Security-Policy" not in headers, path
    code, headers, body = call(svc, "HEAD", "/")
    assert code == 200 and body == b"" and headers["Content-Security-Policy"] == CSP
    assert call(svc, "GET", "/healthz")[2] == b"ok\n"


def test_status(svc):
    for t in ["Grit", "Mindset"]:
        submit(svc, t)
    code, headers, body = call(svc, "GET", "/api/status")
    data = json.loads(body)
    assert code == 200 and headers["Cache-Control"] == "no-store"
    assert [e["title"] for e in data["entries"]] == ["Mindset", "Grit"]  # newest first
    assert set(data["entries"][0]) == {
        "id", "title", "author", "state", "message", "created_at", "updated_at", "commit", "usd",
    }
    assert data["limits"] == {"daily_cap": 5, "today": 2, "max_queued": 3, "active": 2,
                              "check_cap": 30, "checks_today": 0}


def test_status_shows_at_most_20(svc):
    _seed(svc, 25)
    data = json.loads(call(svc, "GET", "/api/status")[2])
    assert len(data["entries"]) == 20


def test_concurrent_submissions_respect_the_cap(svc):
    svc.max_queued = 100
    codes = []

    def go(i):
        codes.append(submit(svc, f"Book {i}")[0])

    threads = [threading.Thread(target=go, args=(i,)) for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(codes) == [201] * 5 + [429] * 5
    assert len(queue_files(svc)) == 5


def test_main_refuses_unwritable_queue(tmp_path, monkeypatch, capsys):
    from gttp import submit as submit_mod

    monkeypatch.setenv("GTTP_QUEUE_DIR", str(tmp_path / "missing"))
    assert submit_mod.main() == 1
    assert "is not writable by uid" in capsys.readouterr().out



# -- /api/check ------------------------------------------------------------------

FIX = Path(__file__).parent / "fixtures"
GOOD = {"ok": True, "title": "The Power of Habit", "author": "Charles Duhigg",
        "source": "typed", "changes": [], "warnings": []}


class FakeResolver(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self):
        self.reply = (200, json.dumps(GOOD).encode())
        self.hits = 0
        self.delay = 0.0
        self.entered = threading.Event()
        self.gate = None
        fake = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                self.rfile.read(int(self.headers.get("Content-Length", "0")))
                fake.hits += 1
                fake.entered.set()
                if fake.gate is not None:
                    fake.gate.wait(5)
                if fake.delay:
                    time.sleep(fake.delay)
                code, body = fake.reply
                try:
                    self.send_response(code)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                except OSError:
                    pass

        super().__init__(("127.0.0.1", 0), H)

    def handle_error(self, *a):
        pass


@pytest.fixture
def check_svc(tmp_path):
    made = []

    def make(resolver_url=None, catalog=CATALOG, **kw):
        fake = None
        if resolver_url is None:
            fake = FakeResolver()
            threading.Thread(target=fake.serve_forever, kwargs={"poll_interval": 0.02},
                             daemon=True).start()
            made.append(fake)
            resolver_url = f"http://127.0.0.1:{fake.server_address[1]}"
        queue = tmp_path / "queue"
        queue.mkdir(exist_ok=True)
        cat = tmp_path / "books.yaml"
        cat.write_text(catalog)
        server = make_server(queue, cat, port=0, host="127.0.0.1",
                             resolver_url=resolver_url, **kw)
        threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.02},
                         daemon=True).start()
        server.base = f"http://127.0.0.1:{server.server_address[1]}"
        server.fake = fake
        made.append(server)
        return server

    yield make
    for s in made:
        s.shutdown()
        s.server_close()


def check(svc, body, **kw):
    code, _, raw = call(svc, "POST", "/api/check", body, **kw)
    return code, json.loads(raw)


def _real_resolver(fixture="empty.json"):
    def fetch_json(url, timeout):
        return (FIX / "openlibrary" / fixture).read_bytes()

    def no_pages(url):
        raise AssertionError("no page fetch expected")

    srv = resolver_mod.make_server("127.0.0.1", 0, lambda: {"127.0.0.1"},
                                   fetch_page=no_pages, fetch_json=fetch_json)
    threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.02},
                     daemon=True).start()
    return srv


def test_check_integration_with_the_real_resolver(check_svc):
    res = _real_resolver("charisma.json")
    try:
        url = f"http://127.0.0.1:{res.server_address[1]}"
        svc = check_svc(resolver_url=url,
                        catalog=CATALOG + "- title: The Charisma Myth\n  author: Olivia Fox Cabane\n")
        code, body = check(svc, {"title": "the charisma myth", "author": "olivia fox cabane"})
        assert code == 200
        assert body == {
            "ok": True, "title": "The Charisma Myth", "author": "Olivia Fox Cabane",
            "source": "typed",
            "changes": [
                {"field": "title", "from": "the charisma myth", "to": "The Charisma Myth",
                 "reason": "capitalization"},
                {"field": "author", "from": "olivia fox cabane", "to": "Olivia Fox Cabane",
                 "reason": "capitalization"},
            ],
            "warnings": [],
            "duplicate": "'The Charisma Myth' is already in the catalog",
            "ready": False,
        }
        code, body = check(svc, {"title": "the power of habit", "author": "charles duhigg"})
        assert code == 200 and body["ready"] is True and body["duplicate"] is None
        assert (body["title"], body["author"]) == ("The Power of Habit", "Charles Duhigg")
        assert queue_files(svc) == []
    finally:
        res.shutdown()
        res.server_close()


def test_check_flags_a_queued_duplicate(check_svc):
    svc = check_svc()
    assert submit(svc, "The Power of Habit", "Charles Duhigg")[0] == 201
    code, body = check(svc, {"title": "The Power of Habit", "author": "Charles Duhigg"})
    assert body["duplicate"] == "'The Power of Habit' is already queued"
    assert body["ready"] is False
    assert len(queue_files(svc)) == 1  # the check itself wrote nothing


@pytest.mark.parametrize("payload, message", [
    ({}, "give a link or a title"),
    ({"author": "x"}, "give a link or a title"),
    ({"url": "ftp://x"}, "link must start with http:// or https://"),
    ({"url": "https://x.org/" + "a" * 2040}, "link must be at most 2048 characters"),
    ({"url": "https://x.org/\n"}, "link contains control or invisible characters"),
    ({"title": "Bad​Title"}, "title contains control or invisible characters"),
    ({"url": 5}, "link must be text"),
    ({"title": "Grit", "isbn": "1"}, "unexpected fields"),
])
def test_check_validation(check_svc, payload, message):
    svc = check_svc()
    assert check(svc, payload) == (400, {"ok": False, "error": message})
    assert svc.fake.hits == 0


def test_check_transport_errors(check_svc):
    svc = check_svc()
    assert call(svc, "POST", "/api/check", raw=b"title=x",
                headers={"Content-Type": "application/x-www-form-urlencoded"})[0] == 415
    assert call(svc, "POST", "/api/check", {"title": "x" * 5000})[0] == 413
    assert check(svc, {"title": "Grit"}, headers={"Origin": "https://evil.example"})[0] == 403


def _closed_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_resolver_down_falls_back_to_casing(check_svc):
    svc = check_svc(resolver_url=f"http://127.0.0.1:{_closed_port()}")
    code, body = check(svc, {"title": "the power of habit", "author": "charles duhigg"})
    assert code == 200
    assert (body["title"], body["author"]) == ("The Power of Habit", "Charles Duhigg")
    assert body["warnings"] == [W_RESOLVER_DOWN] and body["ready"] is True
    assert len(body["changes"]) == 2


def test_resolver_down_with_only_a_link(check_svc):
    svc = check_svc(resolver_url=f"http://127.0.0.1:{_closed_port()}")
    code, body = check(svc, {"url": "https://www.goodreads.com/book/show/1"})
    assert code == 200 and (body["title"], body["author"]) == ("", "")
    assert body["warnings"] == [W_RESOLVER_DOWN, resolver_mod.W_LINK_FAILED]
    assert body["ready"] is False


def test_resolver_timeout_falls_back(check_svc):
    svc = check_svc(resolver_timeout=0.3)
    svc.fake.delay = 1.0
    t0 = time.monotonic()
    code, body = check(svc, {"title": "grit", "author": "angela duckworth"})
    assert code == 200 and body["warnings"] == [W_RESOLVER_DOWN] and body["title"] == "Grit"
    assert time.monotonic() - t0 < 1.0


def _bad(**over):
    return json.dumps({**GOOD, **over}).encode()


@pytest.mark.parametrize("reply, warning", [
    ((500, b'{"ok": false}'), W_RESOLVER_DOWN),
    ((429, b'{"ok": false, "error": "busy"}'), W_RESOLVER_BUSY),
    ((200, b"not json"), W_RESOLVER_DOWN),
    ((200, json.dumps({**GOOD, "state": "done"}).encode()), W_RESOLVER_DOWN),
    ((200, _bad(title="Evil‮Title")), W_RESOLVER_DOWN),
    ((200, _bad(author="A" * 200)), W_RESOLVER_DOWN),
    ((200, _bad(changes=[{"field": "title", "from": "a", "to": "b",
                          "reason": "capitalization"}] * 50)), W_RESOLVER_DOWN),
    ((200, _bad(changes=[{"field": "title", "from": "a", "to": "b", "reason": "rm -rf"}])),
     W_RESOLVER_DOWN),
    ((200, _bad(source="admin")), W_RESOLVER_DOWN),
    ((200, _bad(warnings=["x\n" * 3])), W_RESOLVER_DOWN),
    ((200, b"[" * 20000), W_RESOLVER_DOWN),
])
def test_untrusted_resolver_answers_fall_back(check_svc, reply, warning):
    svc = check_svc()
    svc.fake.reply = reply
    code, body = check(svc, {"title": "grit", "author": "angela duckworth"})
    assert code == 200 and body["warnings"] == [warning]
    assert (body["title"], body["author"]) == ("Grit", "Angela Duckworth")
    blob = json.dumps(body)
    for bad in ("\\u202e", "AAAA", "rm -rf", "admin", '"state"'):
        assert bad not in blob


def test_validate_resolver_result_builds_a_new_dict():
    out = validate_resolver_result(GOOD)
    assert out == {k: v for k, v in GOOD.items() if k != "ok"}
    assert out is not GOOD
    assert validate_resolver_result({**GOOD, "ok": 1}) is None
    assert validate_resolver_result({**GOOD, "title": 5}) is None
    assert validate_resolver_result(None) is None


def test_daily_check_cap(check_svc):
    svc = check_svc(check_cap=2)
    for _ in range(2):
        assert check(svc, {"title": "Grit"})[1]["warnings"] == []
    code, body = check(svc, {"title": "grit"})
    assert code == 200 and body["warnings"] == [W_LIMIT] and body["title"] == "Grit"
    assert svc.fake.hits == 2
    status = json.loads(call(svc, "GET", "/api/status")[2])
    assert status["limits"]["checks_today"] == 2 and status["limits"]["check_cap"] == 2


def test_check_burst_limit(check_svc):
    now = [1000.0]
    svc = check_svc(check_burst=2, clock=lambda: now[0])
    assert check(svc, {"title": "Grit"})[0] == 200
    assert check(svc, {"title": "Grit"})[0] == 200
    assert check(svc, {"title": "Grit"}) == (429, {"ok": False,
                                                   "error": "too many checks; wait a minute"})
    now[0] += 61
    assert check(svc, {"title": "Grit"})[0] == 200


def test_one_check_in_flight(check_svc):
    svc = check_svc()
    svc.fake.gate = threading.Event()
    first = {}
    t = threading.Thread(target=lambda: first.update(r=check(svc, {"title": "Grit"})))
    t.start()
    assert svc.fake.entered.wait(5)
    assert check(svc, {"title": "Mindset"}) == (
        429, {"ok": False, "error": "a check is already running; try again in a moment"})
    svc.fake.gate.set()
    t.join(5)
    assert first["r"][0] == 200


def test_check_slug_rule_warning(check_svc):
    svc = check_svc()
    svc.fake.reply = (200, _bad(title="Café", author="X"))
    code, body = check(svc, {"title": "Café", "author": "X"})
    assert body["ready"] is False
    assert "title must contain letters or digits (A-Z, 0-9) for its page address" in body["warnings"]


def test_unreadable_catalog_is_a_warning(check_svc):
    svc = check_svc()
    svc.catalog_path.write_text("books: [")
    code, body = check(svc, {"title": "Grit", "author": "Angela Duckworth"})
    assert code == 200 and "Couldn't check the catalog for duplicates." in body["warnings"]
    assert body["ready"] is False
