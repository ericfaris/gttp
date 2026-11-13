"""The resolver service (gttp.resolver): check() with stub fetchers, the HTTP
server on 127.0.0.1:0, and the prompt-injection fixture. No real network."""

import json
import shutil
import socket
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from gttp import jobs, resolver, safefetch
from gttp.config import BOOKS_FILE
from gttp.resolver import (
    W_AMAZON_SLUG,
    W_AUTHOR_GUESSED,
    W_LINK_BLOCKED,
    W_LINK_FAILED,
    W_NOT_FOUND,
    W_UNVERIFIED,
    BadRequest,
    check,
    make_server,
)
from gttp.safefetch import FetchError, FetchResult
from gttp.submit import validate_resolver_result

FIX = Path(__file__).parent / "fixtures"
AMAZON_URL = "https://www.amazon.com/Charisma-Myth-Science-Personal-Magnetism/dp/1591845947"
GOODREADS_URL = "https://www.goodreads.com/book/show/13436116-the-charisma-myth"
HOSTILE_URL = "https://hostile.example.org/the-quiet-lever"
KEYS = {"title", "author", "source", "changes", "warnings"}


def pages(mapping):
    """fetch_page stub: URL -> fixture page name (or an exception)."""
    def fetch(url):
        v = mapping[url]
        if isinstance(v, Exception):
            raise v
        return FetchResult(url, 200, "text/html", "utf-8",
                           (FIX / "pages" / v).read_bytes(), False)
    return fetch


def ol(name="empty.json", raises=None, calls=None):
    def fetch(url, timeout):
        if calls is not None:
            calls.append(url)
        if raises:
            raise raises
        return (FIX / "openlibrary" / name).read_bytes()
    return fetch


def no_pages(url):
    raise AssertionError("the page must not be fetched")


# -- AC2: typed ----------------------------------------------------------------

def test_typed_charisma():
    r = check({"title": "the charisma myth", "author": "olivia fox cabane"},
               no_pages, ol("charisma.json"))
    assert r == {
        "title": "The Charisma Myth", "author": "Olivia Fox Cabane", "source": "typed",
        "changes": [
            {"field": "title", "from": "the charisma myth", "to": "The Charisma Myth",
             "reason": "capitalization"},
            {"field": "author", "from": "olivia fox cabane", "to": "Olivia Fox Cabane",
             "reason": "capitalization"},
        ],
        "warnings": [],
    }


def test_typed_names_are_not_mangled():
    r = check({"title": "essentialism", "author": "greg mckeown"}, no_pages, ol("mckeown.json"))
    assert (r["title"], r["author"]) == ("Essentialism", "Greg McKeown")
    r = check({"title": "tiny experiments", "author": "anne-laure le cunff"}, no_pages, ol())
    assert r["author"] == "Anne-Laure Le Cunff"
    assert r["warnings"] == [W_NOT_FOUND]


def test_open_library_fixes_a_typo_and_fills_the_author():
    r = check({"title": "the charsima myth"}, no_pages, ol("charisma.json"))
    assert (r["title"], r["author"]) == ("The Charisma Myth", "Olivia Fox Cabane")
    assert {"field": "author", "from": "", "to": "Olivia Fox Cabane",
            "reason": "open-library"} in r["changes"]


def test_typed_values_win_and_skip_the_fetch():
    r = check({"url": GOODREADS_URL, "title": "Grit", "author": "Angela Duckworth"},
              no_pages, ol())
    assert (r["title"], r["source"]) == ("Grit", "typed")


@pytest.mark.parametrize("req, msg", [
    ({}, "give a link or a title"),
    ({"author": "x"}, "give a link or a title"),
    ({"url": "ftp://x"}, "link must start with http:// or https://"),
    ({"url": 5}, "link must be text"),
    ({"url": "https://x.org/" + "a" * 2048}, "link must be at most 2048 characters"),
    ({"url": "https://x.org/\n"}, "link contains control or invisible characters"),
    ({"title": "Bad​Title"}, "title contains control or invisible characters"),
])
def test_bad_requests(req, msg):
    with pytest.raises(BadRequest) as e:
        check(req, no_pages, ol())
    assert str(e.value) == msg


