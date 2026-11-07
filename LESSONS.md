# Lessons

- **2026-10-25 — Collected, not applied.** The real problem with gttp was
  that 29 good summaries were being collected but never read or acted on,
  not that they were bad. The first step was a point-first page: verdict,
  "try this today", for/skip, honest take as short points, and everything
  else folded under `<details>`. Ideas still open: weekly push of one
  experiment (e.g. a Google Task via goose-cli) plus a keep/drop check-in.
  - Gotcha: finalized pages are locked, so new per-book fields go in a
    separate **additive** step (`gttp enrich`) that derives only from the
    cached page. Re-synthesizing with `--force` would change summaries that
    were already approved. New `BookPage` fields need defaults so the
    committed cache JSON still loads.
  - Prompt for "try today" must say "exactly one action, not a menu". The
    first pass returned "delete an app, OR move a snack, OR…".
- **2026-10-25 — Show design choices in an artifact first.** Mood boards
  and logo concepts were described in text and the user had to ask for an
  artifact. Publish the gallery before asking for a pick. (Also added to the
  global `~/.claude/CLAUDE.md`.) Ideogram's plan here runs about 2 jobs at
  a time, so queue prompts one by one rather than firing 6 in parallel.
- **2026-10-25 — Switched On redesign gotchas.** Decorative `::before` glows
  that extend past the column widen the page on phones, and an
  element-bounds check won't find them (pseudo-elements aren't elements).
  Measure `documentElement.scrollWidth`, and clip with `overflow-x: clip` on
  `main.content` (clip, not hidden, so nothing becomes a scroll container).
  Any sticky bar painted over the paper-grain background shows as a visible
  box, so the back link is no longer sticky. `write_site` used to copy only
  top-level static files; it now copies subfolders too (fonts).
- **2026-10-25 — Deploy shipped new HTML with stale CSS.** Cloudflare
  cached `/style.css` (4h TTL), and the mooseflip API token has no Cache
  Purge scope, so the new markup was served against the old stylesheet.
  The fix is a content hash on the stylesheet URL (`?v=`), added at build time.
  Also: the deploy clone was 10 commits behind (the earlier redesign was
  never deployed), and a container restart doesn't rebuild `site/`. Run
  `gttp build` inside the container after `up -d`.
- **2026-10-25 — Half the catalog was synthesized from off-topic threads.**
  Unquoted title queries with `sort=top` return each subreddit's most
  popular posts, and `filter_threads` kept the top 40 by upvotes, so real
  summaries (around 100-300 upvotes) were cut before ranking. Claude then wrote
  "from general knowledge" with disclaimers in the core idea. Fix: only
  threads naming the book or author pass, and queries quote the title.
  Lesson: check that sources are on-topic, not just that the output reads
  well.
  - A failed `--refresh` (Reddit blocked) saved `[]` over the cached threads.
    The caching client now refuses to overwrite with a degraded or empty
    fetch.
  - Sentinel's push-to-main deploy runs in the dev checkout, which conflicted
    with the deploy-clone rule. Five pushes in one session flipped the live
    container between the two checkouts (resolved below).
  - Cost, measured with usage logging: re-synthesizing 17 books took 51 calls
    and $0.90 (about $0.05 per book; Boundaries, with 30 threads, cost $0.10).
    My token-count estimate was 3x too high because output and thinking were
    far below the max_tokens caps. Ranking scales with thread count. Nothing
    rebuilds automatically, so there's no recurring cost.
  - Matching on the title alone fails for everyday-phrase titles
    ("Boundaries", "Emotional Intelligence"): they match generic advice. Use
    the per-book `require_author` flag. A blanket rule would be too strict:
    Mindset and Grit threads rarely name the author.
