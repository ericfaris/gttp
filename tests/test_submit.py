"""The submit service (gttp.submit) against a scratch queue and catalog."""

import json
import threading
import urllib.error
import urllib.request
from datetime import datetime

import pytest

from gttp import jobs
from gttp.submit import CSP, make_server

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
    assert data["limits"] == {"daily_cap": 5, "today": 2, "max_queued": 3, "active": 2}


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