# -- AC1: links (fixtures) ---------------------------------------------------------

def test_goodreads_link():
    r = check({"url": GOODREADS_URL}, pages({GOODREADS_URL: "goodreads-charisma.html"}),
              ol("charisma.json"))
    assert (r["title"], r["author"], r["source"]) == (
        "The Charisma Myth", "Olivia Fox Cabane", "link")
    assert r["changes"][0]["reason"] == "link"
    assert r["changes"][0]["from"].startswith("The Charisma Myth: How Anyone")
    assert r["warnings"] == []


@pytest.mark.parametrize("name, title, author", [
    ("generic-jsonld-graph.html", "Deep Work", "Cal Newport"),
    ("generic-by-author.html", "Essentialism", "Greg McKeown"),
    ("generic-title-only.html", "The Power of Habit", ""),
])
def test_generic_links(name, title, author):
    url = "https://books.example.org/x"
    r = check({"url": url}, pages({url: name}), ol())
    assert (r["title"], r["author"], r["source"]) == (title, author, "link")


def test_amazon_product_page():
    r = check({"url": AMAZON_URL}, pages({AMAZON_URL: "amazon-product.html"}),
              ol("charisma.json"))
    assert (r["title"], r["author"]) == ("The Charisma Myth", "Olivia Fox Cabane")


def test_amazon_captcha_uses_the_slug_and_open_library():
    r = check({"url": AMAZON_URL}, pages({AMAZON_URL: "amazon-captcha.html"}),
              ol("charisma.json"))
    assert (r["title"], r["author"], r["source"]) == ("The Charisma Myth", "", "link")
    assert W_AMAZON_SLUG in r["warnings"]


def test_author_guessed_warning():
    url = "https://books.example.org/tiny"
    r = check({"url": url}, pages({url: "generic-og-book.html"}), ol())
    assert r["author"] == "Anne-Laure Le Cunff" and W_AUTHOR_GUESSED in r["warnings"]


def test_unreadable_page():
    url = "https://books.example.org/x"
    r = check({"url": url}, pages({url: FetchError("http-503")}), ol())
    assert (r["title"], r["author"], r["warnings"]) == ("", "", [W_LINK_FAILED])


def test_unreadable_page_with_a_typed_title_warns_once():
    url = "https://books.example.org/x"
    r = check({"url": url, "title": "Grit"}, pages({url: FetchError("timeout")}), ol())
    assert r["warnings"] == [W_LINK_FAILED, W_NOT_FOUND]


def test_page_without_an_author():
    url = "https://books.example.org/x"
    r = check({"url": url}, pages({url: "generic-title-only.html"}), ol())
    assert r["warnings"] == [resolver.W_NO_AUTHOR, W_NOT_FOUND]


def test_link_fills_only_the_missing_author():
    r = check({"url": GOODREADS_URL, "title": "The Charisma Myth"},
              pages({GOODREADS_URL: "goodreads-charisma.html"}), ol("charisma.json"))
    assert (r["title"], r["author"], r["source"]) == (
        "The Charisma Myth", "Olivia Fox Cabane", "typed")


# -- AC3: Open Library trouble -------------------------------------------------------

@pytest.mark.parametrize("fetch_json", [
    ol(raises=FetchError("timeout")),
    ol(raises=FetchError("http-503")),
    ol(raises=FetchError("limit")),
    ol("malformed.txt"),
])
def test_open_library_down_degrades_to_casing(fetch_json):
    r = check({"title": "the charisma myth", "author": "olivia fox cabane"}, no_pages, fetch_json)
    assert (r["title"], r["author"]) == ("The Charisma Myth", "Olivia Fox Cabane")
    assert r["warnings"] == [W_UNVERIFIED]


