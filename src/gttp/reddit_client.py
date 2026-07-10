"""The swappable Reddit fetch layer.

Two implementations behind one interface so the rest of the pipeline never
knows or cares where threads come from:

- FixtureRedditClient reads bundled JSON from fixtures/ — zero network, used by
  `--offline` and by tests.
- HttpRedditClient hits Reddit's OAuth API for real search.

If gttp ever needs a different source (a Reddit data agreement, a cache, a
different platform), it slots in here without touching ranking/synthesis.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Protocol

import requests

from .config import FIXTURES_DIR, Book, slugify
from .models import RedditThread


class RedditClient(Protocol):
    def search(self, book: Book) -> list[RedditThread]:
        """Return candidate threads for a book (unfiltered, unranked)."""
        ...


class FixtureRedditClient:
    """Loads threads from fixtures/<slug>.json. Missing fixtures yield []."""

    def __init__(self, fixtures_dir: Path = FIXTURES_DIR):
        self.fixtures_dir = fixtures_dir

    def search(self, book: Book) -> list[RedditThread]:
        path = self.fixtures_dir / f"{slugify(book.title)}.json"
        if not path.exists():
            return []
        raw = json.loads(path.read_text())
        return [_thread_from_dict(d) for d in raw.get("threads", [])]


class HttpRedditClient:
    """Live Reddit search via the OAuth client-credentials flow.

    Read-only and low-volume: this is batch curation, not real-time. Results are
    deduplicated by post id across all (subreddit, query) combinations.
    """

    def __init__(self, client_id: str, client_secret: str, user_agent: str):
        self.user_agent = user_agent
        self._token = self._authenticate(client_id, client_secret, user_agent)

    def _authenticate(self, client_id: str, client_secret: str, ua: str) -> str:
        resp = requests.post(
            "https://www.reddit.com/api/v1/access_token",
            auth=(client_id, client_secret),
            data={"grant_type": "client_credentials"},
            headers={"User-Agent": ua},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()["access_token"]

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self._token}", "User-Agent": self.user_agent}

    def search(self, book: Book) -> list[RedditThread]:
        found: dict[str, RedditThread] = {}
        for subreddit in book.subreddits:
            for query in book.search_queries():
                for thread in self._search_one(subreddit, query):
                    found.setdefault(thread.id, thread)
                    if len(found) >= book.max_threads_per_book * 2:
                        return list(found.values())
                time.sleep(0.5)  # stay polite to the API
        return list(found.values())

    def _search_one(self, subreddit: str, query: str) -> list[RedditThread]:
        resp = requests.get(
            f"https://oauth.reddit.com/r/{subreddit}/search",
            headers=self._headers(),
            params={
                "q": query,
                "restrict_sr": "true",
                "sort": "top",
                "t": "all",
                "limit": 15,
            },
            timeout=30,
        )
        if resp.status_code != 200:
            return []
        threads = []
        for child in resp.json().get("data", {}).get("children", []):
            post = child.get("data", {})
            if post.get("removed_by_category") or not post.get("selftext"):
                continue
            thread = RedditThread(
                id=post.get("id", ""),
                title=post.get("title", ""),
                subreddit=post.get("subreddit", subreddit),
                author=post.get("author", "[unknown]"),
                score=int(post.get("score", 0)),
                permalink=post.get("permalink", ""),
                selftext=post.get("selftext", ""),
                top_comments=self._top_comments(post.get("permalink", "")),
            )
            threads.append(thread)
        return threads

    def _top_comments(self, permalink: str, limit: int = 5) -> list[str]:
        if not permalink:
            return []
        resp = requests.get(
            f"https://oauth.reddit.com{permalink}",
            headers=self._headers(),
            params={"sort": "top", "limit": limit, "depth": 1},
            timeout=30,
        )
        if resp.status_code != 200 or len(resp.json()) < 2:
            return []
        comments = []
        for child in resp.json()[1].get("data", {}).get("children", []):
            body = child.get("data", {}).get("body")
            if body and body not in ("[deleted]", "[removed]"):
                comments.append(body)
            if len(comments) >= limit:
                break
        return comments


def _thread_from_dict(d: dict) -> RedditThread:
    return RedditThread(
        id=d["id"],
        title=d["title"],
        subreddit=d["subreddit"],
        author=d.get("author", "[unknown]"),
        score=int(d.get("score", 0)),
        permalink=d["permalink"],
        selftext=d.get("selftext", ""),
        top_comments=list(d.get("top_comments", [])),
    )
