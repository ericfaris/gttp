# Implementation plan — gttp browsing polish (HTML only)

Executor context: you have this plan and the repo at `/home/eric/projects/gttp`.
The concept brief is at `.claude/plans/browsing-polish-brief.md` (read it too).
All work happens in `src/gttp/publish.py` plus one small test addition in
`tests/test_pipeline.py`, then a rebuild of `site/`.

## Summary

gttp generates a static site (`site/index.html` + `site/books/*.html`) that is
live at https://gttp.mooseflip.com (a Cloudflare tunnel serves the repo's
`site/` dir from localhost:8100 — do not touch any of that). The site works but
is bare: the index is a flat `<ul>`, 17 of 20 books currently say "No
qualifying Reddit summaries found yet." and clutter the top-level list, and
book pages have no in-page navigation. This task upgrades the HTML rendering in
`src/gttp/publish.py` with four features — client-side instant search on the
index, redesigned index cards (blurb line, subreddit tag, dim + sort-to-bottom
for no-summary books), a per-idea anchored jump list on book pages, and a
shared header/footer across all pages — then regenerates `site/` with
`gttp build --offline` so the live site picks the changes up automatically.
Stdlib only, all CSS/JS inline, `.md` output byte-identical to today.

## Current code, exactly as it stands (read before changing)

`src/gttp/publish.py` (141 lines) has:

- `render_markdown(page: BookPage) -> str` — **do not modify.** The `.md`
  output must not change.
- `write_site(pages, site_dir=SITE_DIR) -> Path` — loops pages, writes
  `books/{slug}.md` + `books/{slug}.html` via `_render_book_html`, then writes
  `index.html` via `_render_index_html`. Called from
  `src/gttp/pipeline.py:100` as `write_site(ordered)` (catalog order). Keep the
  signature and the fact that it returns the index path.
- `_render_index_html(pages)` — builds `<li>` strings in a generator, wraps in
  `<ul class='books'>`, formats into `_HTML_SHELL`.
- `_render_book_html(page)` — appends HTML fragments to a `parts` list
  (back link, `<h1>`, `.author`, `<blockquote class='core'>`, bullets `<ul>`,
  honest take, quotes, sources, `.gen` line) and formats into `_HTML_SHELL`.
- `_HTML_SHELL` — a module-level `str` used with `.format(title=..., body=...)`.
  **Because it goes through `str.format`, every literal `{`/`}` in its CSS is
  doubled (`{{ }}`).** Any JS you add to it must also double its braces, or you
  must restructure to avoid `.format` (see decision below).

Data model (`src/gttp/models.py`): `BookPage` has `title`, `author`
(`str | None`), `core_idea`, `bullets: list[str]`, `honest_take`,
`quotes: list[dict]` (`text`/`author`/`url`), `sources: list[dict]`
(`title`/`url`/`subreddit`/`score`), `generated_by` (`"heuristic"`, `"claude"`,
or `"error"`). **No model changes are needed.**

"No summaries" books: `src/gttp/synthesize.py:52-61` returns a `BookPage` with
`core_idea="No qualifying Reddit summaries found yet."`, `bullets=[]`,
`sources=[]`, `honest_take=""`, `quotes=[]`. There is also an error-placeholder
page (`generated_by == "error"`, see `tests/test_pipeline.py::
test_build_book_isolates_errors`) which likewise has no bullets/sources.

Current state of the corpus (useful for verification): exactly three books
have bullets/sources — `site/books/atomic-habits.html`,
`site/books/deep-work.html`,
`site/books/the-7-habits-of-highly-effective-people.html`. The other 17 are
no-summary placeholders.

Style baseline to preserve (from `_HTML_SHELL`): system font stack
(`-apple-system, system-ui, sans-serif`), 17px/1.6, `max-width: 42rem`
centered column, accent `#e0703c`, muted `#888`/`#aaa`,
`:root { color-scheme: light dark; }`. Enhance this; do not swap palettes,
fonts, or layout philosophy. Because `color-scheme: light dark` is relied on
with mostly-default colors, any new backgrounds you introduce (tags, search
input) must look right in both schemes — prefer `color-mix(...)` with
`currentColor`, borders, or opacity over hard-coded light-only hex
backgrounds.

