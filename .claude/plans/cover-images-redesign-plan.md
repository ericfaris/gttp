# Implementation Plan: Visual Redesign + Book Cover Images

Executor note: this plan is self-contained. The concept brief it derives from is
`.claude/plans/cover-images-redesign-brief.md` (same directory) if you want the
original framing, but everything needed is restated here. All paths are relative
to the repo root `/home/eric/projects/gttp`. Use the local venv for every
command: `.venv/bin/pytest`, `.venv/bin/gttp` — bare `python`/`pytest` are NOT
on PATH for this project.

## Summary

`gttp` generates a static site of crowd-vetted book summaries (`site/index.html`
+ `site/books/*.html`/`.md`) from `books.yaml` via `src/gttp/publish.py`. The
site currently has no imagery and generic styling. This work adds real book
cover images sourced from Open Library — fetched once at `gttp add` time and
via a new `gttp covers` backfill command, stored as committed repo assets in a
new `covers/` directory — plus a visual redesign of the index cards and book
detail pages (typography, spacing, color, cover thumbnails on cards, a larger
cover on detail pages). Books without a stored cover render a deterministic
inline-SVG placeholder so `gttp build --offline` stays zero-network and layout
stays consistent. No new dependencies (`requests` is already a dependency), no
JS framework, no build tooling — the site remains a single self-contained HTML
shell (`_HTML_SHELL` in `publish.py`).

## Approach & key decisions

### Decision 1: Cover storage — committed `covers/` at repo root (NOT `.cache/`)

Covers live in a new git-tracked `covers/` directory at the repo root, one file
per book named `covers/<slug>.jpg` (slug from `gttp.config.slugify`). At build
time, `write_site` copies any covers for catalog books into `site/covers/`.

Rationale — this is forced by how CI publishes the site, not just preference:

- `.github/workflows/build.yml` runs `gttp build --offline` on a **fresh
  checkout** and deploys `site/` to GitHub Pages. `.cache/` is gitignored and
  therefore empty in CI; covers stored there would never appear on the
  published site — every deploy would show placeholders. Committed `covers/`
  is the only storage that survives to CI.
- `.cache/` (see `src/gttp/cache.py` docstring) holds *re-fetchable* data;
  covers are fetch-once assets meant to ship with the site, matching the
  brief's lean toward committed storage.
- Note a repo quirk the brief got slightly wrong: `site/` itself is
  **gitignored** (see `.gitignore` — `site/` is listed), so the Markdown
  pages are regenerated output, not committed. This is exactly why covers
  cannot live only inside `site/` either — they'd vanish on fresh checkout.
  Hence: source of truth `covers/` (committed), copied to `site/covers/`
  (regenerated) on every build.

Repo-size cost: 20 JPEGs at Open Library "L" size run roughly 20–150 KB each,
~1–2 MB total. Acceptable; they change essentially never after fetch.

### Decision 2: Backfill shape — a dedicated `gttp covers` subcommand

Add `gttp covers [--force]` to `src/gttp/cli.py`. It iterates
`load_catalog()`, fetches a cover for every book missing `covers/<slug>.jpg`
(or all books with `--force`), and prints a per-book ok/miss/fail line plus a
summary count. Exit code 0 even if some fetches fail (misses are expected —
obscure titles may have no Open Library cover).

Rejected alternative — folding backfill into `gttp build`: it would put
network calls on the build path, complicating the hard requirement that
`gttp build --offline` is zero-network, and would silently re-hit Open Library
on every build for any book that legitimately has no cover there. A separate
idempotent command mirrors `gttp add`'s one-shot fetch model and is trivially
re-runnable, which is what the brief prefers.

### Decision 3: Cover resolution — fetch Large ("L") once, size with CSS

Fetch only the `-L` size (`https://covers.openlibrary.org/b/id/{cover_i}-L.jpg`,
typically ~330–500 px wide) and store that single file. The index thumbnail
(~72–90 px wide) and the detail-page cover (~180–220 px wide) both render from
the same file via CSS `width`/`aspect-ratio`/`object-fit: cover`.

