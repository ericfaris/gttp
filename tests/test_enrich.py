"""The "get to the point" layer: enrichment never touches the summary itself,
and the page leads with the verdict / try-today while keeping everything."""

import json
from dataclasses import asdict

import pytest

from gttp import cache, enrich, pipeline, synthesize
from gttp.config import Book
from gttp.models import BookPage
from gttp.publish import render_markdown, write_site

_CLAUDE_OUT = {
    "verdict": "Read this page, skip the book.",
    "try_today": "Move your phone charger out of the bedroom tonight.",
    "for_who": "People who keep restarting the same habit.",
    "skip_if": "You already track your habits.",
    "take_points": ["Four ideas, stretched.", "  ", "Identity framing is the fresh bit."],
}


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    # Never hit the real committed cache or the real API.
    monkeypatch.setattr(cache, "THREADS_DIR", tmp_path / "threads")
    monkeypatch.setattr(cache, "PAGES_DIR", tmp_path / "pages")
    monkeypatch.setattr(enrich, "anthropic_key", lambda: "test-key")
    monkeypatch.setattr(enrich, "_enrich_with_claude", lambda page: dict(_CLAUDE_OUT))


def _page(**kw):
    base = dict(
        title="Atomic Habits",
        author="James Clear",
        core_idea="Systems beat goals.",
        bullets=["Make it obvious", "Make it easy"],
        honest_take="It's four ideas stretched into a book.",
        quotes=[{"text": "Future You is unreliable.", "author": "u1", "url": "https://q"}],
        sources=[{"title": "t", "url": "https://x", "subreddit": "getdisciplined", "score": 42}],
        generated_by="claude",
        generated_at="2026-07-21",
    )
    base.update(kw)
    return BookPage(**base)


def _book(title="Atomic Habits"):
    return Book(title=title, author="James Clear")


def test_enrich_only_adds_fields():
    page = _page()
    out = enrich.enrich(page)
    assert out.verdict == _CLAUDE_OUT["verdict"]
    assert out.try_today.startswith("Move")
    assert out.take_points == ["Four ideas, stretched.", "Identity framing is the fresh bit."]
    assert out.enriched_at
    # The summary itself is byte-for-byte unchanged, and so is finality.
    for f in ("title", "author", "core_idea", "bullets", "honest_take",
              "quotes", "sources", "generated_by", "generated_at"):
        assert getattr(out, f) == getattr(page, f)
    assert cache.is_final(out)
    assert not enrich.is_enriched(page)  # original not mutated
    assert enrich.is_enriched(out)


def test_enrich_requires_key(monkeypatch):
    monkeypatch.setattr(enrich, "anthropic_key", lambda: None)
    with pytest.raises(RuntimeError):
        enrich.enrich(_page())


def test_can_enrich_only_real_summaries():
    assert enrich.can_enrich(_page())
    assert not enrich.can_enrich(_page(generated_by="heuristic"))
    assert not enrich.can_enrich(_page(generated_by="error"))
    assert not enrich.can_enrich(_page(bullets=[]))


def test_old_cached_page_without_new_fields_loads(tmp_path):
    d = asdict(_page())
    for f in ("verdict", "try_today", "for_who", "skip_if", "take_points", "enriched_at"):
        d.pop(f)
    cache.PAGES_DIR.mkdir(parents=True)
    (cache.PAGES_DIR / "atomic-habits.json").write_text(json.dumps(d))
    page = cache.load_cached_page("atomic-habits")
    assert page.verdict == "" and page.take_points == []
    assert cache.is_final(page)