## Approach & key decisions

### Shared helpers first (feature 4 underpins 1–3)

**Decision: keep `_HTML_SHELL` + `.format`, extend it with `{header}`,
`{footer}`, and `{script}` slots.** Rejected: switching to `string.Template`
or f-strings — a bigger diff for no benefit; the only cost of `.format` is
doubling braces in new CSS, and the search JS goes in through the `{script}`
slot as an already-built plain string so its braces never pass through
`.format`.

Add to `publish.py`:

- `_render_header(root: str) -> str` — returns
  `<header class='site-header'><a class='site-title' href='{root}index.html'>gttp<span> — Get To The Point</span></a></header>`
  (exact markup up to you, but: site title always links to the index; `root`
  is `""` on the index page and `"../"` on book pages so links work from
  `file://`).
- `_render_footer() -> str` — a `<footer class='site-footer'>` with a short
  line, e.g. "Crowd-vetted self-help summaries, curated from Reddit." plus a
  link back to the index. Keep it one or two lines, muted color.
- Update `_HTML_SHELL` to
  `...<body>{header}<main>{body}</main>{footer}{script}</body></html>` and add
  the small amount of CSS for `.site-header`, `.site-title`, `.site-footer`,
  and the new card/tag/search/toc classes (all doubled-brace). Both
  `_render_index_html` and `_render_book_html` then call
  `.format(title=..., header=_render_header(root), body=..., footer=_render_footer(), script=...)`
  with `script=""` for book pages.

The existing `<h1>Get To The Point</h1>` on the index becomes redundant with a
header that says the site name — keep a single `<h1>` on the index (either
drop the old one and make the header's title the visual anchor, or keep the
`h1` and make the header compact; pick one, don't render the site name twice
at full size). On book pages the header is compact and the book title stays
the `<h1>`.

The old `<p class='back'><a href='../index.html'>← all books</a></p>` at the
top of `_render_book_html` stays (it satisfies acceptance criterion 4's
"← All books" link), but make it sticky-ish and clearly visible: simplest
robust option is `position: sticky; top: 0` on `.back` with a background of
`Canvas` (the CSS system color, correct in both light and dark) — or skip
sticky and just keep it prominent at the top; sticky is nice-to-have, the
working link is the requirement.

### Feature 1 — instant search/filter (index only)

**Decision: no JSON index, no framework — filter the DOM directly using a
lowercased `data-search` attribute on each `<li>`.** Each card `<li>` gets
`data-search="{title} {author} {core_idea}"` (lowercased in Python,
`html.escape`d — note `html.escape` escapes `"` by default, which is what
protects the attribute). The inline script grabs
`document.querySelectorAll('.books li')` once, and on `input` sets
`li.hidden = !li.dataset.search.includes(query.toLowerCase())`. Clearing the
box unhides everything (empty string is `includes`-true for all). Rejected:
building a JS array of book objects in a `<script type="application/json">`
blob — more escaping surface (`</script>` breakout) for zero functional gain
at 20 books; the data-attribute approach has exactly one escaping mechanism
(`html.escape`) already used everywhere in this file.

Markup: an `<input type="search" id="q" class="search" placeholder="Search books…" autocomplete="off" aria-label="Search books">`
between the intro line and the `<ul class='books'>`. Also render a hidden
"no matches" element (`<p id="no-results" hidden>No books match.</p>`) the
script toggles when every li is hidden — cheap and makes the feature feel
finished.

The script is a single `<script>…</script>` string built in
`_render_index_html` and passed as the `script` format arg (so no brace
doubling). Keep it ~15 lines, no dependencies, works from `file://`. Guard
with a trivial existence check (`var q=document.getElementById('q'); if(q){...}`)
so book pages (script="") and any future reuse never error.

### Feature 2 — redesigned index cards

**Detection of "no summaries": `has_summary = bool(page.bullets or page.sources)`.**
Do **not** string-match `core_idea == "No qualifying..."` — that couples
publish.py to synthesize.py's copy and misses the `generated_by == "error"`
placeholder, which also has empty bullets/sources and should be deprioritized
the same way.

