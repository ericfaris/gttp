"""Orchestration: search -> filter -> rank -> synthesize -> publish.

Incremental by design: threads and synthesized pages are cached, so `--only`
rebuilds a single book while the index still renders the rest from cache, and
re-runs don't re-hit Reddit unless `--refresh` is passed. Every book is built
in isolation — one failure never sinks the whole run.
"""

from __future__ import annotations

from .cache import is_final, load_cached_page, page_quality, save_page
from .config import Book, slugify
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
    # Live Reddit blocks raw HTTP clients, so fetch through a real browser; the
    # client degrades to fixtures on its own if the browser can't launch.
    return CachingRedditClient(PlaywrightRedditClient(), refresh=refresh)


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
    targets = match_books(books, only)
    target_slugs = {b.slug for b in targets}
    client = make_reddit_client(offline, refresh)

    pages: dict[str, BookPage] = {}
    try:
        # (Re)build the targeted books — `force` only ever applies here, never
        # to the cache-filled rest below, so a plain full `gttp build` can
        # never mass-resummarize an already-finalized catalog by accident.
        for book in targets:
            before = load_cached_page(book.slug)
            page = build_book(book, client, force=force)
            if page is not before:
                save_page(book.slug, page)
            pages[book.slug] = page

        # Fill in the rest from cache so the index stays complete; build any
        # that have never been generated.
        for book in books:
            if book.slug in target_slugs:
                continue
            cached = load_cached_page(book.slug)
            if cached is None:
                page = build_book(book, client)
                save_page(book.slug, page)
                cached = page
            pages[book.slug] = cached
    finally:
        # Release the browser (if the client launched one).
        close = getattr(client, "close", None)
        if close is not None:
            close()

    ordered = [pages[b.slug] for b in books]
    index = write_site(ordered)
    built = len(targets) if only else len(books)
    print(f"\nBuilt {built} book(s), wrote {len(ordered)} pages. Open {index}")
    return ordered


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
