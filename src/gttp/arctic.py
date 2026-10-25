"""Reddit threads from the Arctic Shift archive (arctic-shift.photon-reddit.com).

Reddit blocks the lab's IP from its own data endpoints (403, "blocked by
network security", since at least 2026-10-25), so live search goes through
Arctic Shift instead: a free, public archive of Reddit posts and comments
with a keyword search API. Book discussions are evergreen, so the archive's
small lag doesn't matter.

Title search is "not supported with very active subreddits": r/books,
r/getdisciplined and r/productivity answer 422 "Timeout. Maybe slow down a
bit" within a second, every time (tested 2026-10-25, with and without date
ranges or smaller limits). Smaller subreddits sometimes time out once and
then work. So each search is retried once, and a subreddit that still refuses
is skipped for the rest of the run instead of costing every later search. The API asks for considerate use
(a couple of requests per second at most), and this is a low-volume batch job.
"""

from __future__ import annotations

import re
import time

import requests

from .config import Book
from .models import RedditThread
from .ranking import mentions_book

BASE_URL = "https://arctic-shift.photon-reddit.com/api"
_POST_FIELDS = "id,title,selftext,score,num_comments,author,subreddit"
_COMMENT_FIELDS = "id,body,score,parent_id,author"
_USER_AGENT = "gttp/1.0 (personal book-summary batch job)"


def _title_keywords(book: Book) -> list[str]:
    """Title-search terms: the title without a leading article and without
    punctuation, plus each author's surname (catches posts that name the
    author but shorten the title)."""
    title = re.sub(r"^(the|a|an)\s+", "", book.title.strip(), flags=re.I)
    title = re.sub(r"[^\w\s']", " ", title)
    terms = [" ".join(title.split())]
    for name in re.split(r" and |,|&", book.author or ""):
        if name.strip():
            terms.append(name.strip().split()[-1])
    return [t for t in dict.fromkeys(terms) if t]


def _top_level_comments(rows: list[dict], post_id: str, limit: int = 5) -> list[str]:
    """Highest-scored top-level comment bodies, skipping deleted/removed."""
    top = [
        r for r in rows
        if r.get("parent_id") == f"t3_{post_id}"
        and r.get("body") not in (None, "", "[deleted]", "[removed]")
    ]
    top.sort(key=lambda r: r.get("score") or 0, reverse=True)
    return [r["body"] for r in top[:limit]]


def _thread_from_archive(post: dict, comments: list[str]) -> RedditThread:
    sub = post.get("subreddit", "")
    pid = post.get("id", "")
    return RedditThread(
        id=pid,
        title=post.get("title", ""),
        subreddit=sub,
        author=post.get("author") or "[unknown]",
        score=int(post.get("score") or 0),
        permalink=f"/r/{sub}/comments/{pid}/",
        selftext=post.get("selftext") or "",
        top_comments=comments,
    )


class ArcticShiftClient:
    """RedditClient backed by the Arctic Shift archive API."""

    REQUEST_INTERVAL = 1.0  # seconds between requests
    RETRIES = 1  # per request, on timeout / rate limit / server error
    BACKOFF = 3.0  # seconds, doubled per retry

    def __init__(self, session: requests.Session | None = None, sleep=time.sleep):
        self.session = session or requests.Session()
        self.session.headers.setdefault("User-Agent", _USER_AGENT)
        self._sleep = sleep
        self.failed_searches = 0
        self.refused_subreddits: set[str] = set()  # remembered across books

    def _get(self, path: str, params: dict) -> list[dict] | None:
        """GET with retries; returns the `data` list, or None if it never
        succeeded."""
        delay = self.BACKOFF
        for _ in range(self.RETRIES + 1):
            self._sleep(self.REQUEST_INTERVAL)
            try:
                resp = self.session.get(f"{BASE_URL}{path}", params=params, timeout=60)
            except requests.RequestException:
                resp = None
            if resp is not None and resp.status_code == 200:
                data = resp.json().get("data")
                return data if isinstance(data, list) else []
            wait = delay
            if resp is not None and resp.status_code == 429:
                wait = max(wait, float(resp.headers.get("X-RateLimit-Reset", wait)))
            elif resp is not None and resp.status_code not in (422, 500, 502, 503, 504):
                return None  # a real client error: retrying won't help
            self._sleep(wait)
            delay *= 2
        return None

    def search(self, book: Book) -> list[RedditThread]:
        posts: dict[str, dict] = {}
        for sub in book.subreddits:
            for term in _title_keywords(book):
                if sub in self.refused_subreddits:
                    break
                rows = self._get("/posts/search", {
                    "subreddit": sub, "title": term, "limit": 100, "fields": _POST_FIELDS,
                })
                if rows is None:
                    self.failed_searches += 1
                    self.refused_subreddits.add(sub)
                    print(f"    arctic: r/{sub} refused title search; skipping it this run")
                    continue
                for post in rows:
                    if post.get("id") and post.get("selftext") not in (None, "", "[deleted]", "[removed]"):
                        posts.setdefault(post["id"], post)

        # Only posts that are actually about the book are worth their
        # comments; take the best-scored ones up to the per-book cap.
        relevant = [
            p for p in posts.values()
            if mentions_book(_thread_from_archive(p, []), book)
        ]
        relevant.sort(key=lambda p: p.get("score") or 0, reverse=True)
        threads = []
        for post in relevant[: book.max_threads_per_book]:
            rows = self._get("/comments/search", {
                "link_id": post["id"], "limit": 100, "fields": _COMMENT_FIELDS,
            }) or []
            threads.append(_thread_from_archive(post, _top_level_comments(rows, post["id"])))
        print(f"    arctic: {len(posts)} posts matched, {len(threads)} about the book")
        return threads
