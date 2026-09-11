# Concept Brief: Visual Redesign + Book Cover Images

## Problem

The generated site (`src/gttp/publish.py`) works but looks generic: plain
text cards on the index, no imagery anywhere. For a site whose whole pitch is
"real books, crowd-vetted," the lack of cover art undercuts the sense that
these are real, tangible books — it reads more like a list of blog post
titles than a library shelf.

## Goal

1. Give the site a more distinctive visual identity (index + book detail
   pages) — better typography, color, spacing, card layout — while keeping it
   a single dependency-free static site (no build tooling, no JS framework,
   inline CSS in the existing `_HTML_SHELL`).
2. Add book cover images to index cards and detail pages so the site conveys
   "real world gravity" — these are real, known books.

## In scope

- Fetch cover images from **Open Library** (`covers.openlibrary.org` +
  the Open Library Search API to resolve title/author → cover id/ISBN, no key
  required).
- Cover fetch happens as part of **`gttp add`**: when a book is added to the
  catalog, look up and download its cover, store it locally (e.g.
  `covers/<slug>.jpg`), so it's a persisted repo asset like the Markdown
  pages, not refetched every build.
- **Backfill existing catalog**: the 20 books already in `books.yaml` have no
  cover yet. Provide a way to backfill them (e.g. a `gttp covers` command
  that fetches any missing cover for every catalog book, or folding backfill
  logic into `gttp build` for books lacking a cover file — pick whichever
  fits the existing pipeline shape best; a dedicated command that's easy to
  re-run is preferred since it mirrors `gttp add`'s one-shot fetch model).
- **Offline mode / missing cover fallback**: `gttp build --offline` must
  remain zero-network and zero-key. When no cover file exists for a book
  (offline mode, fetch failed, or not yet backfilled), render a generated
  placeholder — e.g. a CSS/SVG tile with the book's initial letter over a
  deterministic color swatch derived from the title — so layout stays
  consistent whether or not a real cover is present.
- Visual redesign of `_HTML_SHELL` CSS and the index/detail card markup in
  `publish.py` to incorporate cover art (index: thumbnail per card; detail
  page: larger cover near the title) plus a general typography/spacing/color
  pass. Keep the existing features intact: client-side search, tag pills,
  TOC, sticky back-link, light/dark via `color-scheme`.

## Out of scope

- No other content sources (Google Books, manual local-only mode) — Open
  Library only, per decision.
- No server-side rendering, JS framework, or build pipeline (Webpack/Vite
  etc.) — stays a Python script emitting static HTML.
- No changes to the Reddit-fetch/ranking/synthesis pipeline stages.
- No per-book cover editing UI — covers are fetched programmatically, not
  hand-curated (backfill/re-fetch is fine, manual override is not required
  for v1).

## Constraints

- Must not break `gttp build --offline` (zero network calls, no keys,
  deterministic — used by tests and CI-like flows).
- Open Library has no API key requirement; be a reasonable citizen (single
  request per book at add/backfill time, not per build).
- Existing repo conventions: static HTML written to `site/`, per-book
  Markdown mirrors kept in sync, no new runtime dependencies unless
  justified (requests is already a dependency and can be reused for the
  Open Library HTTP calls).
- Cover images should be committed alongside the repo's existing pattern of
  persisting generated/fetched artifacts (Markdown pages, `.cache/`) — decide
  during planning whether `covers/` lives in the repo (committed) or in
  `.cache/` (gitignored); note that `.cache/` currently holds re-fetchable
  data while Markdown pages are committed output, so covers likely belong
  with the committed output given they're fetched once and meant to ship
  with the site.

## Acceptance criteria

- `gttp add "<title>" [--author ...]` fetches and stores a cover image for
  the new book (when network/Open Library is reachable), without changing
  the command's existing catalog-append behavior.
- A new backfill path (command or build-integrated) fetches covers for the
  20 existing catalog books that currently lack one.
- `gttp build --offline` still runs with zero network calls and produces a
  complete site, using placeholders for any book without a stored cover.
- `gttp build` (online) renders real cover thumbnails on the index cards and
  a larger cover on each book detail page for any book with a stored cover
  file, and a generated placeholder otherwise.
- The redesigned index and detail pages preserve current functional
  features (search box filtering, subreddit tag pills, TOC on detail pages,
  sticky back-link, dark/light via `color-scheme`) — nothing regresses.
- Existing tests (`tests/test_pipeline.py`, `tests/test_reddit_parsing.py`)
  continue to pass; add coverage for the new cover-fetch/backfill/placeholder
  logic.

## Open questions & decisions made

- Cover source: **Open Library Covers API** (decided).
- Offline/missing-cover fallback: **generated placeholder**, not omitted
  (decided).
- Fetch timing: **at `gttp add` time** (decided) — plus a backfill mechanism
  for the pre-existing 20 books (flagged above; exact shape left to the
  planning phase, e.g. `gttp covers` subcommand).
- Storage location for cover files (`covers/` committed vs `.cache/`
  gitignored) — leaned toward committed `covers/`, final call left to
  planning phase given it affects repo size/diff noise.
- Cover resolution/size (Open Library offers S/M/L) — left to planning phase;
  likely M for index thumbnails, L for detail page.

## Relevant files/areas

- `src/gttp/publish.py` — HTML/CSS rendering (`_HTML_SHELL`, index card,
  book detail rendering) — primary file for the redesign and cover markup.
- `src/gttp/models.py` — `BookPage` dataclass; will likely need a
  `cover_path` or similar field.
- `src/gttp/config.py` — `Book` dataclass, `slugify`, `add_book`,
  `load_catalog`; likely where a `Book.cover_path` property and the Open
  Library fetch helper get added, and where `add_book` triggers the fetch.
- `src/gttp/cli.py` — `gttp add` command wiring; possible new `gttp covers`
  subcommand for backfill.
- `src/gttp/pipeline.py` — where `BookPage`s are built; needs to plumb the
  cover path through to `publish.write_site`.
- `books.yaml` — the 20 existing catalog entries needing backfilled covers.
- `tests/test_pipeline.py` — existing pipeline test patterns to extend.
- No existing `covers/` or image-fetch module — this is new.

## Repo commands & tree state

- Python env: `.venv/bin/python`, `.venv/bin/pytest` (project uses a local
  venv; do not assume bare `python`/`pytest` on `PATH`).
- Run tests: `.venv/bin/pytest` (or `.venv/bin/pytest tests/`).
- CLI entry point (installed in venv): `.venv/bin/gttp add|build|list`.
- Offline build for manual verification: `.venv/bin/gttp build --offline`
  then inspect `site/index.html` and `site/books/*.html`.
- Working tree at brief time: clean except an untracked `.claude/` directory
  (this skill's own working files) — no pre-existing uncommitted source
  changes to worry about.
