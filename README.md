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
cp .env.example .env          # optional: add Reddit + Anthropic credentials

# Runs fully offline from bundled fixtures — no credentials needed:
gttp build --offline
open site/index.html          # or serve site/ however you like

# With credentials, add a real book end-to-end:
gttp add "Deep Work"
gttp build
```

- **Offline mode** (`--offline`) reads Reddit threads from `fixtures/` and
  synthesizes pages with a deterministic heuristic — zero network calls. Use it
  to see the pipeline work before wiring up any keys.
- **Online mode** uses Reddit's API for search and Claude for ranking +
  synthesis. Missing an Anthropic key falls back to the heuristic synthesizer
  automatically, so `gttp build` degrades gracefully.

## Configuration

`books.yaml` is the seed list — book titles plus the subreddits and search
queries to fan out over. Edit it directly or use `gttp add "<title>"`.

Credentials live in `.env` (see `.env.example`):

- `ANTHROPIC_API_KEY` — enables Claude ranking + synthesis.
- `REDDIT_CLIENT_ID` / `REDDIT_CLIENT_SECRET` — enable live Reddit search.

Reddit's free API is non-commercial only; this is batch curation that links
back to Reddit, so it stays well within the free tier. The Reddit fetch layer
is deliberately swappable (`reddit_client.py`) so it can be replaced without
touching the rest of the pipeline.

## Deploying

`.github/workflows/build.yml` runs the online build and publishes `site/` to
GitHub Pages. To enable it:

1. **Settings → Pages → Source: GitHub Actions.**
2. **Settings → Secrets and variables → Actions**, add:
   - `ANTHROPIC_API_KEY`
   - `REDDIT_CLIENT_ID`
   - `REDDIT_CLIENT_SECRET`
3. Trigger it from the **Actions** tab (*Build and publish gttp → Run workflow*),
   or wait for the weekly Monday run.

Missing secrets don't break the build — any book the pipeline can't fetch falls
back to fixtures + the heuristic synthesizer, so the workflow always publishes a
site.

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
