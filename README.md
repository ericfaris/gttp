# gttp — Get To The Point

Curated, crowd-vetted summaries of self-help books, distilled from the best
Reddit threads about them.

Most self-help books are one idea stretched to 300 pages, and Reddit already
knows this — threads like *"Atomic Habits in 5 bullet points"* exist because
readers want exactly that. **gttp** finds the highest-signal Reddit summaries
for a book, ranks them, and synthesizes them into a single two-minute page:
the one core idea, the 5-bullet version, the honest take from the comments, and
links back to the source threads.

## How it works

```
search  ──▶  filter  ──▶  rank  ──▶  synthesize  ──▶  publish
(Reddit)     (quality)    (Claude)   (Claude)         (static site)
```

1. **search** — fan out queries across self-improvement subreddits for a book title.
2. **filter** — drop deleted/removed posts, low-score noise, and off-topic rants.
3. **rank** — Claude scores each thread on fidelity, actionability, and comprehensiveness; blended with the Reddit score.
4. **synthesize** — Claude merges the top threads into one opinionated "get to the point" page, quoting the sharpest lines verbatim (attributed).
5. **publish** — one Markdown file per book plus an index page, ready for any static site generator.

The whole thing is a batch job: no server, no database — a repo of Markdown
files and a script that adds a book on demand.

## Quick start

```bash
pip install -e .
playwright install chromium   # one-time: the browser used for live Reddit search
cp .env.example .env          # optional: add your Anthropic key

# Runs fully offline from bundled fixtures — no browser or keys needed:
gttp build --offline
open site/index.html          # or serve site/ however you like

# Live: fetch real Reddit threads and synthesize a book end-to-end:
gttp add "Deep Work"
gttp build
```

- **Offline mode** (`--offline`) reads Reddit threads from `fixtures/` and
  synthesizes pages with a deterministic heuristic — zero network calls, no
  browser. Use it to see the pipeline work before wiring up anything.
- **Online mode** fetches live Reddit threads through a real browser (see
  Configuration) and uses Claude for ranking + synthesis. Missing an Anthropic
  key falls back to the heuristic synthesizer, and a missing/unlaunchable
  browser falls back to fixtures — so `gttp build` always degrades gracefully.

### Incremental builds

Fetched threads and synthesized pages are cached under `.cache/`, so iterating
is cheap:

```bash
gttp build --only "Deep Work"   # rebuild one book; the rest render from cache
gttp build --refresh            # ignore cached threads, re-fetch from Reddit
```

`--only` re-fetches and re-synthesizes just the matching book (matched by title
substring) and reuses cached pages for everything else, so the index stays
complete. Without `--refresh`, re-runs reuse cached Reddit threads — you can
re-tune ranking and synthesis without spending a single Reddit call. Every book
builds in isolation: one failed fetch never sinks the run (it falls back to the
last cached page, or an error placeholder).

## Configuration

`books.yaml` is the seed list — book titles plus the subreddits and search
queries to fan out over. Edit it directly or use `gttp add "<title>"`.

The only secret is `ANTHROPIC_API_KEY` in `.env` (see `.env.example`) — it
enables Claude ranking + synthesis. Nothing else is required.

**Live Reddit search runs through a real browser, not the API.** Reddit blocks
raw-HTTP clients (`requests`/`curl` → 403) and its OAuth API now sits behind a
manual "Responsible Builder Policy" application that low-volume hobby tools
rarely clear. So `PlaywrightRedditClient` launches a headed Chromium, warms it
by loading a normal Reddit page, and issues each `.json` request as a
same-origin in-page `fetch()` — which returns clean JSON. Consequences:

- Run `playwright install chromium` once (the Docker image bakes it in).
- A **display** is required. On Windows/WSL, WSLg provides one; the Docker
  image runs Xvfb. Headless does *not* work — Reddit blocks it.
- Requests are paced ~6s apart (≈10/min), so a full uncached build is slow;
  the `.cache/` (including the browser profile) makes re-runs cheap.

The fetch layer is deliberately swappable (`reddit_client.py`) so it can be
replaced without touching the rest of the pipeline.

## Running as a container

`docker-compose.yml` builds an image that serves `site/` on `127.0.0.1:8100`.
It runs headed Chromium under Xvfb, so **live Reddit fetching works from the
container** — unlike GitHub Actions (below). Nothing builds automatically: the
container only serves whatever is already in `site/`. Every build is run
manually, on purpose — see [Summaries are permanent](#summaries-are-permanent).

```bash
docker compose up -d --build      # build the image + serve on http://127.0.0.1:8100
docker exec gttp-app-1 gttp build --only "Some Title"   # build one book manually
docker compose logs -f            # watch it
```

`.cache/`, `site/`, and `books.yaml` are bind-mounted, so cached threads,
the browser profile, and the catalog persist across restarts.

## Summaries are permanent

Once a book gets a real Claude-synthesized page it's **finalized** and
`gttp build` — even a full one with no `--only` — will never rebuild it again
automatically; you'll see `already finalized, skipping`. The only way to redo
one is deliberately, with `--force`:

```bash
gttp build --only "Some Title" --force
```

Non-final pages (empty placeholders, heuristic fallbacks — e.g. when the
Anthropic API is unreachable or out of credit) are still safe: a rebuild can
never save something *worse* than what's cached, only equal or better.

`.cache/pages/*.json` — the synthesized summaries themselves — are committed
to git (unlike `.cache/threads/` and the browser profile, which are
regenerable and stay ignored), so they're recoverable from history no matter
what happens to the local cache or container.

Nothing in this repo runs a build on a schedule. All builds are manual.

## Covers

Book cover images come from [Open Library](https://openlibrary.org/). A cover is
fetched automatically when you `gttp add` a book, and you can backfill or refresh
the whole catalog at any time:

```bash
gttp covers            # fetch covers for books that don't have one yet
gttp covers --force    # re-fetch every book (e.g. after a wrong match)
```

Covers are stored committed under `covers/<slug>.jpg` (one Large image per book)
and copied into `site/covers/` at build time. Books without a cover render a
deterministic lettered placeholder, so `gttp build --offline` never touches the
network — fetching only happens in `gttp add` and `gttp covers`.

## Deploying (GitHub Pages)

`.github/workflows/build.yml` publishes `site/` to GitHub Pages, but builds
**offline** (`gttp build --offline`): a headless datacenter runner can't drive a
real browser and Reddit blocks such IPs anyway, so live refreshes happen on the
self-hosted container above. To enable Pages:

1. **Settings → Pages → Source: GitHub Actions.**
2. **Settings → Secrets and variables → Actions**, add `ANTHROPIC_API_KEY`
   (optional — without it the heuristic synthesizer is used).
3. Trigger it from the **Actions** tab (*Build and publish gttp → Run workflow*),
   or wait for the weekly Monday run.

## Layout

```
src/gttp/
  config.py        settings + books.yaml loader
  models.py        RedditThread, RankedThread, BookPage
  reddit_client.py swappable fetch layer (fixture + HTTP)
  ranking.py       Claude thread scoring (+ heuristic fallback)
  synthesize.py    Claude page synthesis (+ heuristic fallback)
  publish.py       Markdown + static-site rendering
  pipeline.py      orchestration
  covers.py        Open Library cover fetch + SVG placeholder
  cli.py           `gttp add` / `gttp build` / `gttp list` / `gttp covers`
fixtures/          offline sample data
books.yaml         seed catalog
covers/            committed book cover images (<slug>.jpg)
```
