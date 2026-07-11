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

`docker-compose.yml` builds an image that rebuilds the catalog on a timer
(`POLL_INTERVAL_HOURS`, default weekly) and serves `site/` on
`127.0.0.1:8100`. It runs headed Chromium under Xvfb, so **live Reddit fetching
works from the container** — unlike GitHub Actions (below).

```bash
docker compose up -d --build      # build + serve on http://127.0.0.1:8100
docker compose logs -f            # watch build progress
```

`.cache/`, `site/`, and `books.yaml` are bind-mounted, so cached threads,
the browser profile, and the catalog persist across restarts.

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
  cli.py           `gttp add` / `gttp build` / `gttp list`
fixtures/          offline sample data
books.yaml         seed catalog
```
