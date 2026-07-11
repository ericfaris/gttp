"""Command-line entry point: `gttp add`, `gttp build`, `gttp list`."""

from __future__ import annotations

import argparse
import sys
import time

from . import __version__
from .config import add_book, load_catalog, slugify
from .covers import cover_file, fetch_cover
from .pipeline import build_all, match_books


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="gttp", description=__doc__)
    parser.add_argument("--version", action="version", version=f"gttp {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p_add = sub.add_parser("add", help="add a book to the catalog")
    p_add.add_argument("title", help="book title")
    p_add.add_argument("--author", help="author name")

    p_build = sub.add_parser("build", help="build the site for all catalog books")
    p_build.add_argument(
        "--offline",
        action="store_true",
        help="use bundled fixtures and the heuristic synthesizer (no network)",
    )
    p_build.add_argument(
        "--only",
        metavar="TITLE",
        help="rebuild just the matching book (by title substring); the rest "
        "of the index renders from cache",
    )
    p_build.add_argument(
        "--refresh",
        action="store_true",
        help="ignore cached threads and re-fetch from Reddit",
    )

    sub.add_parser("list", help="list catalog books")

    p_covers = sub.add_parser(
        "covers", help="fetch missing cover images from Open Library"
    )
    p_covers.add_argument(
        "--force",
        action="store_true",
        help="re-fetch even if a cover file already exists",
    )

    args = parser.parse_args(argv)

    if args.command == "add":
        if add_book(args.title, args.author):
            print(f"Added '{args.title}'. Run `gttp build` to generate its page.")
        else:
            print(f"'{args.title}' is already in the catalog.")
        slug = slugify(args.title)
        if fetch_cover(args.title, args.author, slug):
            print(f"Fetched cover -> covers/{slug}.jpg")
        else:
            print("No cover found (run 'gttp covers' later to retry).")
        return 0

    if args.command == "covers":
        fetched = missing = skipped = 0
        for book in load_catalog():
            if cover_file(book.slug) and not args.force:
                print(f"  {book.title}: skipped (exists)")
                skipped += 1
                continue
            if fetch_cover(book.title, book.author, book.slug):
                print(f"  {book.title}: ok")
                fetched += 1
            else:
                print(f"  {book.title}: no cover found")
                missing += 1
            time.sleep(1)
        print(f"Fetched {fetched}, missing {missing}, skipped {skipped}.")
        return 0

    if args.command == "list":
        for book in load_catalog():
            suffix = f" — {book.author}" if book.author else ""
            print(f"- {book.title}{suffix}")
        return 0

    if args.command == "build":
        books = load_catalog()
        if not books:
            print("Catalog is empty. Add a book with `gttp add \"<title>\"`.")
            return 1
        if args.only and not match_books(books, args.only):
            print(f"No catalog book matches '{args.only}'.")
            return 1
        scope = f"'{args.only}'" if args.only else f"{len(books)} book(s)"
        print(f"Building {scope}{' (offline)' if args.offline else ''}...")
        build_all(books, offline=args.offline, only=args.only, refresh=args.refresh)
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
