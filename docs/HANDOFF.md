# gttp — Handoff

## What it is / where it runs

Static site of point-first self-help book digests built from Reddit threads
and Claude synthesis. Production: Docker container `gttp-app-1` on
`127.0.0.1:8100`, public at https://gttp.mooseflip.com through the mooseflip
Cloudflare tunnel (no Access gate).

**Book submission pipeline (0.2.0, 2026-11-07):**
- `gttp-submit-1` (compose service `submit`, same image, `python -m
  gttp.submit`) on `127.0.0.1:8101`: private form + queue status page. Its
  only mount is `.cache/queue/` (rw); read-only root, no git, no keys, no
  Anthropic key. It validates input (title ≤120, author ≤80, no
  control/invisible chars, ASCII slug, duplicates, 5/day, 3 waiting) and
  writes one JSON entry per submission. Intended public hostname:
  `submit-gttp.mooseflip.com` behind Cloudflare Access (see wiring below).
- Host worker `python -m gttp.worker run` (`src/gttp/worker.py`), run every
  minute by the `gttp-worker.timer` user unit (`systemd/` in this repo). It
  takes the oldest queued entry, preflights, holds Sentinel's deploy lock,
  runs `gttp add` + `gttp build --exact --only=<title>` in the app container
  with `GTTP_MAX_BUDGET_USD`, validates the page (real Claude synthesis,
  reddit-only links, quality guard), runs pytest + ruff, commits exactly
  `books.yaml` + `.cache/pages/<slug>.json` (+ `covers/<slug>.jpg`) and
  pushes `main`. Sentinel's normal push-to-main deploy publishes it. Any
  failure restores the tree and marks the entry `failed: <reason>`.
- Manual `gttp build` in the container still works as before (no budget
  guard unless `GTTP_MAX_BUDGET_USD` is set).

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
- Worker (from the repo root, host only):
  `.venv/bin/python -m gttp.worker preflight` (each check ✓/✗, read-only
  apart from a `git fetch`; exit 0 or 2), `… status` (print the queue),
  `… cancel <id>` (queued → failed "cancelled by operator"; how to clear
  test/junk entries without deleting files), `… run` (what the timer runs).
- Worker logs and timer: `journalctl --user -u gttp-worker -n 50`,
  `systemctl --user list-timers gttp-worker.timer`, and
  `systemctl --user start gttp-worker.service` to run one tick now.
- Submit service: `docker compose logs submit`, `curl -s
  127.0.0.1:8101/api/status`. Exit codes of the worker: 0 nothing to do /
  done / another worker running; 1 job failed, tree clean; 2 preflight
  refused (entry stays queued with `waiting: <reason>`); 3 restore failed,
  tree still dirty, **Sentinel deploys blocked** until fixed by hand.

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

## Config not in git

- `.env`: `ANTHROPIC_API_KEY` only, used by the `app` service via compose
  interpolation. The `submit` service gets nothing from it (no `env_file:`).
- `~/.config/gttp/worker.env` (optional, no secrets, loaded by the unit's
  `EnvironmentFile=-`): `GTTP_MAX_BUDGET_USD` (per job, default 0.50),
  `GTTP_MONTHLY_BUDGET_USD` (NY calendar month across all of
  `.cache/usage.jsonl`, default 5.00), `GTTP_WORKER_BUILD_TIMEOUT` (s,
  default 600). Compose-level overrides for submit:
  `GTTP_SUBMIT_DAILY_CAP`, `GTTP_SUBMIT_MAX_QUEUED`.
- `.cache/queue/` (gitignored) must exist and be owned by eric **before**
  the first `docker compose up`, or Docker creates it root-owned and the
  submit service exits with "queue dir … is not writable".

## Shared-system wiring

