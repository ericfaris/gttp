# gttp — Handoff

## What it is / where it runs

Static site of point-first self-help book digests built from Reddit threads
and Claude synthesis. Production: Docker container on `127.0.0.1:8100`, public
at https://gttp.mooseflip.com through the mooseflip Cloudflare tunnel (no
Access gate). It rebuilds the catalog on a timer (`POLL_INTERVAL_HOURS`).

**Production runs from a separate clone, `/home/eric/deploy/gttp`.** Never run
`docker compose` or `gttp build` from `/home/eric/projects/gttp`: the compose
file bind-mounts `./site` and `./.cache`, so a dev build would overwrite the
live site (this happened on 2026-07-11).

## Commands

- Tests: `.venv/bin/python -m pytest -q`
- Lint (CI gate): `.venv/bin/ruff check .`
- Local preview without touching `site/`: call `publish.write_site(pages,
  <scratch dir>)` on the cached `.cache/pages/*.json`, then serve that dir
  from its root (the pages use absolute `/style.css` paths).
- Deploy: merge to `main`, then in `/home/eric/deploy/gttp` run
  `git pull --ff-only origin main && docker compose build --pull && docker compose up -d`
  (or use the `deploy` skill, but run it in the deploy clone, not here).
  Then `docker compose exec -T app gttp build`: the entrypoint only builds
  when `site/` is empty, so a restart alone keeps serving the old HTML.
  Last deployed: `06295e0` (2026-10-25).

## Claude usage

One model for ranking, synthesis and enrich: `MODEL` in
`src/gttp/config.py` (`claude-sonnet-5-5` since 2026-10-25). New pages record
the model that served them (`BookPage.model`, `enrich_model`, shown in the
"Synthesized by" line). The 29 existing pages predate this and show no model.
Pages are locked, so a refresh is explicit, run inside the container:
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

1. **Resolve the deploy-path conflict (blocks pushing).** `test.yml`'s
   `deploy` job makes Sentinel (`/api/deploy`, repo `gttp`) run compose in
   `~/projects/gttp`, the dev checkout, on every push to `main`. That
   remounts the live container onto dev's `site/` and `.cache/`, which the
   deploy-clone rule exists to prevent. On 2026-10-25 this served a stale page
   and wrote into dev's cache. Choose one: point Sentinel's gttp deploy at
   `/home/eric/deploy/gttp`, or drop the separate clone and protect dev
   another way. Until then, deploy by hand from the deploy clone after any
   push, and check with
   `docker inspect gttp-app-1 --format '{{index .Config.Labels "com.docker.compose.project.working_dir"}}'`.
2. **Re-synthesize the books built from off-topic threads** (see LESSONS,
   2026-10-25). The filter is fixed; each needs
   `gttp build --only "<title>" --force` (about $0.10-0.20 each). Books with no
   relevant cached threads would drop to the "no summaries yet" page.
3. **Live Reddit fetching is blocked** ("blocked by network security", 403
   behind a JS challenge). The thread cache is from 2026-07-11. The legitimate
   fix is Reddit's official API (needs an approved app).
4. Idea from LESSONS: a weekly push of one try-today experiment (Google Task
   via goose-cli) with a keep/drop check-in.
5. `the-courage-to-be-disliked` has no stored cover. Run `gttp covers` in
   the deploy clone, or accept the on-brand placeholder.

## Gotchas

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
