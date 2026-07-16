"""On-disk caches so re-runs are cheap.

Two caches under .cache/:

- threads/<slug>.json — raw fetched Reddit threads (gitignored; regenerable).
  Lets you re-tune ranking and synthesis without re-hitting the Reddit API.
- pages/<slug>.json — synthesized BookPages (committed to git — see
  `page_quality`/`is_final`). Lets `--only` rebuild one book while the index
  still renders the rest from cache, and means a finalized summary survives
  even if the working tree's cache is wiped or corrupted.
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


def page_quality(page: BookPage | None) -> int:
    """Rank a page so a rebuild never silently saves something worse.

    -1 build error / missing, 0 empty placeholder ("no qualifying threads"),
    1 heuristic content, 2 real Claude synthesis. `save_page` should only ever
    be called with a page whose quality is >= the quality of what it replaces.
    """
    if page is None or page.generated_by == "error":
        return -1
    if not page.bullets and not page.sources:
        return 0
    return 2 if page.generated_by == "claude" else 1


def is_final(page: BookPage | None) -> bool:
    """A finalized page (real Claude synthesis) is never rebuilt automatically —
    only an explicit `--force` on the CLI can touch it again."""
    return page is not None and page_quality(page) == 2