- Cloudflare (live since 2026-11-07): `submit-gttp.mooseflip.com` →
  `http://localhost:8101`. Access app "gttp submit" (id
  `7cf2acc2-8def-4d10-98c0-c72f9592cb38`, only ericfaris@gmail.com, 24h),
  proxied CNAME, and a remote tunnel ingress rule (the local `config.yml`
  fallbacks weren't updated; they already lack other hosts). Verified:
  unauthenticated GET/POST get a 302 to Access and create no queue entry;
  `gttp.mooseflip.com` stays public.
- systemd: `systemd/gttp-worker.{service,timer}` are symlinked into
  `~/.config/systemd/user/`, and **the timer is enabled** (every minute;
  enabled 2026-11-07 after `python -m gttp.worker preflight` was all ✓).
  Real negative run verified the same day: The Like Switch failed with
  "insufficient evidence", tree clean, nothing pushed, ~$0. A positive run
  (a real book that publishes) has not been done yet. Linger is on for eric.
- Sentinel: the worker takes Sentinel's per-repo deploy lock
  (`/tmp/sentinel-deploy-locks/_home_eric_projects_gttp.lock`, same
  pid-file + hard-link protocol as `sentinel/src/deployLock.js`) while the
  tree is dirty, so a deploy that arrives mid-job waits instead of
  refusing. If Sentinel changes that path or protocol, the worker silently
  stops serializing with deploys (its clean-tree checks still protect it).
  Sentinel waits at most 15 min for the lock; keep a job's dirty window
  under ~12 min. Sentinel's health check only looks at `gttp-app-1`;
  `gttp-submit-1` isn't in its `repo-map.json` (optional to add).
- A systemd-context `git push --dry-run` works (ssh auth OK from the user
  manager, checked 2026-11-07).

## Design system

`DESIGN.md` ("Switched On" v2) is the source of truth. Tokens and components
are in `static/style.css`, and the live showcase is `static/design-system.html`
(served at `/design-system.html`). Run a design-system compliance pass after
any UI change: no literal colors or radii outside tokens (one-offs are listed
in DESIGN.md), and every new component goes into the showcase.

## Open items (priority order)

1. **Roll out the submit pipeline** (in order): resolve the dirty tree
   (The Like Switch entry in `books.yaml`, `covers/the-like-switch.jpg`,
   the untracked plan files); push `main` so Sentinel deploys 0.2.0 and
   starts `submit`; link + enable the timer; run the Cloudflare runbook;
   then the real negative run (submit The Like Switch; expect `failed:
   insufficient evidence`, clean tree) and, with OK to spend, one positive
   run. The image built locally on 2026-11-07 baked in the dirty
   `books.yaml`, so the running `gttp-submit-1` currently treats The Like
   Switch as already in the catalog (409) until the next deploy rebuilds
   the image from a clean tree.
2. **The Like Switch has no evidence** (Arctic Shift: 16 posts, 0 passed
   filtering). Publishing it is blocked pending a user decision; it is the
   pipeline's real negative fixture.
3. **3 pages still lack real Reddit evidence:** Why Has Nobody Told Me This
   Before? and Boundaries (nothing in the Arctic Shift archive) and How to
   Begin (the archive only had generic "how to begin" posts). Their pages
   come from the July run and stay as they are (user's decision,
   2026-10-25); re-check them only if a new data source turns up. Thin but on-topic (1-3 threads): The Mountain Is You,
   The Magic of Thinking Big, Emotional Intelligence, The 7 Habits, Thinking
   Fast and Slow, Tiny Experiments.
4. **Reddit data source:** Reddit blocks the lab IP (403, "blocked by network
   security"), and official API access isn't available to us (tried
   2026-10-25), so don't pursue it. Live search uses the Arctic Shift
   archive (`src/gttp/arctic.py`, the default; `GTTP_REDDIT_SOURCE=browser`
   switches back). Its title search refuses very active subreddits (r/books,
   r/getdisciplined, r/productivity usually return 422 within a second) and
   results vary run to run. A `--refresh` merges into the cache, so
   re-running adds threads.
5. Idea from LESSONS: a weekly push of one try-today experiment (Google Task
   via goose-cli) with a keep/drop check-in.
6. `the-courage-to-be-disliked` has no stored cover. Run `gttp covers` in
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
- **books.yaml is a single-file bind mount.** Anything that replaces it by
  rename (`git checkout -- books.yaml`, `git reset --hard`, `os.replace`,
  editors that save by rename) leaves the app container reading the old,
  deleted file. Restore it in place (`git show HEAD:books.yaml >
  books.yaml`) and, if the container already lost it, `docker compose
  restart app`. The worker's preflight compares the two sha256s and
  refuses on a mismatch. `gttp add` now appends in place (comments kept).
- **`--only` matches by substring.** "Habits" also rebuilds "Atomic Habits"
  and "The 7 Habits". Automation must use `--exact`.
- **A dirty tree blocks both the worker and Sentinel deploys**, untracked
  files included. A worker exit 3 means it couldn't restore the tree; read
  the journal, fix the tree by hand, and the next tick or deploy continues.
- **`gttp build` exits 0 with a stub page** when Reddit has nothing usable,
  and writes the stub to the live `site/` immediately. Gate on the page's
  content (`worker.validate_page`), never the exit code.
