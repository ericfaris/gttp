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
  (or use the `deploy` skill).

## Design system

`DESIGN.md` ("Switched On" v2) is the source of truth. Tokens and components
are in `static/style.css`, and the live showcase is `static/design-system.html`
(served at `/design-system.html`). Run a design-system compliance pass after
any UI change: no literal colors or radii outside tokens (one-offs are listed
in DESIGN.md), and every new component goes into the showcase.

## Open items (priority order)

1. Deploy the 2026-10-25 compliance pass (placeholder cover, card author
   line, radius token, showcase fixes). It's committed but not yet deployed.
2. Idea from LESSONS: a weekly push of one try-today experiment (Google Task
   via goose-cli) with a keep/drop check-in.
3. `the-courage-to-be-disliked` has no stored cover. Run `gttp covers` in
   the deploy clone, or accept the on-brand placeholder.

## Gotchas

- Finalized pages are locked. Per-book additions go through the additive
  `gttp enrich` step, and new `BookPage` fields need defaults.
- Decorative glows can widen the page on phones. Check
  `documentElement.scrollWidth` at 390px after layout changes.
- Full-page Playwright screenshots show a visible seam in the background at
  viewport height (`background-attachment: fixed`). It's a screenshot
  artifact, not a bug.