Rejected alternative — storing M for index + L for detail: doubles files,
doubles requests to Open Library, complicates copy/backfill/force logic, and
buys nothing at these display sizes (an L file downscaled looks identical to M
at 90 px and the page count is 21). One file per slug keeps every code path
(`add`, `covers`, `write_site`, placeholder fallback) keyed on a single
existence check.

### Decision 4: Cover resolution at render time — NO `BookPage.cover_path` field

Do **not** add a cover field to the `BookPage` dataclass in
`src/gttp/models.py`, and do not plumb cover paths through
`src/gttp/pipeline.py`. Instead, `publish.py` resolves a cover at render time
by checking `covers/<slugify(page.title)>.jpg` existence.

Rationale: `BookPage`s are cached as JSON in `.cache/pages/<slug>.json` and
reloaded with `BookPage(**d)` (`src/gttp/cache.py:load_cached_page`). If the
cover path were baked into the cached page, running `gttp covers` after a
build would *not* surface the new covers until every page happened to be
rebuilt — a real staleness bug, and the exact workflow the backfill command
exists for (backfill, then rebuild offline). Filesystem-existence-at-render is
always current, needs zero model/cache/pipeline changes, and keeps old cached
JSON loading without migration. The brief guessed a `cover_path` field would
be needed; this plan deliberately deviates, with the above as the reason.

### Decision 5: New module `src/gttp/covers.py` for fetch + resolution

All Open Library logic goes in one new module rather than into `config.py`
(which is settings/catalog only, per its docstring). Public surface:

- `COVERS_DIR = ROOT / "covers"` (import `ROOT` from `.config`)
- `cover_file(slug: str, covers_dir: Path = COVERS_DIR) -> Path | None` —
  returns the path if `covers_dir / f"{slug}.jpg"` exists, else `None`.
- `fetch_cover(title: str, author: str | None, slug: str, covers_dir: Path = COVERS_DIR, timeout: float = 15.0) -> Path | None` —
  resolve via Open Library Search API, download the L image, write
  `covers_dir/<slug>.jpg`, return the path; return `None` on any miss/failure
  (never raise out of this function for network/HTTP/parse errors).

### Decision 6: Placeholder — deterministic inline SVG, no files

When `cover_file(slug)` is `None`, render an inline `<svg>` tile in the HTML
(both index card and detail page) with the same box dimensions/classes as a
real cover: the book title's first alphanumeric character, over a background
color derived deterministically from the title. **Determinism gotcha:** use
`hashlib.md5(title.encode()).hexdigest()` to derive the hue — never Python's
built-in `hash()`, which is randomized per process (`PYTHONHASHSEED`) and
would break "deterministic offline build" and any snapshot-style assertions.
Suggested formula: `hue = int(md5hex[:6], 16) % 360`, background
`hsl({hue} 45% 38%)`, white letter, and keep contrast fixed by construction
(fixed S/L, only H varies). Inline SVG means zero extra files, works offline,
and inherits nothing from network state.

### Open Library API usage (the only network surface added)

One search request per book, at `add`/`covers` time only — never during
`build`:

1. `GET https://openlibrary.org/search.json` with params
   `{"title": <title>, "author": <author or omit>, "limit": 5, "fields": "key,title,author_name,cover_i"}`.
   Take the first doc with a truthy `cover_i`. (Passing `author` when known
   dramatically improves match quality for generic titles like "Influence",
   "Boundaries", "Mindset", "Grit" — all present in `books.yaml`.)
2. `GET https://covers.openlibrary.org/b/id/{cover_i}-L.jpg?default=false` —
   `default=false` makes Open Library return HTTP 404 instead of a 1×1
   transparent placeholder when the image is missing. Treat non-200, or a
   body under ~1 KB, as a miss.

Both requests: `requests.get(..., timeout=15)` with a real User-Agent header,
e.g. `{"User-Agent": "gttp/0.1 (+https://github.com/ericfaris/gttp)"}` —
Open Library asks for identifying UAs. In the backfill loop, `time.sleep(1)`
between books (the repo already has this politeness pattern:
`_RedditSearchBase.REQUEST_INTERVAL` in `src/gttp/reddit_client.py`).