**Ordering: sort inside `_render_index_html` only** —
`ordered = sorted(pages, key=lambda p: not (p.bullets or p.sources))` —
`sorted` is stable so catalog order is preserved within each group. Do not
reorder in `write_site` or `pipeline.py`; the index is a *view*, and the
brief forbids pipeline changes.

**Card markup** (replace the current one-line `<li>` f-string; a small helper
`_render_index_card(page, has_summary) -> str` keeps `_render_index_html`
readable):

- `<li class="card">` for summary books, `<li class="card empty">` for
  no-summary books. `.empty { opacity: .55; }` (or similar) dims them;
  keep them clickable/searchable — dim, don't hide.
- Title (+ author) link as today: `<a href="books/{slug}.html">`.
- Blurb line: `page.core_idea` for summary books. For no-summary books the
  core_idea *is* the "No qualifying Reddit summaries found yet." sentence —
  render it, but in the muted style (it doubles as the explanation for why
  the card is dim). Optionally shorten to "No summaries yet." — either is
  fine; don't invent new copy beyond that.
- **Subreddit tag:** derive from `page.sources` — dedupe preserving order:
  `subs = list(dict.fromkeys(s["subreddit"] for s in page.sources))`. Render
  each as `<span class="tag">r/{sub}</span>` after the blurb (cap at 2–3 tags
  to keep cards tidy). Books with no sources get no tag — the brief's decided
  answer, do not invent topic tags from anywhere else. Tag CSS: small
  (`font-size: .75rem`), pill-ish (border + border-radius or a
  `color-mix`-based background), muted — must read fine in dark mode.
- The whole card carries the `data-search` attribute from feature 1.

An optional single divider (`<p class="pending-label">` or a subtle `<hr>`)
between the two groups is allowed but not required; if you add one, make sure
the search script also hides it when filtering (simplest: give it a class the
script toggles alongside, or just leave it out — leaving it out is the safer
default).

### Feature 3 — per-idea navigation (book pages)

In `_render_book_html`, when `page.bullets` is non-empty:

- Give each bullet `<li id="idea-{i}">` (1-based).
- Render a jump list between the core-idea blockquote and the
  "The 5-bullet version" heading: `<nav class="toc" aria-label="Ideas">` with
  an `<ol>` of `<a href="#idea-{i}">` links. Link text: a truncated form of
  the bullet — first ~60 chars cut at a word boundary with an ellipsis
  (a tiny module-level helper `_truncate(text: str, limit: int = 60) -> str`;
  escape *after* truncating, i.e. truncate the raw text then `html.escape`
  the result, so you never slice an entity like `&#x27;` in half).
- Add `html {{ scroll-behavior: smooth; }}` and a small
  `li[id^='idea-'] {{ scroll-margin-top: 3rem; }}` so anchors don't land
  under the sticky back link.
- Books with no bullets get no toc — no empty `<nav>`.

Rejected: numbering-only links ("Idea 1…Idea 5") — truncated text is far more
useful for navigation and the data is right there. Rejected: JS-driven
scrollspy — out of scope, plain anchors satisfy the criterion.

### Feature 4 — shared header/footer

Covered by the helpers above. The one subtlety is relative paths: index uses
`root=""`, book pages `root="../"`. Both `curl https://gttp.mooseflip.com` and
`file://…/site/index.html` must work, so never emit absolute (`/…`) URLs.

## Step-by-step tasks

All edits in `/home/eric/projects/gttp/src/gttp/publish.py` unless noted.

1. **Extend `_HTML_SHELL`**: add `{header}`, `{footer}`, `{script}` slots and
   wrap `{body}` in `<main>`; add new CSS rules (header, footer, `.card`,
   `.empty`, `.tag`, `.search`, `.toc`, sticky `.back`, `scroll-behavior`,
   `scroll-margin-top`) with **doubled braces**. Add `_render_header(root)`
   and `_render_footer()` helpers.
   Verify: `python -c "from gttp import publish; print(publish._HTML_SHELL.format(title='t', header='', body='', footer='', script=''))"`
   emits valid-looking HTML (no `KeyError`/`IndexError` from stray braces).
2. **Update `_render_book_html`** to pass the new format args
   (`header=_render_header("../")`, `footer=_render_footer()`, `script=""`).
   Verify: `pytest` still green; regenerate one page mentally or via step 8.
