"""Settings and catalog loading.

Reads credentials from the environment (via a local .env if present) and the
book catalog from books.yaml. Keeping this in one place means the rest of the
pipeline never touches os.environ or the YAML directly.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from dotenv import load_dotenv

# Project root is two levels up from this file: src/gttp/config.py -> repo root.
ROOT = Path(__file__).resolve().parents[2]
BOOKS_FILE = ROOT / "books.yaml"
FIXTURES_DIR = ROOT / "fixtures"
SITE_DIR = ROOT / "site"
# `gttp build --offline` writes here and never saves pages to the cache. The
# production container serves site/ and .cache/ from this same checkout, so
# an offline (fixtures/heuristic) build must not be able to touch either.
OFFLINE_SITE_DIR = ROOT / "site-offline"
STATIC_DIR = ROOT / "static"

# The model used for ranking and synthesis.
MODEL = "claude-sonnet-5-5"

load_dotenv(ROOT / ".env")


@dataclass(frozen=True)
class Book:
    title: str
    author: str | None = None
    subreddits: list[str] = field(default_factory=list)
    queries: list[str] = field(default_factory=list)
    min_score: int = 10
    min_chars: int = 400
    max_threads_per_book: int = 40
    synthesis_top_n: int = 5
    # For titles that are everyday phrases ("Boundaries", "Emotional
    # Intelligence"): a thread must also name an author's surname to count
    # as being about the book.
    require_author: bool = False

    @property
    def slug(self) -> str:
        return slugify(self.title)

    def search_queries(self) -> list[str]:
        return [q.format(title=self.title) for q in self.queries]


def slugify(text: str) -> str:
    keep = [c.lower() if c.isalnum() else "-" for c in text.strip()]
    slug = "".join(keep)
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug.strip("-")


def load_catalog(path: Path = BOOKS_FILE) -> list[Book]:
    """Load books.yaml, applying `defaults` to each book entry."""
    data = yaml.safe_load(path.read_text()) or {}
    defaults = data.get("defaults", {})
    books: list[Book] = []
    for entry in data.get("books", []):
        merged = {**defaults, **entry}
        books.append(
            Book(
                title=merged["title"],
                author=merged.get("author"),
                subreddits=list(merged.get("subreddits", [])),
                queries=list(merged.get("queries", [])),
                min_score=int(merged.get("min_score", 10)),
                min_chars=int(merged.get("min_chars", 400)),
                max_threads_per_book=int(merged.get("max_threads_per_book", 40)),
                synthesis_top_n=int(merged.get("synthesis_top_n", 5)),
                require_author=bool(merged.get("require_author", False)),
            )
        )
    return books


def add_book(title: str, author: str | None = None, path: Path = BOOKS_FILE) -> bool:
    """Append a book to books.yaml. Returns False if it already exists.

    Appends text to the end of the file instead of re-dumping the YAML, so
    the comments in books.yaml survive. The write happens in place (same
    inode): books.yaml is a single-file bind mount into the app container,
    and a file replaced by rename would leave the container reading the old,
    deleted one.
    """
    text = path.read_text()
    before = yaml.safe_load(text) or {}
    books = before.get("books") or []
    if slugify(title) in {slugify(b["title"]) for b in books}:
        return False
    if "books" in before and list(before)[-1] != "books":
        raise ValueError("books: must be the last key in books.yaml")
    entry = {"title": title}
    if author:
        entry["author"] = author
    snippet = yaml.safe_dump(
        [entry], sort_keys=False, allow_unicode=True, default_flow_style=False
    )
    if "books" not in before:
        snippet = "books:\n" + snippet
    with path.open("a") as f:
        if text and not text.endswith("\n"):
            f.write("\n")
        f.write(snippet)

    after = yaml.safe_load(path.read_text()) or {}
    others_same = {k: v for k, v in after.items() if k != "books"} == {
        k: v for k, v in before.items() if k != "books"
    }
    if not others_same or after.get("books") != books + [entry]:
        with path.open("r+") as f:
            f.truncate(0)
            f.write(text)
        raise ValueError(f"appending {title!r} did not round-trip; books.yaml left unchanged")
    return True


def anthropic_key() -> str | None:
    return os.environ.get("ANTHROPIC_API_KEY") or None
