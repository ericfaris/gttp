# gttp — Handoff

## What it is / where it runs

Static site of point-first self-help book digests built from Reddit threads
and Claude synthesis. Production: Docker container on `127.0.0.1:8100`, public
at https://gttp.mooseflip.com through the mooseflip Cloudflare tunnel (no
Access gate). Nothing builds automatically: every `gttp build` is run by
hand inside the container.

**Production runs from this checkout, `/home/eric/projects/gttp`,** like
every other lab app. The compose file bind-mounts `./site`, `./.cache`,
`./books.yaml` and `./covers`, so the live container reads and writes these
directly. Rules:
- `gttp build --offline` is sandboxed: it writes `site-offline/` and never
  saves to `.cache/`. Use it freely.
- A plain `gttp build` here (outside the container) **updates the live site**
  and may spend Claude credits. Run builds inside the container instead.
- Pages re-synthesized in production land in `.cache/pages/` (tracked in
  git). Commit them, because Sentinel refuses to deploy into a dirty checkout.
- `/home/eric/deploy/gttp` is the retired separate clone (used until
  2026-10-25). It's kept for now as a fallback and is safe to delete.

## Commands

- Tests: `.venv/bin/python -m pytest -q`
- Lint (CI gate): `.venv/bin/ruff check .`
- Local preview without touching `site/`: call `publish.write_site(pages,
  <scratch dir>)` on the cached `.cache/pages/*.json`, then serve that dir
  from its root (the pages use absolute `/style.css` paths).
- Deploy: push to `main`. `test.yml` runs the tests, then its `deploy` job
  (self-hosted runner `gha-runner-gttp`) has Sentinel (`/api/deploy`) pull,
  `docker compose build --pull` and `up -d` here. On start the container
  runs `gttp render` (cached pages only, no network, no API), so the new
  code's HTML is live immediately. Manual equivalent:
  `docker compose build --pull && docker compose up -d`.
- Re-render without deploying: `docker compose exec -T app gttp render`.

## Claude usage

One model for ranking, synthesis and enrich: `MODEL` in
`src/gttp/config.py` (`claude-sonnet-5-5` since 2026-10-25). New pages record
the model that served them (`BookPage.model`, `enrich_model`, shown in the
"Synthesized by" line). The 29 existing pages predate this and show no model.
Pages are locked, so a refresh is explicit, run inside the container
(`docker compose exec -T app …`), then commit the changed page. Each call
prints its tokens and cost and appends to `.cache/usage.jsonl`. Real cost on
2026-10-25: about $0.05 per book (17 books for $0.90), and up to ~$0.10 for a
book with 30-40 relevant threads, since ranking dominates.
`gttp build --only "<title>" --force` (full re-synthesis) or
`gttp enrich --only "<title>" --force` (verdict layer only). A refusal,
truncation or API failure falls back to the heuristic path, and the
quality guard then keeps the cached page.

## Design system

`DESIGN.md` ("Switched On" v2) is the source of truth. Tokens and components
are in `static/style.css`, and the live showcase is `static/design-system.html`
(served at `/design-system.html`). Run a design-system compliance pass after
any UI change: no literal colors or radii outside tokens (one-offs are listed
in DESIGN.md), and every new component goes into the showcase.

## Open items (priority order)

1. **3 pages still lack real Reddit evidence:** Why Has Nobody Told Me This
   Before? and Boundaries (nothing in the Arctic Shift archive) and How to
   Begin (the archive only had generic "how to begin" posts). Their pages
   come from the July run and stay as they are (user's decision,
   2026-10-25); re-check them only if a new data source turns up. Thin but on-topic (1-3 threads): The Mountain Is You,
   The Magic of Thinking Big, Emotional Intelligence, The 7 Habits, Thinking
   Fast and Slow, Tiny Experiments.
2. **Reddit data source:** Reddit blocks the lab IP (403, "blocked by network
   security"), and official API access isn't available to us (tried
   2026-10-25), so don't pursue it. Live search uses the Arctic Shift
   archive (`src/gttp/arctic.py`, the default; `GTTP_REDDIT_SOURCE=browser`
   switches back). Its title search refuses very active subreddits (r/books,
   r/getdisciplined, r/productivity usually return 422 within a second) and
   results vary run to run. A `--refresh` merges into the cache, so
   re-running adds threads.
3. Idea from LESSONS: a weekly push of one try-today experiment (Google Task
   via goose-cli) with a keep/drop check-in.
4. `the-courage-to-be-disliked` has no stored cover. Run `gttp covers` in
   the container, or accept the on-brand placeholder.

## Gotchas

- Sentinel finds the repo at `~/projects/<name>` (`resolveRepoDirByName`,
  set in its code), so production must live in this checkout. After a
  deploy, `docker inspect gttp-app-1 --format '{{index .Config.Labels "com.docker.compose.project.working_dir"}}'`
  should print `/home/eric/projects/gttp`.

- Cloudflare caches static assets (`.css`, fonts, images) for 4h, and the
  `mooseflip` token **can't purge cache**. Pages link
  `/style.css?v=<content hash>` (`publish._css_version`), so CSS changes
  always reach viewers. A changed font, logo or icon under the same filename
  will still be served stale for up to 4 hours. Rename the file or purge it
  from the dashboard.
- Finalized pages are locked. Per-book additions go through the additive
  `gttp enrich` step, and new `BookPage` fields need defaults.
- Decorative glows can widen the page on phones. Check
  `documentElement.scrollWidth` at 390px after layout changes.
- Full-page Playwright screenshots show a visible seam in the background at
  viewport height (`background-attachment: fixed`). It's a screenshot
  artifact, not a bug.