### Visual redesign scope (within `_HTML_SHELL` + card/detail markup)

Stay in the existing architecture: one Python format-string HTML shell, inline
CSS, tiny inline JS for search. **Format-string gotcha:** `_HTML_SHELL` is
rendered with `.format(...)`, so every literal `{`/`}` in CSS (and any inline
SVG placed inside the shell template itself) must be doubled (`{{`, `}}`).
Placeholder SVG built in normal f-strings in the card/detail render functions
is fine — only text inside `_HTML_SHELL` needs doubling.

Design direction (executor has latitude on exact values, not on structure):

- **Index cards**: convert each `<li class="card">` to a horizontal layout
  (CSS grid or flex): cover thumbnail (~4.5rem wide, `aspect-ratio: 2/3`,
  `object-fit: cover`, subtle border-radius + border) on the left; title,
  author, core idea, tag pills on the right. Keep `<ul class="books">` and
  `li` structure so the existing search JS (`document.querySelectorAll('.books li')`)
  keeps working. **Preserve exactly** `class="card"` / `class="card empty"`
  and the `data-search` attribute — tests assert on them.
- **Detail pages**: a header block with the larger cover (~11–13rem wide,
  2/3 aspect) beside the `<h1>`/author/core-idea blockquote (grid with a
  single-column collapse under ~30rem via a media query). Keep the sticky
  `.back` link, `.toc`, `#idea-N` anchors, quotes, sources sections untouched
  in structure.
- **Typography/color/spacing pass**: keep `color-scheme: light dark` (hard
  requirement), keep the system font stack (no webfonts — no network at view
  time), refine the existing `#e0703c` accent usage, card hover affordance,
  slightly wider `main` if the card grid needs it (e.g. 46rem), consistent
  vertical rhythm. Keep `color-mix(...currentColor...)` for theme-neutral
  borders (existing pattern).
- **Images**: `loading="lazy"` and explicit `width`/`height` (or CSS
  `aspect-ratio`) on `<img>` to avoid layout shift; `alt="Cover of <title>"`;
  all attribute values through `html.escape` as the file already does.

## Step-by-step tasks

Each step is independently verifiable; run the listed check before moving on.

### 1. Create `src/gttp/covers.py` (new file)

Implement per Decisions 5–6 above:

- `COVERS_DIR = ROOT / "covers"` (from `.config import ROOT`).
- `cover_file(slug, covers_dir=COVERS_DIR) -> Path | None`.
- `_search_cover_id(title, author, timeout) -> int | None` — Open Library
  search call; return `None` on any exception/non-200/no-match.
- `fetch_cover(title, author, slug, covers_dir=COVERS_DIR, timeout=15.0) -> Path | None` —
  calls `_search_cover_id`, downloads `-L.jpg?default=false`, validates
  status 200 and `len(resp.content) > 1024`, `covers_dir.mkdir(parents=True,
  exist_ok=True)`, writes bytes, returns path; `None` otherwise. Catch
  `requests.RequestException` (and `ValueError` from `.json()`) — never let a
  cover failure raise.
- `placeholder_svg(title, css_class) -> str` — deterministic md5-hue inline
  SVG (see Decision 6); include `role="img"` and an `aria-label` with the
  title. Escape the letter/label with `html.escape`.

Verify: `.venv/bin/python -c "from gttp.covers import placeholder_svg; print(placeholder_svg('Atomic Habits','cover'))"` — same output on repeated runs
(run twice; must be byte-identical, proving no `hash()` use).

### 2. Wire cover fetch into `gttp add` (`src/gttp/cli.py`)

In the `args.command == "add"` branch (currently `src/gttp/cli.py:44-49`):
after a successful `add_book(...)`, call
`fetch_cover(args.title, args.author, slugify(args.title))` and print either
`Fetched cover -> covers/<slug>.jpg` or
`No cover found (run 'gttp covers' later to retry).` Failure must not change
the exit code or undo the catalog append — the catalog write already happened
in `add_book` (`src/gttp/config.py:78-90`), which stays untouched. Import
`slugify` from `.config` and `fetch_cover` from `.covers`.