def test_enrich_all_targets_and_skips(monkeypatch):
    written = []
    monkeypatch.setattr(pipeline, "write_site", lambda pages: written.append(pages) or "index")
    books = [_book("Atomic Habits"), _book("Deep Work"), _book("Grit"), _book("Missing")]
    cache.save_page("atomic-habits", _page())
    cache.save_page("deep-work", _page(title="Deep Work", verdict="done", try_today="done"))
    cache.save_page("grit", _page(title="Grit", generated_by="heuristic"))

    done, failed = pipeline.enrich_all(books)
    assert (done, failed) == (1, 0)
    assert cache.load_cached_page("atomic-habits").verdict == _CLAUDE_OUT["verdict"]
    assert cache.load_cached_page("deep-work").verdict == "done"  # already enriched
    assert cache.load_cached_page("grit").verdict == ""  # heuristic: never enriched
    # Site re-rendered with every cached page, uncached book left out.
    assert [p.title for p in written[0]] == ["Atomic Habits", "Deep Work", "Grit"]

    # --force redoes an enriched page; --only limits the scope.
    done, _ = pipeline.enrich_all(books, only="Deep Work", force=True)
    assert done == 1
    assert cache.load_cached_page("deep-work").verdict == _CLAUDE_OUT["verdict"]


def test_enrich_all_failure_keeps_page(monkeypatch):
    monkeypatch.setattr(pipeline, "write_site", lambda pages: "index")

    def boom(page):
        raise RuntimeError("out of credit")

    monkeypatch.setattr(enrich, "_enrich_with_claude", boom)
    cache.save_page("atomic-habits", _page())
    done, failed = pipeline.enrich_all([_book()])
    assert (done, failed) == (0, 1)
    assert cache.load_cached_page("atomic-habits") == _page()


def test_build_book_enriches_fresh_claude_page(monkeypatch):
    class Client:
        def search(self, book):
            return []

    monkeypatch.setattr(pipeline, "rank_threads", lambda kept, book: ([], True))
    monkeypatch.setattr(pipeline, "synthesize", lambda book, ranked, used: _page())
    assert pipeline.build_book(_book(), Client()).verdict == _CLAUDE_OUT["verdict"]

    # An enrich failure never fails the build — the page just isn't enriched.
    monkeypatch.setattr(enrich, "_enrich_with_claude", lambda p: 1 / 0)
    page = pipeline.build_book(_book(), Client())
    assert page.generated_by == "claude" and page.verdict == ""


def test_build_book_never_enriches_heuristic(monkeypatch):
    called = []
    monkeypatch.setattr(enrich, "_enrich_with_claude", lambda p: called.append(p))
    monkeypatch.setattr(synthesize, "anthropic_key", lambda: None)
    from gttp.reddit_client import FixtureRedditClient

    book = Book(title="Atomic Habits", author="James Clear", min_chars=100)
    monkeypatch.setattr(pipeline, "rank_threads", lambda kept, b: ([], False))
    pipeline.build_book(book, FixtureRedditClient())
    assert called == []


def test_enriched_page_renders_point_first(tmp_path):
    page = enrich.enrich(_page())
    covers_dir = tmp_path / "covers-src"
    covers_dir.mkdir()
    write_site([page], tmp_path / "site", covers_dir=covers_dir)
    html = (tmp_path / "site" / "books" / "atomic-habits.html").read_text()

    # Order: verdict and core idea, then try-today, then fit, then the ideas.
    order = [html.index(m) for m in (
        "class='verdict'", "class='core'", "class='try'", "class='fit'",
        "The 5-bullet version", "class='take'",
    )]
    assert order == sorted(order)
    assert "Move your phone charger" in html
    assert "<dt>Skip it if</dt>" in html
    # Nothing is dropped: full take, quotes and sources are folded in <details>.
    assert "<summary>Read the full take</summary>" in html
    assert "It&#x27;s four ideas stretched into a book." in html
    assert "<summary>Worth quoting (1)</summary>" in html
    assert "Future You is unreliable." in html
    assert "<summary>Sources (1 Reddit threads)</summary>" in html
    assert "https://x" in html


