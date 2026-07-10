"""On-disk caches so re-runs are cheap.

Two caches, both under .cache/ (gitignored):

- threads/<slug>.json — raw fetched Reddit threads. Lets you re-tune ranking
  and synthesis without re-hitting the Reddit API.
- pages/<slug>.json — synthesized BookPages. Lets `--only` rebuild one book
  while the index still renders the rest from cache.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from .config import ROOT
from .models import BookPage, RedditThread
from .reddit_client import _thread_from_dict

CACHE_DIR = ROOT / ".cache"
THREADS_DIR = CACHE_DIR / "threads"
PAGES_DIR = CACHE_DIR / "pages"


def load_cached_threads(slug: str) -> list[RedditThread] | None:
    path = THREADS_DIR / f"{slug}.json"
    if not path.exists():
        return None
    raw = json.loads(path.read_text())
    return [_thread_from_dict(d) for d in raw]


def save_threads(slug: str, threads: list[RedditThread]) -> None:
    THREADS_DIR.mkdir(parents=True, exist_ok=True)
    (THREADS_DIR / f"{slug}.json").write_text(
        json.dumps([asdict(t) for t in threads], indent=2)
    )


def load_cached_page(slug: str) -> BookPage | None:
    path = PAGES_DIR / f"{slug}.json"
    if not path.exists():
        return None
    d = json.loads(path.read_text())
    return BookPage(**d)


def save_page(slug: str, page: BookPage) -> None:
    PAGES_DIR.mkdir(parents=True, exist_ok=True)
    (PAGES_DIR / f"{slug}.json").write_text(json.dumps(asdict(page), indent=2))
