"""Cover fetching, placeholder rendering, and the `gttp covers` backfill.

All network calls are mocked — these tests never hit Open Library.
"""

import pytest
import requests

from gttp import cli, covers


class _FakeResponse:
    def __init__(self, status_code=200, json_data=None, content=b""):
        self.status_code = status_code
        self._json = json_data
        self.content = content

    def json(self):
        if self._json is None:
            raise ValueError("no json")
        return self._json


def test_fetch_cover_success(tmp_path, monkeypatch):
    calls = []

    def fake_get(url, **kwargs):
        calls.append((url, kwargs))
        if "search.json" in url:
            return _FakeResponse(json_data={"docs": [{"cover_i": 12345}]})
        return _FakeResponse(content=b"\xff\xd8" + b"x" * 2000)

    monkeypatch.setattr(covers.requests, "get", fake_get)
    path = covers.fetch_cover("Atomic Habits", "James Clear", "atomic-habits", tmp_path)

    assert path == tmp_path / "atomic-habits.jpg"
    assert path.exists()
    cover_call = calls[1]
    assert "12345-L.jpg" in cover_call[0]
    assert cover_call[1]["params"]["default"] == "false"


def test_fetch_cover_no_match(tmp_path, monkeypatch):
    calls = []

    def fake_get(url, **kwargs):
        calls.append(url)
        return _FakeResponse(json_data={"docs": []})

    monkeypatch.setattr(covers.requests, "get", fake_get)
    path = covers.fetch_cover("Nope", None, "nope", tmp_path)

    assert path is None
    assert not (tmp_path / "nope.jpg").exists()
    assert len(calls) == 1  # no second (image) request


def test_fetch_cover_network_error(tmp_path, monkeypatch):
    def fake_get(url, **kwargs):
        raise requests.RequestException("boom")

    monkeypatch.setattr(covers.requests, "get", fake_get)
    assert covers.fetch_cover("X", None, "x", tmp_path) is None


def test_fetch_cover_rejects_tiny_body(tmp_path, monkeypatch):
    def fake_get(url, **kwargs):
        if "search.json" in url:
            return _FakeResponse(json_data={"docs": [{"cover_i": 9}]})
        return _FakeResponse(content=b"tiny")

    monkeypatch.setattr(covers.requests, "get", fake_get)
    path = covers.fetch_cover("X", None, "x", tmp_path)

    assert path is None
    assert not (tmp_path / "x.jpg").exists()


def test_cover_file_lookup(tmp_path):
    (tmp_path / "atomic-habits.jpg").write_bytes(b"data")
    assert covers.cover_file("atomic-habits", tmp_path) == tmp_path / "atomic-habits.jpg"
    assert covers.cover_file("missing", tmp_path) is None


def test_placeholder_svg_deterministic():
    a1 = covers.placeholder_svg("Atomic Habits", "cover")
    a2 = covers.placeholder_svg("Atomic Habits", "cover")
    other = covers.placeholder_svg("Deep Work", "cover")

    assert a1 == a2
    # Different titles derive different fill hues.
    assert a1 != other
    assert 'aria-label="Cover of Atomic Habits"' in a1
    assert ">A</text>" in a1


def test_placeholder_svg_escapes():
    svg = covers.placeholder_svg("<b>&", "cover")
    assert "<b>" not in svg.replace('viewBox', '')  # title not injected raw
    assert "&amp;" in svg or "&lt;" in svg


def test_covers_cli_backfill(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(covers, "COVERS_DIR", tmp_path)
    monkeypatch.setattr(cli, "cover_file", lambda slug: covers.cover_file(slug, tmp_path))
    monkeypatch.setattr(cli.time, "sleep", lambda *a: None)

    catalog = cli.load_catalog()
    # Pretend the first catalog book already has a cover.
    existing_slug = catalog[0].slug
    (tmp_path / f"{existing_slug}.jpg").write_bytes(b"data")

    attempted = []

    def fake_fetch(title, author, slug, *a, **k):
        attempted.append(slug)
        return None

    monkeypatch.setattr(cli, "fetch_cover", fake_fetch)

    assert cli.main(["covers"]) == 0
    assert existing_slug not in attempted  # skipped (exists)
    assert len(attempted) == len(catalog) - 1

    # --force re-attempts everything.
    attempted.clear()
    assert cli.main(["covers", "--force"]) == 0
    assert len(attempted) == len(catalog)
