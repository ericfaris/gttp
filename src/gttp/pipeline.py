"""Orchestration: search -> filter -> rank -> synthesize -> publish.

Incremental by design: threads and synthesized pages are cached, so `--only`
rebuilds a single book while the index still renders the rest from cache, and
re-runs don't re-hit Reddit unless `--refresh` is passed. Every book is built
in isolation — one failure never sinks the whole run.
"""

from __future__ import annotations

import os

from .arctic import ArcticShiftClient
from .cache import is_final, load_cached_page, page_quality, save_page
from .config import OFFLINE_SITE_DIR, Book, slugify
from .enrich import can_enrich, enrich, is_enriched
from .models import BookPage
from .publish import write_site
from .ranking import filter_threads, rank_threads
from .reddit_client import (
    CachingRedditClient,
    FixtureRedditClient,
    PlaywrightRedditClient,
    RedditClient,
)
from .synthesize import synthesize


def make_reddit_client(offline: bool, refresh: bool = False) -> RedditClient:
    if offline:
        return FixtureRedditClient()
    # Reddit blocks the lab's IP from its data endpoints (2026-10-25), so the
    # default source is the Arctic Shift archive. GTTP_REDDIT_SOURCE=browser
    # switches back to the headed-browser client (which degrades to fixtures
    # on its own if Reddit refuses it).
    if os.environ.get("GTTP_REDDIT_SOURCE", "arctic") == "browser":
        return CachingRedditClient(PlaywrightRedditClient(), refresh=refresh)
    return CachingRedditClient(ArcticShiftClient(), refresh=refresh)


def match_books(books: list[Book], only: str | None) -> list[Book]:
    """Books to (re)build. `only` matches on slug or a case-insensitive title
    substring; None means all books."""
    if not only:
        return list(books)
    q = slugify(only)
    return [b for b in books if q in b.slug or only.lower() in b.title.lower()]


def build_book(book: Book, client: RedditClient, force: bool = False) -> BookPage:
    """Build one book's page.

    A finalized page (real Claude synthesis — see `is_final`) is never
    rebuilt automatically; pass `force=True` (the CLI's `--force`) to
    deliberately redo it. For everything else: on any failure, or if the
    fresh result is lower quality than what's cached (e.g. the Anthropic API
    is unreachable/out of credit and synthesis silently degrades to the
    heuristic fallback), the existing cached page wins — a rebuild must never
    save something worse than what it started with.
    """
    cached = load_cached_page(book.slug)
    if cached is not None and is_final(cached) and not force:
        print(f"  {book.title}: already finalized, skipping (use --force to redo)")
        return cached

    print(f"  {book.title}")
    try:
        threads = client.search(book)
        print(f"    found {len(threads)} candidate threads")
        kept = filter_threads(threads, book)
        print(f"    {len(kept)} passed filtering")
        ranked, ranking_used_claude = rank_threads(kept, book)
        page = synthesize(book, ranked, ranking_used_claude)
        print(f"    synthesized page ({page.generated_by})")
        page = _try_enrich(page)
    except Exception as exc:
        print(f"    ERROR: {exc}")
        page = _error_page(book, exc)

    if cached is not None and page_quality(page) < page_quality(cached):
        print(
            f"    new result ({page.generated_by}) is lower quality than the "
            f"cached page ({cached.generated_by}); keeping the cached page"
        )
        return cached
    return page


def build_all(
    books: list[Book],
    offline: bool = False,
    only: str | None = None,
    refresh: bool = False,
    force: bool = False,
) -> list[BookPage]:
    """Build the targeted books and render the site.

    Offline builds are sandboxed: pages stay in memory (nothing is saved to
    .cache/pages/) and the site goes to OFFLINE_SITE_DIR, not the site/ that
    production serves. On 2026-07-11 an offline build in the shared checkout
    replaced 17 live summaries with fixture/heuristic pages.
    """
    targets = match_books(books, only)
    target_slugs = {b.slug for b in targets}
    client = make_reddit_client(offline, refresh)
    save = (lambda slug, page: None) if offline else save_page

    pages: dict[str, BookPage] = {}
    try:
        # (Re)build the targeted books — `force` only ever applies here, never
        # to the cache-filled rest below, so a plain full `gttp build` can
        # never mass-resummarize an already-finalized catalog by accident.
        for book in targets:
            before = load_cached_page(book.slug)
            page = build_book(book, client, force=force)
            if page is not before:
                save(book.slug, page)
            pages[book.slug] = page

        # Fill in the rest from cache so the index stays complete; build any
        # that have never been generated.
        for book in books:
            if book.slug in target_slugs:
                continue
            cached = load_cached_page(book.slug)
            if cached is None:
                page = build_book(book, client)
                save(book.slug, page)
                cached = page
            pages[book.slug] = cached
    finally:
        # Release the browser (if the client launched one).
        close = getattr(client, "close", None)
        if close is not None:
            close()

    ordered = [pages[b.slug] for b in books]
    index = write_site(ordered, OFFLINE_SITE_DIR) if offline else write_site(ordered)
    built = len(targets) if only else len(books)
    print(f"\nBuilt {built} book(s), wrote {len(ordered)} pages. Open {index}")
    return ordered


def _try_enrich(page: BookPage) -> BookPage:
    """Enrich a fresh Claude page; a failure just leaves it un-enriched
    (`gttp enrich` can fill it in later)."""
    if not can_enrich(page):
        return page
    try:
        return enrich(page)
    except Exception as exc:
        print(f"    enrich: skipped ({exc}); run `gttp enrich` later")
        return page


def enrich_all(
    books: list[Book], only: str | None = None, force: bool = False
) -> tuple[int, int]:
    """Add the verdict / try-today layer to cached pages, then re-render the
    site. Only adds fields — the summary itself is never touched, so this is
    safe on finalized pages. Already-enriched pages are skipped unless
    `force`. Returns (enriched, failed)."""
    targets = {b.slug for b in match_books(books, only)}
    done = failed = 0
    pages: list[BookPage] = []
    for book in books:
        page = load_cached_page(book.slug)
        if page is None:
            continue
        if book.slug in targets and can_enrich(page) and (
            force or not is_enriched(page)
        ):
            print(f"  {book.title}")
            try:
                page = enrich(page)
                save_page(book.slug, page)
                done += 1
            except Exception as exc:
                print(f"    ERROR: {exc}")
                failed += 1
        pages.append(page)
    index = write_site(pages)
    print(f"\nEnriched {done} page(s), {failed} failed. Open {index}")
    return done, failed


def render_site(books: list[Book]) -> int:
    """Re-render the site from cached pages only: no Reddit, no Claude, no
    cache writes. The container runs this on every start, so a deploy (which
    only rebuilds and restarts) always serves pages rendered by the new code.
    Returns the number of pages rendered."""
    pages = [p for p in (load_cached_page(b.slug) for b in books) if p is not None]
    index = write_site(pages)
    print(f"Rendered {len(pages)} cached page(s). Open {index}")
    return len(pages)


def _error_page(book: Book, exc: Exception) -> BookPage:
    return BookPage(
        title=book.title,
        author=book.author,
        core_idea="This page could not be generated on the last run.",
        bullets=[],
        honest_take=f"Build error: {exc}",
        quotes=[],
        sources=[],
        generated_by="error",
    )