- **2026-10-25 — Retired the separate deploy clone.** Every lab app runs
  from `~/projects/<name>` because Sentinel's `/api/deploy` resolves repos
  there. gttp's July clone (`~/deploy/gttp`) only worked until the push-to-main
  deploy job arrived, and after that each push silently switched production
  back to the dev checkout. Instead of a second checkout, the July risk is
  now guarded in code: `--offline` builds go to `site-offline/` and never
  save to `.cache/`, finalized pages are locked, and failed fetches can't
  overwrite the thread cache. The container runs `gttp render` on start, so
  a build-and-restart deploy always serves fresh HTML. Lesson: a safety rule
  that lives only in docs and memory drifts when automation changes; enforce
  it in code.
- **2026-10-25 — Reddit via the Arctic Shift archive.** Reddit blocks the
  lab IP and official API access wasn't granted, so new threads come from
  arctic-shift.photon-reddit.com (free, keyless, asks for polite use). Its
  title search is "not supported with very active subreddits": r/books,
  r/getdisciplined and r/productivity answer 422 "Timeout" within a second,
  whatever the limit or date range. Retry once, then skip the subreddit for
  the run. Results vary between runs (The Mountain Is You found 2, then 5
  threads), which is why `--refresh` merges rather than replaces.
  - Everyday-phrase titles keep turning up ("How to Begin" matched "how to
    begin to like myself?"). When a page's core idea opens with "Caveat:"
    the sources are off-topic: add `require_author` and keep the old page.
- **2026-11-07 — Book-submission pipeline (form → host worker → push).**
  Decisions and footguns from building it:
  - `gttp build` exits 0 and writes a "No qualifying Reddit summaries" stub
    (to `.cache/pages/` and the live `site/`) when nothing passes filtering;
    The Like Switch did exactly that. Automation must gate on page content
    (`worker.validate_page`: Claude synthesis, bullets + reddit-only
    sources, quality guard), never the exit code, and the rollback must
    remove the derived `site/books/<slug>.*` and re-render.
  - `--only` is a title substring match, fine by hand but dangerous in
    automation ("Habits" rebuilds three books). Added `--exact`; the worker
    also fails a job if any file outside the book's own three changed.
  - `yaml.safe_dump` round-trips strip every comment in `books.yaml`.
    `add_book` now appends a snippet in place and re-parses to verify.
  - `books.yaml` is a single-file bind mount: `git checkout`, `git reset
    --hard`, `os.replace` and rename-on-save editors swap the inode and the
    container keeps reading the deleted file. Restore by rewriting in place;
    undo an unpushed commit with `reset --soft` + `reset` (index only).
  - Sentinel refuses to deploy into a dirty tree and the worker's job makes
    the tree dirty for minutes. Borrowing Sentinel's own per-repo deploy
    lock (same pid + hard-link protocol) makes a deploy wait instead of
    fail. Coupling: if Sentinel changes the lock, serialization silently
    stops (the clean-tree checks still hold).
  - Hard budget without a token-count call: reserve `(prompt bytes + 4000) ×
    input price + max_tokens × output price` before each call (byte-level
    BPE means a token is at least one byte), refuse if spent + reserve >
    cap, and make the refusal sticky. The existing fallbacks turn it into
    the heuristic/keep-cache path. Fail closed on bad env or unknown price.
  - Residual injection risk is accepted: a hostile thread can still put
    misleading *words* on a page that passes validation (escaped text,
    reddit-only links), but it can't run anything, touch other files or
    add a non-reddit link.
  - Gotchas hit while verifying: a CSP without `unsafe-eval` breaks
    Playwright's `wait_for_function` (use locator waits); browsers send
    `Origin` on same-origin POSTs, so a test instance on another port needs
    its origin in `GTTP_SUBMIT_ORIGINS`; `systemd-run -p WorkingDirectory=`
    doesn't expand `%h`; `pkill -f <pattern>` can match the calling shell.
    And never call `gttp.cli.main(["add", ...])` to "just check argparse":
    it writes the real `books.yaml` and fetches a real cover.