3. **Add per-idea nav** in `_render_book_html`: `_truncate` helper, `id`s on
   bullet `<li>`s, `<nav class='toc'>` before the bullets section, only when
   `page.bullets`.
   Verify: after rebuild, `grep -c 'idea-' site/books/deep-work.html` > 0 and
   `grep 'idea-' site/books/meditations.html` finds nothing.
4. **Add `_render_index_card(page) -> str`** implementing the card markup:
   `has_summary` check, `class="card"`/`"card empty"`, blurb, deduped
   subreddit tags, `data-search` attribute (lowercased, escaped).
5. **Rewrite `_render_index_html`**: stable-sort pages (summary-first), map
   through `_render_index_card`, insert the search `<input>` + hidden
   no-results element above the list, build the search `<script>` string,
   pass everything through the extended `_HTML_SHELL.format(...)` with
   `root=""` header.
6. **Do not touch** `render_markdown`, `write_site`'s signature/behavior,
   `models.py`, `config.py`, `cli.py`, or anything under
   `pipeline.py`/`ranking.py`/`synthesize.py`/`reddit_client.py`.
7. **Add cheap test assertions** in
   `/home/eric/projects/gttp/tests/test_pipeline.py` (new test, don't change
   existing ones): call `write_site([...], tmp_path)` with two `BookPage`s —
   one with bullets/sources, one no-summary placeholder — and assert the
   index HTML contains `id="q"` (search box), `data-search`, that the empty
   book's card has the `empty` class, and that the summary book sorts first
   (compare `str.index` of the two titles). Assert the book page for the
   bullet book contains `id="idea-1"` and the header markup. Also assert the
   `.md` file content still equals `render_markdown(page)` (guards the
   "md unchanged" constraint). Construct `BookPage`s directly — no network,
   mirrors `test_build_book_offline_produces_page`'s style.
8. **Rebuild the site**: from the repo root run `gttp build --offline`
   (installed entry point; `python -m gttp.cli build --offline` also works).
   This rewrites `site/index.html` and all `site/books/*` — the live site
   updates automatically because the tunnel serves this directory.
9. **Verify** (next section), then commit. Per the user's global rules, do
   **not** add a `Co-Authored-By: Claude` line to the commit message. Commit
   both the `src`/`tests` changes and the regenerated `site/` output (the
   `site/` dir is tracked — it *is* the deployment).

## Data/model/API changes

None. `BookPage`, `write_site(pages, site_dir)`, `render_markdown(page)`, and
the CLI are unchanged. New code is private helpers inside `publish.py`
(`_render_header`, `_render_footer`, `_render_index_card`, `_truncate`) plus
new format keys on the module-private `_HTML_SHELL`.

## Testing & verification (maps to the brief's 7 acceptance criteria)

1. **Build succeeds**: `cd /home/eric/projects/gttp && gttp build --offline`
   exits 0, no tracebacks; `git status` shows `site/index.html` and
   `site/books/*.html` modified but **no `.md` diffs**
   (`git diff --stat site/books/*.md` must be empty — this is the md-unchanged
   proof).
2. **Search**: open `site/index.html` in a browser (`file://` path). Type
   "newport" → only Deep Work remains; type "james clear" → only Atomic
   Habits; type "qualifying" → the 17 placeholder books remain (blurb text is
   searched); clear the box → all 20 visible; type "zzzz" → the no-results
   message shows. If a browser check is inconvenient, the webapp-testing
   skill / Playwright against the `file://` URL works; at minimum
   `grep -c 'data-search' site/index.html` → 20 and
   `grep 'id="q"' site/index.html` confirm the moving parts exist, but do
   drive it in a browser once — criterion 2 is behavioral.
