# Concept brief — gttp browsing polish

## Problem
gttp already generates a static site (`site/index.html` + `site/books/*.html`) and
it is already live at **https://gttp.mooseflip.com** (Cloudflare Tunnel →
`localhost:8100`, which serves the repo's `site/` directory). The site is
functional but plain: the index is a flat `<ul>` with no way to find a book,
"no summaries yet" books clutter the list, and long book pages have no in-page
navigation. It doesn't yet feel like a browsable site.

## Goal
Make the generated site pleasant to browse — find a book fast, scan the index,
and navigate within a book — by improving the **HTML generation in
`publish.py`**, then rebuilding `site/` so the changes go live at
gttp.mooseflip.com automatically. No hosting/infra changes.

## In scope (all four, this pass)
1. **Instant search/filter** — a search box on the index that filters the book
   list by title + author + blurb as the user types. Pure client-side
   (inline `<script>`), no backend, works from `file://` and over the tunnel.
2. **Redesigned index cards** — more scannable index: show the book's core-idea
   line (or blurb) under each title, a subreddit/topic tag when available, and
   visually deprioritize (dim + sort to bottom) books with "No qualifying
   Reddit summaries found yet."
3. **Per-idea navigation** — on each book page, an anchored jump list of the
   ideas (the 5 bullets) so readers can navigate within a book; a sticky/clear
   "← All books" back link.
4. **Shared site header/footer** — a consistent header (site title linking home)
   and footer rendered on both the index and every book page for a cohesive feel.

## Out of scope
- Any Cloudflare/tunnel/DNS/hosting changes — already done and working; do not touch
  `~/.cloudflared/`, the :8100 server, or DNS.
- Changing the Reddit pipeline, ranking, or synthesis (`pipeline.py`, `ranking.py`,
  `synthesize.py`, `reddit_client.py`).
- A `gttp deploy`/serve subcommand, search over full page text, tags beyond what
  data already provides, JS frameworks/build tooling, external assets/CDNs.
- Editing the Markdown (`.md`) output format — polish is HTML-only. (Keep `.md`
  generation working as-is.)

## Constraints
- **Self-contained HTML** — all CSS/JS inline in the generated files (matches the
  current `publish.py` approach). No external fonts, scripts, or network calls;
  must render offline from `file://` and identically over the tunnel.
- The live server serves the repo `site/` dir; `gttp build --offline` regenerates
  it. Changes go live on rebuild — no separate deploy step.
- Preserve graceful handling of books with no summaries (they still get a page and
  an index entry, just deprioritized).
- Keep the existing warm/minimal aesthetic (system font, `#e0703c` accent,
  `color-scheme: light dark`). Enhance, don't redesign from scratch.
- Python 3.12, stdlib only for publishing (no new deps). Tests must still pass.

## Acceptance criteria
1. `gttp build --offline` succeeds and regenerates `site/index.html` +
   `site/books/*.html` with no errors.
2. **Search:** typing a query in the index search box hides non-matching book
   entries and shows matching ones, matching against title, author, and blurb;
   clearing the box restores the full list. Works with JS only (no server).
3. **Index cards:** each entry shows the core-idea/blurb line; books with no
   summaries are visually dimmed and ordered after books that have summaries.
4. **Per-idea nav:** a book page that has bullets (e.g. `deep-work.html`,
   `the-7-habits-of-highly-effective-people.html`) renders an in-page jump list
   whose links anchor to each idea; every book page has a working "← All books"
   link back to the index.
5. **Header/footer:** index and every book page share a header (title links to
   index) and a footer.
6. `pytest` passes (existing tests unchanged in intent).
7. Live check: after rebuild, `curl -sS https://gttp.mooseflip.com` reflects the
   new index (contains the search box markup), confirming the served dir picked
   up the changes.

## Open questions & decisions made
- **Hosting:** DONE in a prior session — tunnel gttp.mooseflip.com → localhost:8100
  serving repo `site/`. Confirmed live (HTTP 200; served index md5 == repo
  `site/index.html`). No infra work in this task.
- **Access:** public. No auth.
- **Search scope:** title + author + blurb only (data already on the index). Decided
  to keep it simple; no full-text search.
- **"Topic tag":** use subreddit(s) from a book's sources when present; omit the tag
  when a book has no qualifying summaries. Executor: derive from existing page data,
  don't invent tags.

## Relevant files/areas
- `src/gttp/publish.py` — **primary file.** Renders Markdown + the HTML index and
  per-book HTML. All four features are implemented here (index `render_*` +
  per-book HTML rendering + shared header/footer helper + inline search JS).
- `src/gttp/models.py` — `BookPage` shape (title, author, core_idea, bullets,
  honest_take, quotes, sources). Source of the fields the index/pages render.
- `src/gttp/config.py` — `SITE_DIR = ROOT / "site"` (output location).
- `src/gttp/cli.py` — `gttp build [--offline]` entry point used to regenerate.
- `tests/test_pipeline.py` — existing tests that touch build/publish; keep green,
  extend if a feature warrants a cheap assertion (e.g. search box present in index).
- `site/index.html`, `site/books/*.html` — current output to inspect for the
  present structure/style to preserve.
- Live: `https://gttp.mooseflip.com` (served from repo `site/` via :8100 tunnel).
