"""End-to-end offline test: fixtures -> filter -> heuristic rank/synth -> markdown."""

from gttp.config import Book, load_catalog, slugify
from gttp.models import RedditThread
from gttp.pipeline import build_book
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