Do **not** modify `src/gttp/config.py:add_book` — keeping fetch out of
`config.py` preserves its "no side effects beyond YAML" role and keeps the
existing `add_book` tests/behavior intact.

Verify: `.venv/bin/gttp add "Zzz Test Book" --author "Nobody Realname"` on a
throwaway branch or with `git checkout books.yaml` afterward — confirm the
catalog message still prints and a cover line (found or not-found) follows;
`git checkout books.yaml && rm -f covers/zzz-test-book.jpg` to clean up.
(If offline while developing, it should print the not-found line and exit 0.)

### 3. Add the `gttp covers` backfill subcommand (`src/gttp/cli.py`)

- Register `p_covers = sub.add_parser("covers", help="fetch missing cover images from Open Library")`
  with `--force` (`action="store_true"`, help: re-fetch even if a cover file
  already exists).
- Handler: `for book in load_catalog():` skip when
  `cover_file(book.slug)` exists and not `--force`; else
  `fetch_cover(book.title, book.author, book.slug)`, print one status line
  per book (`ok` / `no cover found` / `skipped (exists)`), `time.sleep(1)`
  between actual fetches (not between skips). Print a final summary
  (`Fetched X, missing Y, skipped Z.`). Return 0.

Verify: `.venv/bin/gttp covers` twice — first run fetches (network required;
expect most of the 20 books to resolve), second run prints all-skipped and
makes zero network calls (fast). `ls covers/` shows `<slug>.jpg` files.

### 4. Publish-side cover resolution + copy into `site/covers/` (`src/gttp/publish.py`)

In `write_site` (`src/gttp/publish.py:52-67`):

- Add parameter `covers_dir: Path = None` defaulting to
  `covers.COVERS_DIR` (use a `None` sentinel or import default — keep the
  existing `write_site(pages, site_dir)` call in `pipeline.py:100` working
  unchanged; tests call `write_site([...], tmp_path)` positionally).
- For each page whose `covers_dir / f"{slug}.jpg"` exists, copy it
  (`shutil.copy2`) into `site_dir / "covers" / f"{slug}.jpg"` (mkdir first).
  Copy unconditionally/overwrite — covers can be re-fetched with `--force`.
- Pass cover presence into `_render_index_card` and `_render_book_html`
  (simplest: give both an extra `has_cover: bool` or `cover_href: str | None`
  argument computed in `write_site`/`_render_index_html`; note
  `_render_index_html` currently builds cards itself, so thread the
  `covers_dir` down or precompute a `{slug: bool}` map and pass it through).

Markup:

- Index card (`_render_index_card`, `src/gttp/publish.py:99-118`): inside the
  existing `<li>`, add a cover block before the text —
  `<img class="cover cover-thumb" src="covers/<slug>.jpg" alt="Cover of <title>" loading="lazy" width="72" height="108">`
  when present, else `placeholder_svg(title, "cover cover-thumb")`. Wrap
  text content in a `<div class="card-body">` for the flex/grid layout. Keep
  `class="{cls}"` (`card` / `card empty`) and `data-search` exactly as-is.
- Detail page (`_render_book_html`, `src/gttp/publish.py:157-202`): after the
  `.back` link, emit a `<div class="book-hero">` containing the cover
  (`src="../covers/<slug>.jpg"`, class `cover cover-detail`, or placeholder
  SVG) plus the existing `<h1>`, `.author`, and `blockquote.core` moved
  inside it. Everything after (`toc`, bullets, quotes, sources, `.gen`)
  unchanged.

Verify: `.venv/bin/gttp build --offline`, then open `site/index.html` — every
card shows either a real thumbnail or a lettered placeholder; `site/covers/`
contains copies of whatever exists in `covers/`; `grep -c "cover-thumb" site/index.html`
equals the catalog size (20).

### 5. Visual redesign of `_HTML_SHELL` CSS (`src/gttp/publish.py:205-249`)

Apply the design direction from "Visual redesign scope" above. Concretely:

