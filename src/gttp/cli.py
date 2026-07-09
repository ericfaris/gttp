"""Command-line entry point: `gttp add`, `gttp build`, `gttp list`."""

from __future__ import annotations

import argparse
import sys

from . import __version__
from .config import add_book, load_catalog
from .pipeline import build_all


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

    sub.add_parser("list", help="list catalog books")

    args = parser.parse_args(argv)

    if args.command == "add":
        if add_book(args.title, args.author):
            print(f"Added '{args.title}'. Run `gttp build` to generate its page.")
        else:
            print(f"'{args.title}' is already in the catalog.")
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
        print(f"Building {len(books)} book(s)"
              f"{' (offline)' if args.offline else ''}...")
        build_all(books, offline=args.offline)
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