def test_enriched_markdown_has_everything():
    md = render_markdown(enrich.enrich(_page()))
    assert "**Verdict:** Read this page, skip the book." in md
    assert "## Try this today" in md
    assert "- **Skip it if:** You already track your habits." in md
    assert "- Four ideas, stretched." in md
    assert "It's four ideas stretched into a book." in md  # full take kept
    assert md.index("**Verdict:**") < md.index("## The 5-bullet version")


def test_pick_today_rotates_daily():
    from datetime import date, timedelta

    from gttp.publish import pick_today

    ideas = [{"slug": s} for s in "abc"]
    d = date(2026, 10, 25)
    picks = [pick_today(ideas, d + timedelta(days=i)) for i in range(6)]
    assert sorted(set(picks)) == [0, 1, 2]  # every idea gets a day
    assert picks[0] != picks[1]  # changes day to day
    assert picks[0] == picks[3]  # and cycles
    assert pick_today([], d) == -1


def test_index_features_todays_idea(tmp_path):
    enriched = enrich.enrich(_page())
    plain = _page(title="Deep Work", author="Cal Newport")  # not enriched
    covers_dir = tmp_path / "covers-src"
    covers_dir.mkdir()
    write_site([plain, enriched], tmp_path / "site", covers_dir=covers_dir)
    index = (tmp_path / "site" / "index.html").read_text()

    assert 'id="today"' in index and 'id="shuffle"' in index
    # Only enriched books are candidates; the JSON block can't be broken out of.
    block = index[index.index('id="ideas">') :]
    block = block[: block.index("</script>")]
    data = json.loads(block[len('id="ideas">') :])
    assert [d["slug"] for d in data] == ["atomic-habits"]
    assert data[0]["try"].startswith("Move")
    assert "Read this page, skip the book." in index
    assert 'data-slug="deep-work"' in index  # cards carry the tried-badge hook


def test_index_without_enriched_books_has_no_hero(tmp_path):
    covers_dir = tmp_path / "covers-src"
    covers_dir.mkdir()
    write_site([_page()], tmp_path / "site", covers_dir=covers_dir)
    index = (tmp_path / "site" / "index.html").read_text()
    assert 'id="today"' not in index
    assert "class='books'" in index


def test_ideas_json_escapes_closing_tags(tmp_path):
    page = enrich.enrich(_page())
    page.verdict = "Ends here </script><b>x</b>"
    covers_dir = tmp_path / "covers-src"
    covers_dir.mkdir()
    write_site([page], tmp_path / "site", covers_dir=covers_dir)
    index = (tmp_path / "site" / "index.html").read_text()
    block = index[index.index('id="ideas">') :]
    assert "</script><b>" not in block[: block.index("</script>") + 9]


def test_book_page_has_tried_button(tmp_path):
    covers_dir = tmp_path / "covers-src"
    covers_dir.mkdir()
    write_site([enrich.enrich(_page()), _page(title="Grit")], tmp_path / "site", covers_dir=covers_dir)
    book = (tmp_path / "site" / "books" / "atomic-habits.html").read_text()
    assert "class='tried'" in book and "data-slug='atomic-habits'" in book
    assert "gttp-tried-" in book  # the script that remembers it
    plain = (tmp_path / "site" / "books" / "grit.html").read_text()
    assert "class='tried'" not in plain and "gttp-tried-" not in plain


def test_site_ships_logo_and_fonts(tmp_path):
    covers_dir = tmp_path / "covers-src"
    covers_dir.mkdir()
    write_site([_page()], tmp_path / "site", covers_dir=covers_dir)
    site = tmp_path / "site"
    for f in ("logo.svg", "logo-dark.svg", "mark.svg", "fonts/young-serif.woff2",
              "fonts/instrument-sans.woff2", "fonts/caveat-600.woff2"):
        assert (site / f).is_file(), f
    css = (site / "style.css").read_text()
    for f in ("young-serif.woff2", "instrument-sans.woff2", "caveat-600.woff2"):
        assert f"/fonts/{f}" in css
    index = (site / "index.html").read_text()
    assert "logo.svg" in index and "logo-dark.svg" in index and "logo.png" not in index