- New rules: `.books li` as flex/grid row with gap; `.cover` base (border,
  radius, `object-fit: cover`, `aspect-ratio: 2/3`, background for
  letterboxing); `.cover-thumb` (~4.5rem wide, flex-shrink 0);
  `.cover-detail` (~12rem); `.book-hero` grid + mobile collapse media query;
  card hover state; refreshed heading sizes/letter-spacing; keep/refine
  existing `.search`, `.tag`, `.toc`, `blockquote.core`, `.back` (sticky),
  `.site-header`/`.site-footer` rules.
- **Must keep**: `:root {{ color-scheme: light dark; }}`, `background: Canvas`
  on `.back`, `color-mix` borders, system font stack, `html {{ scroll-behavior: smooth; }}`.
- **Remember**: double every literal CSS brace (`{{`/`}}`) — this template
  goes through `.format()`.

Verify: `.venv/bin/gttp build --offline`; open `site/index.html` and a detail
page (e.g. `site/books/atomic-habits.html`) in a browser; check dark mode
(OS toggle or DevTools emulation) — placeholders, borders, sticky back-link
all legible in both schemes; search box still filters; tag pills, TOC links,
`#idea-1` anchors still work.

### 6. Tests (`tests/test_pipeline.py` + new `tests/test_covers.py`)

New `tests/test_covers.py` (network fully mocked — monkeypatch
`gttp.covers.requests.get`):

- `test_fetch_cover_success(tmp_path, monkeypatch)` — fake search response
  `{"docs": [{"cover_i": 12345}]}` then fake image response (status 200,
  `content=b"\xff\xd8" + b"x"*2000`); assert file written to
  `tmp_path/"<slug>.jpg"` and returned path matches; assert the cover URL
  requested contains `12345-L.jpg` and `default=false`.
- `test_fetch_cover_no_match(tmp_path, monkeypatch)` — search returns
  `{"docs": []}`; assert `None`, no file, and no second HTTP call.
- `test_fetch_cover_network_error(tmp_path, monkeypatch)` — `requests.get`
  raises `requests.RequestException`; assert `None`, no raise.
- `test_fetch_cover_rejects_tiny_body(tmp_path, monkeypatch)` — image
  response body under 1 KB; assert `None`, no file.
- `test_cover_file_lookup(tmp_path)` — touch `tmp_path/"atomic-habits.jpg"`;
  `cover_file("atomic-habits", tmp_path)` returns it; missing slug → `None`.
- `test_placeholder_svg_deterministic` — two calls with the same title are
  identical strings; different titles yield different fill colors; output
  contains the escaped first letter and `aria-label`.