3. **Index cards**: in `site/index.html`, `grep -c "card empty"` → 17
   (today's corpus), and the three summary books
   (Atomic Habits, Deep Work, The 7 Habits…) appear before any `card empty`
   li (visual check or `python -c` with `str.index`). Tags:
   `grep -o 'r/[A-Za-z]*' site/index.html` shows subreddits only on the three
   summary books.
4. **Per-idea nav**: `site/books/deep-work.html`,
   `site/books/atomic-habits.html`, and
   `site/books/the-7-habits-of-highly-effective-people.html` are the only
   pages with bullets — check each contains `class="toc"` (or class='toc';
   match your quoting) and `href="#idea-1"`, and that clicking a toc link in
   a browser scrolls to the bullet. `grep -L toc site/books/*.html` should
   list all 17 placeholder pages. Every book page contains the
   `← all books` link (`grep -L 'all books' site/books/*.html` → empty).
5. **Header/footer**: `grep -L site-header site/books/*.html site/index.html`
   → empty; same for `site-footer`. Click the header title on a book page →
   lands on the index (relative link check).
6. **Tests**: `cd /home/eric/projects/gttp && python -m pytest` — all pass,
   including the new `write_site` test. Existing tests must not be modified
   in intent.
7. **Live check**: after the rebuild (no deploy step exists or is needed):
   `curl -sS https://gttp.mooseflip.com | grep -c 'id="q"'` → ≥1, and
   `curl -sS https://gttp.mooseflip.com/books/deep-work.html | grep -c 'idea-1'`
   → ≥1. If curl shows stale content, the local `site/` file and served file
   should be byte-identical (`curl -sS https://gttp.mooseflip.com | md5sum`
   vs `md5sum site/index.html`) — if they differ, you edited the wrong dir;
   the tunnel serves `/home/eric/projects/gttp/site`. Do not touch tunnel/DNS
   config under any circumstances.

## Risks & watch-outs

- **`str.format` brace-doubling**: every literal `{`/`}` added to
  `_HTML_SHELL` (CSS) must be doubled. The search JS is full of braces —
  that's why it's injected via the `{script}` argument as a prebuilt string,
  never embedded literally in `_HTML_SHELL`. If you see
  `KeyError: ' color-scheme'`-style errors, you missed a doubling.
- **Escaping**: everything user/Reddit-derived (`title`, `author`,
  `core_idea`, bullets, source titles/subreddits) goes through
  `html.escape(...)` exactly as the existing code does — including inside the
  `data-search` attribute (html.escape escapes `"` by default, keeping the
  attribute safe). Never place raw page text inside the `<script>` body; the
  script reads data only from the DOM (`dataset.search`), which sidesteps
  `</script>`-breakout entirely.
- **Truncation vs escaping order** in the toc: truncate the raw bullet text
  first, then escape — slicing escaped text can cut an entity in half.
- **`.md` output is sacred**: `render_markdown` untouched; verify with
  `git diff site/books/*.md` being empty after rebuild.
- **Sorting**: sort only the index *view* (inside `_render_index_html`,
  stable sort). `write_site`'s file-writing loop and `pipeline.py`'s
  `ordered` list stay in their current order.
- **Dark mode**: the site relies on `color-scheme: light dark` with default
  colors. New tag/search/header backgrounds must not assume white — use
  borders, `Canvas`/`CanvasText` system colors, `color-mix` with
  `currentColor`, or opacity.
- **Empty/edge pages**: books with no bullets must render no toc; books with
  no sources no tags; error-placeholder pages (`generated_by == "error"`)
  are treated like no-summary pages by the `bullets or sources` check.
  `author` can be `None` — the current code already guards this; keep the
  guards in the new card helper.
- **Aesthetic drift**: this is a polish pass — same font, column width,
  accent `#e0703c`, quiet grays. If a change makes the page look like a
  different site, it's too much.
- **The `Skill` note in publish.py's docstring about Hugo/Jekyll**: ignore;
  no SSG is being introduced.

## Out of scope (restated from the brief — do not build)

- Any Cloudflare/tunnel/DNS/hosting work. The tunnel → localhost:8100 →
  `site/` setup exists and works; do not touch `~/.cloudflared/`, the server,
  or DNS. No deploy step is needed — rebuilding `site/` *is* the deploy.
- Changes to the Reddit pipeline, ranking, or synthesis (`pipeline.py`,
  `ranking.py`, `synthesize.py`, `reddit_client.py`).
- A `gttp deploy` or `gttp serve` subcommand.
- Full-text search (title + author + blurb only).
- Tags beyond subreddits already present in `page.sources`.
- JS frameworks, build tooling, external assets, CDNs, web fonts.
- Any change to the Markdown output format.