# -- AC4: the metadata address through the real fetcher ----------------------------------

def test_metadata_url_is_blocked_without_connecting():
    def connect(*a, **k):
        raise AssertionError("must not connect")

    def fetch_page(url):
        return safefetch.fetch(url, accept=("text/html",), max_bytes=1000, timeout=1,
                               truncate_ok=True, connect=connect)

    r = check({"url": "http://169.254.169.254/latest/meta-data/"}, fetch_page, ol())
    assert (r["title"], r["author"], r["warnings"]) == ("", "", [W_LINK_BLOCKED])


# -- AC5: prompt injection -----------------------------------------------------------------

def test_injection_page_only_changes_title_and_author(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-canary-123")
    books = tmp_path / "books.yaml"
    shutil.copy(BOOKS_FILE, books)
    before = books.read_bytes()
    queue = tmp_path / "queue"
    queue.mkdir()

    r = check({"url": HOSTILE_URL}, pages({HOSTILE_URL: "injection-page.html"}), ol())
    assert set(r) == KEYS
    for field, max_len in (("title", jobs.TITLE_MAX), ("author", jobs.AUTHOR_MAX)):
        v = r[field]
        assert v == "" or jobs.validate_field(field, v, max_len) is None
    blob = json.dumps(r, ensure_ascii=False)
    for bad in ("‮", "​", "\n", "sk-canary", "A" * 100, '"state"', '"slug"',
                '"queue"', "ANTHROPIC"):
        assert bad not in blob, bad
    assert r["warnings"]
    assert validate_resolver_result({"ok": True, **r}) is not None
    assert "sk-canary" not in capsys.readouterr().out
    assert books.read_bytes() == before
    assert list(queue.iterdir()) == []


# -- HTTP server ----------------------------------------------------------------------------

@pytest.fixture
def server_factory():
    made = []

    def make(**kw):
        kw.setdefault("fetch_page", no_pages)
        kw.setdefault("fetch_json", ol())
        srv = make_server("127.0.0.1", 0, lambda: {"127.0.0.1"}, **kw)
        t = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.02},
                             daemon=True)
        t.start()
        srv.base = f"http://127.0.0.1:{srv.server_address[1]}"
        made.append(srv)
        return srv

    yield make
    for s in made:
        s.shutdown()
        s.server_close()