- `test_covers_cli_backfill(tmp_path, monkeypatch)` — monkeypatch
  `gttp.covers.COVERS_DIR` (and the CLI's view of it) to `tmp_path`, stub
  `fetch_cover` to record calls; run `gttp.cli.main(["covers"])`; assert it
  attempts every catalog book lacking a file and skips existing ones; with
  `--force` it attempts all. Also monkeypatch `time.sleep` to a no-op.

Extend `tests/test_write_site_html_features` (or add a sibling test) in
`tests/test_pipeline.py:118-157`:

- Call `write_site([...], tmp_path, covers_dir=<tmp covers dir>)` with a
  dummy `atomic-habits.jpg` present (a few KB of bytes) and nothing for
  "Meditations". Assert: `site covers copy exists`
  (`tmp_path/"covers"/"atomic-habits.jpg"`), index contains
  `covers/atomic-habits.jpg` and `loading="lazy"`, the Meditations card
  contains `<svg` (placeholder), detail page for atomic-habits references
  `../covers/atomic-habits.jpg`, Meditations detail page contains `<svg`.
- Keep every existing assertion in that test passing unmodified — they pin
  the feature-preservation acceptance criterion (`id="q"`, `data-search`,
  `class="card empty"`, ordering, `r/getdisciplined`, `id="idea-1"`,
  `class="toc"`, `href="#idea-1"`, `site-header`/`site-footer`, Markdown
  byte-identical to `render_markdown`).

Verify: `.venv/bin/pytest` — full suite green (`tests/test_pipeline.py`,
`tests/test_reddit_parsing.py`, `tests/test_covers.py`).

### 7. Backfill the real catalog + README note

- Run `.venv/bin/gttp covers` for real (network). Expect most of the 20
  books in `books.yaml` to resolve; a few may legitimately miss (e.g. very
  new titles like "Tiny Experiments", 2025). Misses are fine — placeholders
  cover them. Re-run once for transient failures.
- Sanity-check the fetched images are actual covers (open a couple; sizes
  should be tens of KB, not 1–2 KB).
- Add a short README section ("Covers") documenting: fetched from Open
  Library at `gttp add` time, `gttp covers [--force]` to backfill/refresh,
  stored committed in `covers/`, placeholders when absent, `--offline`
  builds never touch the network.
- Commit `covers/*.jpg` with the code (git-tracked by default; `.gitignore`
  needs no change — `covers/` is not matched by any existing pattern).

Verify: `.venv/bin/gttp build --offline` renders real thumbnails for fetched
books and placeholders for misses; `git status` shows `covers/` files staged
alongside source changes.

## Data/model/API changes

- **`BookPage` (`src/gttp/models.py`)** — deliberately unchanged (Decision 4).
  No cache migration needed; `.cache/pages/*.json` keeps loading via
  `BookPage(**d)`.
- **`Book` (`src/gttp/config.py`)** — unchanged; `Book.slug` already exists
  and is the cover key.
- **`books.yaml`** — unchanged schema; no cover fields (covers keyed by slug
  on disk).
- **New module `src/gttp/covers.py`** — `COVERS_DIR`, `cover_file`,
  `fetch_cover`, `placeholder_svg` (signatures in Step 1).
- **`write_site` signature** — gains optional `covers_dir` parameter;
  existing positional calls (`pipeline.py:100`, tests) unaffected.
- **CLI** — new `covers` subcommand with `--force`; `add` gains post-append
  fetch behavior (no new flags).
- **External API calls** (add/backfill time only, never build):
  - `GET https://openlibrary.org/search.json?title=…&author=…&limit=5&fields=key,title,author_name,cover_i`
  - `GET https://covers.openlibrary.org/b/id/{cover_i}-L.jpg?default=false`
  - No API key; identifying User-Agent; 15 s timeouts; 1 s spacing in backfill.
- **New on-disk layout**: `covers/<slug>.jpg` (committed source of truth);
  `site/covers/<slug>.jpg` (regenerated copy, inside gitignored `site/`).

## Testing & verification (acceptance criteria → proof)

Run everything with the venv binaries.

1. **`gttp add` fetches a cover without changing catalog behavior** —
   Step 2 manual check plus mocked-`requests` unit tests in
   `tests/test_covers.py`; catalog-append behavior pinned by existing
   `add_book` semantics (untouched) and the CLI still printing the original
   messages. Command: `.venv/bin/gttp add "Zzz Test" --author "X"` (then
   `git checkout books.yaml; rm -f covers/zzz-test.jpg`).
2. **Backfill for the existing 20 books** — `.venv/bin/gttp covers` (real
   network, Step 7); idempotency proven by a second run reporting all
   skipped; logic unit-tested with a stubbed `fetch_cover`.
3. **`--offline` stays zero-network with placeholders** — no code path in
   `build` touches `covers.fetch_cover` (fetching exists only in the `add`
   and `covers` CLI branches — verify by reading the diff:
   `grep -rn "fetch_cover" src/` must show hits only in `cli.py` and
   `covers.py`). Functional proof: `.venv/bin/gttp build --offline` with
   network disabled or simply by construction; placeholder rendering asserted
   in the extended `test_write_site_html_features`.
4. **Online build renders thumbnails + detail covers** — covered by the
   `write_site` test with a dummy cover file (both index `covers/<slug>.jpg`
   reference and detail `../covers/<slug>.jpg` reference asserted), plus
   manual browser check after Step 7's real backfill.
5. **No feature regressions (search, tags, TOC, sticky back, color-scheme)** —
   all pre-existing assertions in `tests/test_pipeline.py::test_write_site_html_features`
   must pass unmodified; manual dark/light + search-filter check in Step 5.
6. **Existing tests keep passing; new coverage added** —
   `.venv/bin/pytest` full-suite green including new `tests/test_covers.py`.

Final gate: `.venv/bin/pytest && .venv/bin/gttp build --offline` both clean,
then eyeball `site/index.html` and two detail pages (one with a real cover,
one placeholder) in light and dark mode.

## Risks & watch-outs

- **`_HTML_SHELL` uses `.format()`** — every literal `{`/`}` added to the CSS
  (or any SVG placed in the shell) must be doubled or the build crashes with
  `KeyError`/`IndexError` at render time. This is the most likely silent
  trip-up in Step 5.
- **Placeholder determinism** — must use `hashlib`, never `hash()`
  (PYTHONHASHSEED randomization would make builds non-deterministic and the
  determinism test flaky-by-design if written naively).
- **Do not bake cover paths into `BookPage`/cache** — see Decision 4; doing
  so reintroduces the staleness bug where backfilled covers don't appear
  until pages are rebuilt.
- **CI publishes from a fresh checkout with `gttp build --offline`**
  (`.github/workflows/build.yml`) — covers only reach production because
  `covers/` is committed and `write_site` copies it into `site/`. If the copy
  step is skipped, local builds will look right (stale `site/covers/` from a
  prior run) while CI ships broken image links. The `write_site` test's
  copy assertion guards this.
- **Test-order trap in `test_write_site_html_features`** — it asserts the
  summary card sorts before the empty card and that specific class strings
  exist. When restructuring card markup, keep `class="{cls}"` emitting
  exactly `card` / `card empty` (no extra classes prepended before `card`
  in a way that breaks the `class="card empty"` substring assertion).
- **Open Library reliability** — search occasionally times out or returns
  odd matches; the fetch must be best-effort (`None`, never raise), `add`
  must succeed regardless, and `covers` must continue past failures. Wrong
  cover for ambiguous titles is possible; passing `author` mitigates it, and
  `gttp covers --force` after fixing is the recovery path (per-book manual
  override is explicitly out of scope).
- **Rate limiting/politeness** — one search + one image request per book,
  1 s spacing in backfill, identifying User-Agent. Never fetch during
  `build`.
- **Image validation** — without `?default=false` + the >1 KB body check,
  Open Library can hand back a blank 1×1 GIF that would render as a broken
  gray box forever (and block re-fetch since the file "exists").
- **Relative URLs differ by page depth** — index references
  `covers/<slug>.jpg`, detail pages `../covers/<slug>.jpg` (same pattern as
  the existing `_render_header(root)` / `_render_footer(root)` calls with
  `""` vs `"../"`).
- **`site/` is gitignored** — don't try to commit `site/covers/`; commit
  `covers/` only.
- **Dockerized deploy** (see `Dockerfile`/`entrypoint.sh`, container on
  :8100 per repo memory) builds from the repo contents — committed `covers/`
  flows in automatically; no Docker changes needed, but don't add network
  calls to the build path or the container's offline-ish flow could hang.

## Out of scope (do not build)

- Any cover source other than Open Library (no Google Books, no manual
  local-only import mode).
- Server-side rendering, JS frameworks, webfonts, or build tooling
  (Webpack/Vite etc.) — the site stays a Python script emitting static HTML
  with inline CSS and the existing tiny inline search script.
- Changes to the Reddit-fetch/ranking/synthesis pipeline stages
  (`reddit_client.py`, `ranking.py`, `synthesize.py`, and the fetch logic in
  `pipeline.py` are untouched except that `write_site`'s output benefits).
- Per-book cover editing/override UI or YAML cover fields — covers are
  programmatic; `gttp covers --force` is the only refresh mechanism.
- New runtime dependencies — `requests` (already in `pyproject.toml`) is the
  only HTTP client used; no Pillow/image processing (store the JPEG bytes
  as-is).
- Responsive `srcset`/multi-resolution image variants — single L-size file
  per book (Decision 3).
