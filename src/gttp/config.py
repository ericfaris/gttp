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

# The model used for ranking and synthesis. Opus 4.8 is the current default.
MODEL = "claude-opus-4-8"

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
            )
        )
    return books


def add_book(title: str, author: str | None = None, path: Path = BOOKS_FILE) -> bool:
    """Append a book to books.yaml. Returns False if it already exists."""
    data = yaml.safe_load(path.read_text()) or {}
    books = data.setdefault("books", [])
    existing = {slugify(b["title"]) for b in books}
    if slugify(title) in existing:
        return False
    entry = {"title": title}
    if author:
        entry["author"] = author
    books.append(entry)
    path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True))
    return True


def anthropic_key() -> str | None:
    return os.environ.get("ANTHROPIC_API_KEY") or None
