"""End-to-end offline test: fixtures -> filter -> heuristic rank/synth -> markdown."""

from gttp import cache
from gttp.config import Book, load_catalog, slugify
from gttp.models import BookPage, RedditThread
from gttp.pipeline import build_book, match_books
from gttp.publish import render_markdown
from gttp.ranking import filter_threads
from gttp.reddit_client import FixtureRedditClient


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


def test_build_book_isolates_errors():
    class ExplodingClient:
        def search(self, book):
            raise RuntimeError("reddit is down")

    page = build_book(_book(), ExplodingClient())  # must not raise
    assert page.generated_by == "error"
    assert "reddit is down" in page.honest_take
