"""Arctic Shift client: a fake HTTP session stands in for the API, so these
tests never touch the network."""

from types import SimpleNamespace

from gttp import arctic
from gttp.arctic import ArcticShiftClient, _title_keywords, _top_level_comments
from gttp.config import Book

BOOK = Book(title="The Mountain Is You", author="Brianna Wiest", subreddits=["selfimprovement", "books"])


def _resp(status, data=None, headers=None):
    return SimpleNamespace(status_code=status, json=lambda: {"data": data}, headers=headers or {})


class FakeSession:
    def __init__(self, handler):
        self.handler, self.calls, self.headers = handler, [], {}

    def get(self, url, params=None, timeout=None):
        self.calls.append((url.replace(arctic.BASE_URL, ""), dict(params)))
        return self.handler(url.replace(arctic.BASE_URL, ""), params)


def _client(handler):
    return ArcticShiftClient(session=FakeSession(handler), sleep=lambda s: None)


ON = {"id": "a1", "title": "The Mountain Is You changed me", "selftext": "Wiest says " + "x " * 300,
      "score": 254, "author": "u1", "subreddit": "selfimprovement"}
OFF = {"id": "b2", "title": "Climbing a mountain", "selftext": "x " * 300, "score": 900,
       "author": "u2", "subreddit": "selfimprovement"}
COMMENTS = [
    {"id": "c1", "body": "great book", "score": 5, "parent_id": "t3_a1"},
    {"id": "c2", "body": "[deleted]", "score": 99, "parent_id": "t3_a1"},
    {"id": "c3", "body": "a reply", "score": 50, "parent_id": "t1_c1"},
    {"id": "c4", "body": "best one", "score": 40, "parent_id": "t3_a1"},
]


def test_title_keywords_drop_article_and_add_surnames():
    assert _title_keywords(BOOK) == ["Mountain Is You", "Wiest"]
    book = Book(title="Why Has Nobody Told Me This Before?", author="Julie Smith")
    assert _title_keywords(book) == ["Why Has Nobody Told Me This Before", "Smith"]
    book = Book(title="Boundaries", author="Henry Cloud and John Townsend")
    assert _title_keywords(book) == ["Boundaries", "Cloud", "Townsend"]


def test_top_level_comments_ranked_and_cleaned():
    assert _top_level_comments(COMMENTS, "a1") == ["best one", "great book"]


def test_search_keeps_only_on_topic_posts_and_skips_their_comments():
    def handler(path, params):
        if path == "/posts/search":
            return _resp(200, [ON, OFF])
        return _resp(200, COMMENTS)

    client = _client(handler)
    threads = client.search(BOOK)
    assert [t.id for t in threads] == ["a1"]
    assert threads[0].top_comments == ["best one", "great book"]
    assert threads[0].url == "https://www.reddit.com/r/selfimprovement/comments/a1/"
    comment_calls = [p for path, p in client.session.calls if path == "/comments/search"]
    assert comment_calls == [{"link_id": "a1", "limit": 100, "fields": arctic._COMMENT_FIELDS}]
    # Every subreddit x keyword was searched.
    searches = {(p["subreddit"], p["title"]) for path, p in client.session.calls if path == "/posts/search"}
    assert searches == {(s, k) for s in BOOK.subreddits for k in _title_keywords(BOOK)}


def test_timeouts_are_retried_then_skipped():
    attempts = {"n": 0}

    def handler(path, params):
        if path == "/posts/search" and params["subreddit"] == "books":
            return _resp(422)  # always times out
        if path == "/posts/search":
            attempts["n"] += 1
            return _resp(422) if attempts["n"] == 1 else _resp(200, [ON])
        return _resp(200, [])

    client = _client(handler)
    threads = client.search(BOOK)
    assert [t.id for t in threads] == ["a1"]  # first timeout recovered on retry
    # r/books refused: tried once with a retry, then skipped for the 2nd keyword
    assert client.failed_searches == 1
    assert client.refused_subreddits == {"books"}
    books_calls = [p for path, p in client.session.calls if p.get("subreddit") == "books"]
    assert len(books_calls) == ArcticShiftClient.RETRIES + 1
    # ...and for later books in the same run.
    client.search(Book(title="Grit", author="Angela Duckworth", subreddits=["books"]))
    books_calls = [p for path, p in client.session.calls if p.get("subreddit") == "books"]
    assert len(books_calls) == ArcticShiftClient.RETRIES + 1


def test_client_errors_are_not_retried():
    client = _client(lambda path, params: _resp(400))
    assert client.search(BOOK) == []
    # one call per subreddit (no retry), then that subreddit is skipped
    assert len(client.session.calls) == len(BOOK.subreddits)


def test_pipeline_defaults_to_arctic(monkeypatch):
    from gttp import pipeline

    monkeypatch.delenv("GTTP_REDDIT_SOURCE", raising=False)
    assert isinstance(pipeline.make_reddit_client(False).inner, ArcticShiftClient)
    monkeypatch.setenv("GTTP_REDDIT_SOURCE", "browser")
    assert type(pipeline.make_reddit_client(False).inner).__name__ == "PlaywrightRedditClient"
