"""End-to-end offline test: fixtures -> filter -> heuristic rank/synth -> markdown."""

import pytest

from gttp import cache, ranking, synthesize
from gttp.config import Book, load_catalog, slugify
from gttp.models import BookPage, RedditThread
from gttp.pipeline import build_book, match_books
from gttp.publish import render_markdown, write_site
from gttp.ranking import filter_threads
from gttp.reddit_client import FixtureRedditClient


@pytest.fixture(autouse=True)
def _force_heuristic(monkeypatch):
    # This suite exercises the offline/heuristic path; keep it independent of
    # (and off the network from) whatever ANTHROPIC_API_KEY may be in the env.
    monkeypatch.setattr(ranking, "anthropic_key", lambda: None)
    monkeypatch.setattr(synthesize, "anthropic_key", lambda: None)


def _book(title="Atomic Habits"):
    return Book(
        title=title,
        author="James Clear",
        subreddits=["BettermentBookClub"],
        queries=["{title} summary"],
        min_score=10,
        min_chars=100,
    )


def test_fixture_client_loads_threads():
    threads = FixtureRedditClient().search(_book())
    assert threads
    assert all(isinstance(t, RedditThread) for t in threads)


def test_filter_drops_low_score():
    book = _book()
    threads = FixtureRedditClient().search(book)
    low = RedditThread(
        id="x", title="t", subreddit="s", author="a", score=1,
        permalink="/p", selftext="short but has enough characters here " * 5,
    )
    kept = filter_threads(threads + [low], book)
    assert all(t.score >= book.min_score for t in kept)


def test_build_book_offline_produces_page():
    book = _book()
    page = build_book(book, FixtureRedditClient())
    assert page.title == "Atomic Habits"
    assert page.bullets
    assert page.generated_by == "heuristic"
    assert page.sources


def test_render_markdown_has_sections():
    page = build_book(_book(), FixtureRedditClient())
    md = render_markdown(page)
    assert "# Atomic Habits" in md
    assert "## Sources" in md


def test_slugify():
    assert slugify("The 7 Habits of Highly Effective People") == \
        "the-7-habits-of-highly-effective-people"


def test_catalog_loads():
    books = load_catalog()
    assert any(b.title == "Atomic Habits" for b in books)
    # defaults should be merged in
    assert all(b.subreddits for b in books)


def test_match_books():
    books = load_catalog()
    assert len(match_books(books, None)) == len(books)  # None -> all
    only = match_books(books, "deep work")  # case-insensitive substring
    assert [b.title for b in only] == ["Deep Work"]
    assert match_books(books, "nonexistent title") == []


def test_thread_and_page_cache_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(cache, "THREADS_DIR", tmp_path / "threads")
    monkeypatch.setattr(cache, "PAGES_DIR", tmp_path / "pages")

    threads = FixtureRedditClient().search(_book())
    cache.save_threads("atomic-habits", threads)
    loaded = cache.load_cached_threads("atomic-habits")
    assert [t.id for t in loaded] == [t.id for t in threads]
    assert cache.load_cached_threads("missing") is None

    page = build_book(_book(), FixtureRedditClient())
    cache.save_page("atomic-habits", page)
    restored = cache.load_cached_page("atomic-habits")
    assert isinstance(restored, BookPage)
    assert restored.core_idea == page.core_idea
    assert restored.bullets == page.bullets


def test_build_book_isolates_errors(tmp_path, monkeypatch):
    # Isolate the page cache so the error path has no cached page to fall back
    # to — we're asserting the error-placeholder behavior specifically.
    monkeypatch.setattr(cache, "PAGES_DIR", tmp_path / "pages")

    class ExplodingClient:
        def search(self, book):
            raise RuntimeError("reddit is down")

    page = build_book(_book(), ExplodingClient())  # must not raise
    assert page.generated_by == "error"
    assert "reddit is down" in page.honest_take


def test_write_site_html_features(tmp_path):
    summary = BookPage(
        title="Atomic Habits",
        author="James Clear",
        core_idea="Small habits compound into big results.",
        bullets=["Make it obvious", "Make it easy"],
        honest_take="Solid.",
        quotes=[],
        sources=[{"title": "t", "url": "https://x", "subreddit": "getdisciplined", "score": 42}],
    )
    empty = BookPage(
        title="Meditations",
        author="Marcus Aurelius",
        core_idea="No qualifying Reddit summaries found yet.",
        bullets=[],
        honest_take="",
        quotes=[],
        sources=[],
    )
    covers_dir = tmp_path / "src-covers"
    covers_dir.mkdir()
    (covers_dir / "atomic-habits.jpg").write_bytes(b"\xff\xd8" + b"x" * 4000)
    write_site([empty, summary], tmp_path, covers_dir=covers_dir)

    index = (tmp_path / "index.html").read_text()
    # Search box + per-card searchable data.
    assert 'id="q"' in index
    assert "data-search" in index
    # The no-summary book is dimmed and sorts after the summary book.
    assert 'class="card empty"' in index
    assert index.index("Atomic Habits") < index.index("Meditations")
    # Subreddit tag derives from sources.
    assert "r/getdisciplined" in index

    # Cover with a stored file renders an <img>; the one without gets an SVG.
    assert "covers/atomic-habits.jpg" in index
    assert 'loading="lazy"' in index
    meditations_card = index[index.index("Meditations") - 200 : index.index("Meditations") + 200]
    assert "<svg" in meditations_card
    # The stored cover was copied into the generated site.
    assert (tmp_path / "covers" / "atomic-habits.jpg").exists()

    book = (tmp_path / "books" / "atomic-habits.html").read_text()
    assert 'id="idea-1"' in book  # per-idea anchor
    assert 'class="toc"' in book  # jump list
    assert 'href="#idea-1"' in book
    assert "site-header" in book and "site-footer" in book
    # Detail page references the cover one directory up.
    assert "../covers/atomic-habits.jpg" in book

    meditations = (tmp_path / "books" / "meditations.html").read_text()
    assert "<svg" in meditations  # placeholder on the coverless detail page

    # The Markdown output is byte-identical to render_markdown.
    md = (tmp_path / "books" / "atomic-habits.md").read_text()
    assert md == render_markdown(summary)
