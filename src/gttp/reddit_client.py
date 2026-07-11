"""The swappable Reddit fetch layer.

Three implementations behind one interface so the rest of the pipeline never
knows or cares where threads come from:

- FixtureRedditClient reads bundled JSON from fixtures/ — zero network, used by
  `--offline` and by tests.
- PlaywrightRedditClient drives a real (headed) Chromium to reach Reddit's
  `.json` endpoints. Reddit blocks raw HTTP clients (requests/curl → 403 via
  bot fingerprinting) and its OAuth API now sits behind a manual "Responsible
  Builder Policy" application that hobby tools rarely clear. A real browser,
  warmed by first loading a normal Reddit page and then issuing same-origin
  `fetch()` calls, gets clean JSON. This is the default live source.
- JsonRedditClient keeps the raw-HTTP path for reference/tests. It shares all
  JSON parsing with the Playwright client but will 403 against live Reddit.

If gttp ever needs a different source (a Reddit data agreement, a cache, a
different platform), it slots in here without touching ranking/synthesis.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Protocol
from urllib.parse import urlencode

import requests

from .config import FIXTURES_DIR, ROOT, Book, slugify
from .models import RedditThread

# A realistic desktop-Chrome UA. The browser must not advertise itself as a
# bot, or Reddit's edge blocks the request before any page JS runs.
_CHROME_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# Chromium launch flags shared with the neighboring bookhunt project, which
# runs the same headed-under-Xvfb pattern in production:
#  - AutomationControlled off so navigator.webdriver isn't advertised
#  - --disable-gpu* avoids the green/black screen Chromium shows under a
#    virtual/WSLg display (GPU compositing bug)
#  - --disable-dev-shm-usage avoids /dev/shm crashes in containers
#  - --no-sandbox / --disable-setuid-sandbox: containers lack the kernel caps
#    the Chromium sandbox needs, so it hangs silently without these
_CHROMIUM_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--disable-gpu",
    "--disable-gpu-compositing",
    "--disable-software-rasterizer",
    "--disable-dev-shm-usage",
    "--no-sandbox",
    "--disable-setuid-sandbox",
    "--start-maximized",
]


class RedditClient(Protocol):
    def search(self, book: Book) -> list[RedditThread]:
        """Return candidate threads for a book (unfiltered, unranked)."""
        ...


# --- Shared JSON parsing (transport-agnostic, unit-tested without a browser) --


def _posts_from_search(data: dict, subreddit: str) -> list[dict]:
    """Pull the usable post objects out of a search listing, dropping removed
    posts and link-only posts (no selftext to summarize)."""
    posts = []
    for child in data.get("data", {}).get("children", []):
        post = child.get("data", {})
        if post.get("removed_by_category") or not post.get("selftext"):
            continue
        posts.append(post)
    return posts


def _thread_from_post(post: dict, subreddit: str, top_comments: list[str]) -> RedditThread:
    return RedditThread(
        id=post.get("id", ""),
        title=post.get("title", ""),
        subreddit=post.get("subreddit", subreddit),
        author=post.get("author", "[unknown]"),
        score=int(post.get("score", 0)),
        permalink=post.get("permalink", ""),
        selftext=post.get("selftext", ""),
        top_comments=top_comments,
    )


def _comments_from_json(data, limit: int = 5) -> list[str]:
    """Extract up to `limit` top-level comment bodies from a comments listing.
    Reddit returns a two-element array [post, comments]; anything else yields []."""
    if not isinstance(data, list) or len(data) < 2:
        return []
    comments = []
    for child in data[1].get("data", {}).get("children", []):
        body = child.get("data", {}).get("body")
        if body and body not in ("[deleted]", "[removed]"):
            comments.append(body)
        if len(comments) >= limit:
            break
    return comments


# --- Clients ---------------------------------------------------------------


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


class _RedditSearchBase:
    """Fan-out search loop shared by the live clients.

    Subclasses supply the transport: `_fetch_search` returns a parsed search
    listing (dict) and `_fetch_comments` a parsed comments listing (list), or
    None on failure. Results are deduplicated by post id across all
    (subreddit, query) combinations. Read-only and low-volume: this is batch
    curation, not real-time, so requests are spaced out to stay polite.
    """

    REQUEST_INTERVAL = 6.0  # seconds; stays under ~10 req/min

    def _fetch_search(self, subreddit: str, query: str) -> dict | None:
        raise NotImplementedError

    def _fetch_comments(self, permalink: str):
        raise NotImplementedError

    def search(self, book: Book) -> list[RedditThread]:
        found: dict[str, RedditThread] = {}
        for subreddit in book.subreddits:
            for query in book.search_queries():
                data = self._fetch_search(subreddit, query)
                for post in _posts_from_search(data or {}, subreddit):
                    pid = post.get("id", "")
                    if pid in found:
                        continue
                    time.sleep(self.REQUEST_INTERVAL)
                    comments = _comments_from_json(
                        self._fetch_comments(post.get("permalink", ""))
                    )
                    found[pid] = _thread_from_post(post, subreddit, comments)
                    if len(found) >= book.max_threads_per_book * 2:
                        return list(found.values())
                time.sleep(self.REQUEST_INTERVAL)
        return list(found.values())


class JsonRedditClient(_RedditSearchBase):
    """Live search via Reddit's public `.json` endpoints over raw HTTP.

    Kept for reference and testing; live Reddit now 403s raw HTTP clients, so
    PlaywrightRedditClient is the default. Shares all parsing with the base.
    """

    def __init__(self, user_agent: str):
        self.user_agent = user_agent

    def _get(self, url: str, params: dict):
        for attempt in range(3):
            resp = requests.get(
                url, headers={"User-Agent": self.user_agent}, params=params, timeout=30
            )
            if resp.status_code == 429:
                time.sleep(self.REQUEST_INTERVAL * (attempt + 1))
                continue
            if resp.status_code != 200:
                return None
            try:
                return resp.json()
            except ValueError:
                return None
        return None

    def _fetch_search(self, subreddit: str, query: str) -> dict | None:
        return self._get(
            f"https://www.reddit.com/r/{subreddit}/search.json",
            {"q": query, "restrict_sr": "true", "sort": "top", "t": "all", "limit": 15},
        )

    def _fetch_comments(self, permalink: str):
        if not permalink:
            return None
        return self._get(
            f"https://www.reddit.com{permalink}.json",
            {"sort": "top", "limit": 5, "depth": 1},
        )


class PlaywrightRedditClient(_RedditSearchBase):
    """Live search through a real (headed) Chromium.

    Reddit blocks non-browser HTTP clients outright, so this launches Chromium
    (headed, under Xvfb in the container / WSLg on the host), warms it by
    loading a normal Reddit page so the origin's cookies are set, then issues
    every `.json` request as a same-origin in-page `fetch()`.

    The browser is launched lazily on the first search and reused across all
    books in a build; call `close()` when the build ends. If Playwright isn't
    installed or the browser can't launch/warm, the client degrades to bundled
    fixtures so a build never dies on a missing browser.

    If Reddit ever starts challenging the warmed session, the escalation path
    is bookhunt's fuller pattern (persistent profile + manual noVNC warm); the
    persistent profile dir here is already the foothold for that.
    """

    def __init__(self, profile_dir: Path | None = None):
        self.profile_dir = profile_dir or (ROOT / ".cache" / "browser-profile")
        self._pw = None
        self._context = None
        self._page = None
        self._warmed = False
        self._degraded: FixtureRedditClient | None = None

    def search(self, book: Book) -> list[RedditThread]:
        if self._degraded is not None:
            return self._degraded.search(book)
        try:
            self._ensure_warm()
        except Exception as exc:
            print(f"    Playwright browser unavailable ({exc}); using bundled fixtures")
            self._degraded = FixtureRedditClient()
            return self._degraded.search(book)
        return super().search(book)

    # -- browser lifecycle --

    def _launch(self) -> None:
        from playwright.sync_api import sync_playwright

        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self._pw = sync_playwright().start()
        self._context = self._pw.chromium.launch_persistent_context(
            str(self.profile_dir),
            headless=False,
            viewport={"width": 1280, "height": 900},
            user_agent=_CHROME_UA,
            args=_CHROMIUM_ARGS,
        )
        self._context.set_default_timeout(30000)
        self._context.set_default_navigation_timeout(30000)
        self._page = (
            self._context.pages[0] if self._context.pages else self._context.new_page()
        )

    def _warm(self) -> None:
        # Land on the reddit.com origin so subsequent same-origin fetches carry
        # its cookies; a short settle lets any edge JS run.
        self._page.goto("https://www.reddit.com/", wait_until="domcontentloaded")
        self._page.wait_for_timeout(3000)

    def _ensure_warm(self) -> None:
        if self._warmed:
            return
        self._launch()
        self._warm()
        # Probe: if we can't get JSON back, warm once more, then give up.
        if self._fetch_json("/r/all/top.json?limit=1") is None:
            self._warm()
            if self._fetch_json("/r/all/top.json?limit=1") is None:
                raise RuntimeError("Reddit returned no JSON after warm-up (blocked?)")
        self._warmed = True

    def close(self) -> None:
        for obj, meth in ((self._context, "close"), (self._pw, "stop")):
            try:
                if obj is not None:
                    getattr(obj, meth)()
            except Exception:
                pass
        self._context = self._page = self._pw = None
        self._warmed = False

    # -- transport (same-origin in-page fetch) --

    def _fetch_json(self, path: str):
        """Fetch a relative Reddit URL from inside the warmed page. Returns the
        parsed JSON, or None on a non-200 status or non-JSON body."""
        for attempt in range(3):
            result = self._page.evaluate(
                """async (p) => {
                    const r = await fetch(p, { headers: { accept: 'application/json' } });
                    return { status: r.status, body: await r.text() };
                }""",
                path,
            )
            if result["status"] == 429:
                time.sleep(self.REQUEST_INTERVAL * (attempt + 1))
                continue
            if result["status"] != 200:
                return None
            try:
                return json.loads(result["body"])
            except (json.JSONDecodeError, TypeError):
                return None
        return None

    def _fetch_search(self, subreddit: str, query: str) -> dict | None:
        qs = urlencode(
            {"q": query, "restrict_sr": "true", "sort": "top", "t": "all", "limit": 15}
        )
        return self._fetch_json(f"/r/{subreddit}/search.json?{qs}")

    def _fetch_comments(self, permalink: str):
        if not permalink:
            return None
        qs = urlencode({"sort": "top", "limit": 5, "depth": 1})
        return self._fetch_json(f"{permalink}.json?{qs}")


class CachingRedditClient:
    """Wraps any RedditClient, persisting fetched threads to .cache/threads/.

    On a cache hit it serves from disk (no network); `refresh=True` forces a
    re-fetch. This keeps ranking/synthesis iteration off the Reddit API.
    """

    def __init__(self, inner: RedditClient, refresh: bool = False):
        self.inner = inner
        self.refresh = refresh

    def search(self, book: Book) -> list[RedditThread]:
        # Imported here to avoid a circular import (cache imports this module).
        from .cache import load_cached_threads, save_threads

        if not self.refresh:
            cached = load_cached_threads(book.slug)
            if cached is not None:
                return cached
        threads = self.inner.search(book)
        save_threads(book.slug, threads)
        return threads

    def close(self) -> None:
        close = getattr(self.inner, "close", None)
        if close is not None:
            close()


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