def post(srv, path, body=None, raw=None, headers=None):
    data = raw if raw is not None else json.dumps(body).encode()
    hdrs = {"Content-Type": "application/json"}
    hdrs.update(headers or {})
    req = urllib.request.Request(srv.base + path, data=data, method="POST", headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_server_resolve(server_factory, capsys):
    srv = server_factory(fetch_json=ol("charisma.json"))
    code, body = post(srv, "/resolve", {"title": "the charisma myth", "author": "olivia fox cabane"})
    assert code == 200 and body["ok"] is True and body["title"] == "The Charisma Myth"
    assert set(body) == KEYS | {"ok"}
    out = capsys.readouterr().out
    assert "[resolver] POST /resolve 200 ol:matched" in out
    assert "charisma" not in out.lower()  # no titles in logs


def test_server_errors(server_factory):
    srv = server_factory()
    assert post(srv, "/nope", {})[0] == 404
    assert post(srv, "/resolve", {"title": "x" * 5000})[0] == 413
    assert post(srv, "/resolve", raw=b"title=x",
                headers={"Content-Type": "text/plain"})[0] == 415
    assert post(srv, "/resolve", {"title": "Grit", "extra": 1}) == (
        400, {"ok": False, "error": "unexpected fields"})
    assert post(srv, "/resolve", {}) == (400, {"ok": False, "error": "give a link or a title"})
    assert post(srv, "/resolve", raw=b"[1]")[0] == 400
    with urllib.request.urlopen(srv.base + "/healthz", timeout=5) as r:
        assert r.read() == b"ok\n"


def test_server_requires_content_length(server_factory):
    srv = server_factory()
    with socket.create_connection(srv.server_address, timeout=5) as s:
        s.sendall(b"POST /resolve HTTP/1.1\r\nHost: x\r\nContent-Type: application/json\r\n"
                  b"Connection: close\r\n\r\n")
        assert b" 411 " in s.recv(4096).split(b"\r\n")[0] + b" "


def test_verify_request(server_factory):
    srv = server_factory()
    assert srv.verify_request(None, ("10.9.9.9", 1)) is False
    assert srv.verify_request(None, ("127.0.0.1", 1)) is True


def test_dropped_peer_gets_no_response(capsys):
    srv = make_server("127.0.0.1", 0, lambda: {"10.0.0.99"}, fetch_page=no_pages,
                      fetch_json=ol())
    t = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
    t.start()
    try:
        with socket.create_connection(srv.server_address, timeout=5) as s:
            s.sendall(b"GET /healthz HTTP/1.0\r\n\r\n")
            assert s.recv(4096) == b""
        assert "dropped connection from 127.0.0.1" in capsys.readouterr().out
    finally:
        srv.shutdown()
        srv.server_close()


def test_concurrency_limit(server_factory):
    entered, release = threading.Event(), threading.Event()

    def slow_page(url):
        entered.set()
        release.wait(5)
        raise FetchError("timeout")

    srv = server_factory(fetch_page=slow_page, concurrency=1)
    first = {}
    t = threading.Thread(target=lambda: first.update(
        r=post(srv, "/resolve", {"url": "https://books.example.org/x"})))
    t.start()
    assert entered.wait(5)
    assert post(srv, "/resolve", {"title": "Grit"}) == (429, {"ok": False, "error": "busy"})
    release.set()
    t.join(5)
    assert first["r"][0] == 200


def test_daily_fetch_cap(server_factory):
    srv = server_factory(fetch_json=ol("charisma.json"), daily_fetches=1)
    _, a = post(srv, "/resolve", {"title": "The Charisma Myth", "author": "Olivia Fox Cabane"})
    assert a["warnings"] == []
    _, b = post(srv, "/resolve", {"title": "Deep Work", "author": "Cal Newport"})
    assert b["warnings"] == [W_UNVERIFIED]


def test_fetch_budget_rolls_over():
    days = iter(["d1", "d1", "d1", "d2"])
    b = resolver.FetchBudget(1, today=lambda: next(days))
    assert b() is True and b() is False and b() is True


def test_healthcheck(server_factory, monkeypatch):
    srv = server_factory()
    monkeypatch.setenv("GTTP_RESOLVER_BIND", "127.0.0.1")
    monkeypatch.setenv("GTTP_RESOLVER_PORT", str(srv.server_address[1]))
    assert resolver.main(["healthcheck"]) == 0
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    monkeypatch.setenv("GTTP_RESOLVER_PORT", str(port))
    assert resolver.main(["healthcheck"]) == 1


def test_peer_list():
    calls = []

    def lookup(name):
        calls.append(name)
        return {"172.30.0.5"}

    p = resolver.PeerList("172.30.0.2", ["gttp-submit-internal"], lookup=lookup)
    assert p() == {"172.30.0.2", "172.30.0.5"}
    p()
    assert calls == ["gttp-submit-internal"]  # cached
    assert "127.0.0.1" in resolver.PeerList("127.0.0.1", [], lookup=lookup)()


def test_resolver_and_submit_import_nothing_heavy():
    code = ("import gttp.resolver, gttp.submit, sys; "
            "print([m for m in ('anthropic','requests','playwright','gttp.claude') "
            "if m in sys.modules])")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                         check=True).stdout.strip()
    assert out == "[]"
