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
