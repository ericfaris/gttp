"""Unit tests for the transport-agnostic JSON parsing helpers shared by the
live Reddit clients. No network or browser is touched."""

from gttp.models import RedditThread
from gttp.reddit_client import (
    _comments_from_json,
    _posts_from_search,
    _thread_from_post,
)


def _search_payload():
    return {
        "data": {
            "children": [
                {"data": {"id": "good", "title": "A summary", "selftext": "body",
                          "subreddit": "books", "author": "alice", "score": 42,
                          "permalink": "/r/books/comments/good/"}},
                # removed post — dropped
                {"data": {"id": "rm", "selftext": "body",
                          "removed_by_category": "moderator"}},
                # link-only post (no selftext) — dropped
                {"data": {"id": "link", "selftext": "", "title": "just a link"}},
            ]
        }
    }


def test_posts_from_search_filters_removed_and_linkonly():
    posts = _posts_from_search(_search_payload(), "books")
    assert [p["id"] for p in posts] == ["good"]


def test_posts_from_search_handles_empty():
    assert _posts_from_search({}, "books") == []


def test_thread_from_post_maps_fields():
    post = _posts_from_search(_search_payload(), "books")[0]
    thread = _thread_from_post(post, "books", ["c1", "c2"])
    assert isinstance(thread, RedditThread)
    assert thread.id == "good"
    assert thread.score == 42
    assert thread.subreddit == "books"
    assert thread.top_comments == ["c1", "c2"]


def test_thread_from_post_defaults_missing_fields():
    thread = _thread_from_post({}, "fallback", [])
    assert thread.author == "[unknown]"
    assert thread.score == 0
    assert thread.subreddit == "fallback"


def test_comments_from_json_filters_and_caps():
    listing = [
        {},  # element 0 is the post; ignored
        {"data": {"children": [
            {"data": {"body": "first"}},
            {"data": {"body": "[deleted]"}},
            {"data": {"body": "[removed]"}},
            {"data": {"body": None}},
            {"data": {"body": "second"}},
        ]}},
    ]
    assert _comments_from_json(listing) == ["first", "second"]
    assert _comments_from_json(listing, limit=1) == ["first"]


def test_comments_from_json_rejects_malformed():
    assert _comments_from_json(None) == []
    assert _comments_from_json([]) == []
    assert _comments_from_json([{"data": {}}]) == []  # too short (needs 2 elems)
    assert _comments_from_json("not a list") == []


class _FakeInner:
    def __init__(self, threads, degraded=False):
        self.threads, self.degraded = threads, degraded

    def search(self, book):
        return self.threads


def _caching_setup(monkeypatch, tmp_path):
    from gttp import cache
    from gttp.config import Book
    from gttp.models import RedditThread

    monkeypatch.setattr(cache, "THREADS_DIR", tmp_path)
    book = Book(title="Grit", author="Angela Duckworth")
    good = [RedditThread(id="g", title="Grit notes", subreddit="s", author="a",
                         score=50, permalink="/p", selftext="x")]
    cache.save_threads(book.slug, good)
    return cache, book, good


def test_refresh_never_overwrites_cache_with_a_failed_fetch(monkeypatch, tmp_path):
    from gttp.reddit_client import CachingRedditClient

    cache, book, good = _caching_setup(monkeypatch, tmp_path)
    for inner in (_FakeInner([], degraded=False), _FakeInner(["fixture"], degraded=True)):
        got = CachingRedditClient(inner, refresh=True).search(book)
        assert [t.id for t in got] == ["g"]
        assert [t.id for t in cache.load_cached_threads(book.slug)] == ["g"]


def test_refresh_saves_a_real_fetch(monkeypatch, tmp_path):
    from gttp.models import RedditThread
    from gttp.reddit_client import CachingRedditClient

    cache, book, _ = _caching_setup(monkeypatch, tmp_path)
    fresh = [RedditThread(id="n", title="Grit", subreddit="s", author="a",
                          score=9, permalink="/q", selftext="y")]
    got = CachingRedditClient(_FakeInner(fresh), refresh=True).search(book)
    # A refresh merges with the cache, so a partial fetch never loses threads.
    assert {t.id for t in got} == {"g", "n"}
    assert {t.id for t in cache.load_cached_threads(book.slug)} == {"g", "n"}


def test_browser_search_covers_every_subreddit_and_skips_off_topic_comments(monkeypatch):
    """Regression: the loop used to stop at 80 posts of any kind, so the
    first subreddits filled the quota and r/books was never searched."""
    from gttp import reddit_client
    from gttp.config import Book

    monkeypatch.setattr(reddit_client.time, "sleep", lambda s: None)
    book = Book(title="Deep Work", author="Cal Newport", subreddits=["a", "b", "books"],
                queries=['"{title}" summary'], max_threads_per_book=5)

    def post(pid, title):
        return {"data": {"id": pid, "title": title, "selftext": "body " * 100, "score": 10,
                         "permalink": f"/r/x/{pid}"}}

    class Fake(reddit_client._RedditSearchBase):
        def __init__(self):
            self.searched, self.comment_calls = [], []

        def _fetch_search(self, sub, query):
            self.searched.append(sub)
            off = [post(f"{sub}{i}", f"Discipline tips {i}") for i in range(100)]
            on = [post(f"{sub}-dw", "Deep Work notes")]
            return {"data": {"children": off + on}}

        def _fetch_comments(self, permalink):
            self.comment_calls.append(permalink)
            return []

    fake = Fake()
    threads = fake.search(book)
    assert fake.searched == ["a", "b", "books"]
    assert sorted(t.id for t in threads) == ["a-dw", "b-dw", "books-dw"]
    assert len(fake.comment_calls) == 3  # none spent on off-topic posts
