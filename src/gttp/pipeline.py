"""Orchestration: search -> filter -> rank -> synthesize -> publish."""

from __future__ import annotations

from .config import Book, reddit_credentials
from .models import BookPage
from .publish import write_site
from .ranking import filter_threads, rank_threads
from .reddit_client import FixtureRedditClient, HttpRedditClient, RedditClient
from .synthesize import synthesize


def make_reddit_client(offline: bool) -> RedditClient:
    if offline:
        return FixtureRedditClient()
    creds = reddit_credentials()
    if creds is None:
        print("No Reddit credentials found; using bundled fixtures.")
        return FixtureRedditClient()
    return HttpRedditClient(*creds)


def build_book(book: Book, client: RedditClient) -> BookPage:
    print(f"  {book.title}")
    threads = client.search(book)
    print(f"    found {len(threads)} candidate threads")
    kept = filter_threads(threads, book)
    print(f"    {len(kept)} passed filtering")
    ranked = rank_threads(kept, book)
    page = synthesize(book, ranked)
    print(f"    synthesized page ({page.generated_by})")
    return page


def build_all(books: list[Book], offline: bool = False) -> list[BookPage]:
    client = make_reddit_client(offline)
    pages = [build_book(book, client) for book in books]
    index = write_site(pages)
    print(f"\nWrote {len(pages)} pages. Open {index}")
    return pages
