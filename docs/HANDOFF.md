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

**Link check (0.3.0, 2026-11-07):** the submit page has a Link field and a
**Check** button in front of **Queue it**.
- `gttp-resolver-1` (compose service `resolver`, same image, `python -m
  gttp.resolver`, port 8102, **no published port**): the only component that
  fetches arbitrary URLs (Goodreads/Amazon/any page) or reaches
  openlibrary.org. No mounts, no secrets, nothing from `.env`, read-only
  root, cap_drop ALL, uid 1000, 256 MB / 64 pids. It binds only to its IP on
  `resolver-internal` and `verify_request` drops every peer except `submit`
  (`gttp-submit-internal`) and itself (healthcheck). Fetches go through
  `gttp.safefetch` (http/https, default ports, DNS-name hosts only, every
  resolved IP must be public, the socket dials the validated IP, ≤3
  redirects re-checked per hop, 1 MB HTML / 256 KB JSON, 8 s / 5 s).
- `submit` gains `POST /api/check` `{url?, title?, author?}` → `{title,
  author, source, changes[], warnings[], duplicate, ready}`. It re-validates
  the resolver's answer as untrusted and falls back to casing only
  (`gttp.casing`) when the resolver is down/busy/over the cap, so the user
  never gets a 5xx. A check never writes the queue; `/api/submit` is
  unchanged and still enforces its own validation and caps. Queue it is
  enabled in the UI only for a `ready` check whose values are unedited (the
  server doesn't require a check: advisory, single user behind Access).
- Order of transforms: link (subtitle/edition dropped) → deterministic
  casing → Open Library (its title re-cased). Every change is listed with
  its reason. No Claude call or API key anywhere in this feature.

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
  127.0.0.1:8101/api/status`. **A `POST /api/check` to 8101 makes real
  outbound calls** (Goodreads/Amazon/Open Library) through the resolver.
- Resolver: `docker compose logs resolver` (method, path, status and
  outcome codes like `link:amazon-slug ol:matched` only). It is unreachable
  from the host by design; probe it from submit:
  `docker compose exec -T submit python -c "import http.client as h; c=h.HTTPConnection('gttp-resolver',8102,timeout=3); c.request('GET','/healthz'); print(c.getresponse().status)"`
  (→ 200). Throwaway no-egress test stack: plan §5.2.4 in
  `.claude/plans/link-resolver-plan.md`.
- Exit codes of the worker: 0 nothing to do /
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
  `GTTP_SUBMIT_DAILY_CAP`, `GTTP_SUBMIT_MAX_QUEUED`, `GTTP_CHECK_DAILY_CAP`
  (checks per NY day that reach the resolver, default 30; past it a check
  is casing only), and for the resolver `GTTP_RESOLVER_DAILY_FETCHES`
  (outbound HTTP requests per NY day incl. each redirect hop and OL query,
  default 200). Both counters are **in memory and reset when the container
  restarts** (a state file in `.cache/queue/` was rejected: the worker
  warns about every non-entry file there). Fixed limits: 6 checks per
  rolling minute and 1 in flight (submit, 429), 2 concurrent resolves
  (resolver, 429 → casing-only fallback).
- Secrets: the resolver gets **nothing** from `.env` (no `env_file`, no
  key); submit is unchanged (no key, only the `.cache/queue` mount).
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
- Networks (link check): `gttp_resolver-internal` (`internal: true`,
  172.25.0.0/16: submit + resolver) and `gttp_resolver-egress` (plain
  bridge, 172.27.0.0/16: resolver only). `app` stays on `gttp_default`
  only. Both subnets are RFC 1918, so the SSRF guard covers them. Verified
  2026-11-07 (local, before deploy): host → resolver internal IP gets an
  empty reply (dropped by `verify_request`), egress IP refused, `app` can't
  connect, submit → resolver 200. No Cloudflare change for this feature.
  Sentinel's health gate only checks `gttp-app-1`, so a broken resolver
  doesn't fail a deploy; check `docker compose ps`.
- A systemd-context `git push --dry-run` works (ssh auth OK from the user
  manager, checked 2026-11-07).

## Design system

`DESIGN.md` ("Switched On" v2) is the source of truth. Tokens and components
are in `static/style.css`, and the live showcase is `static/design-system.html`
(served at `/design-system.html`). Run a design-system compliance pass after
any UI change: no literal colors or radii outside tokens (one-offs are listed
in DESIGN.md), and every new component goes into the showcase.

## Open items (priority order)

1. **Deploy the link check (0.3.0)**: the commits are local, not pushed.
   Push `main` (Sentinel rebuilds and starts `resolver`), then confirm
   `docker compose ps` (app/submit/resolver), `gttp --version` 0.3.0 in
   `app`, `worker preflight` all ✓, and one live check each with a real
   Goodreads link, a typed title, an Amazon link and the metadata URL (plan
   §5.4). Until the deploy, the worker's version gate refuses (host 0.3.0,
   app 0.2.0); queued entries would only wait. Local `submit`/`resolver`
   were already recreated from the new image on 2026-11-07.
2. Optional: a one-off casing audit of the existing catalog (offered
   separately), and adding `gttp-resolver-1` / `gttp-submit-1` to
   Sentinel's `repo-map.json`.
3. A positive worker run (a real book that publishes) has not been done
   yet.
4. **The Like Switch has no evidence** (Arctic Shift: 16 posts, 0 passed
   filtering). Publishing it is blocked pending a user decision; it is the
   pipeline's real negative fixture.
5. **3 pages still lack real Reddit evidence:** Why Has Nobody Told Me This
   Before? and Boundaries (nothing in the Arctic Shift archive) and How to
   Begin (the archive only had generic "how to begin" posts). Their pages
   come from the July run and stay as they are (user's decision,
   2026-10-25); re-check them only if a new data source turns up. Thin but on-topic (1-3 threads): The Mountain Is You,
   The Magic of Thinking Big, Emotional Intelligence, The 7 Habits, Thinking
   Fast and Slow, Tiny Experiments.
6. **Reddit data source:** Reddit blocks the lab IP (403, "blocked by network
   security"), and official API access isn't available to us (tried
   2026-10-25), so don't pursue it. Live search uses the Arctic Shift
   archive (`src/gttp/arctic.py`, the default; `GTTP_REDDIT_SOURCE=browser`
   switches back). Its title search refuses very active subreddits (r/books,
   r/getdisciplined, r/productivity usually return 422 within a second) and
   results vary run to run. A `--refresh` merges into the cache, so
   re-running adds threads.
7. Idea from LESSONS: a weekly push of one try-today experiment (Google Task
   via goose-cli) with a keep/drop check-in.
8. `the-courage-to-be-disliked` has no stored cover. Run `gttp covers` in
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
- **Link check heuristics.** Amazon usually answers bots with a captcha;
  the resolver then guesses the title from the `dp` URL slug (warning
  shown, no author). Open Library titles are often sentence case ("The
  charisma myth"), so its values are re-cased, never taken raw. Casing
  keeps any word with an inner capital (McKeown, Le Cunff, NASA), and every
  catalog entry is a fixed point (`tests/test_casing.py`), but stylized
  names (`bell hooks`, `e.e. cummings`) and a typed-lowercase `Van` will be
  "fixed" wrongly when OL doesn't match. The preview shows every change and
  the fields stay editable. A one-word title with no author must match OL
  exactly (near-miss words like influence/influencer score 0.95).
- The container's Python (3.15) sets `VERIFY_X509_STRICT` by default, so a
  site with a sloppy certificate can fail in the resolver but not in host
  tests. Report it; never disable verification.
