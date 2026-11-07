# Brief: gttp book-submission pipeline

## Problem
Adding a book today is manual: edit `books.yaml`, `docker compose exec -T app gttp build --only ...`, commit the page, push. Eric wants to submit a book (title + author) from a web form and have it built, committed and deployed without touching a terminal.

## Goal
A gated web form queues a book; a host-side worker builds it in the existing container, validates the result, commits an allowlisted set of files to `main` and pushes; the normal Sentinel deploy then publishes it. One job at a time, hard cost caps, no untrusted text able to run commands or reach git/ssh/secrets.

## Decisions made (with user)
- **No tool-using agent.** The job calls the existing deterministic `gttp add` + `gttp build --only` (Python + Anthropic SDK, no tools). No Node, no Claude CLI, no `--allowedTools`. Injection risk is limited to page text; mitigate with schema/quality validation.
- **No separate job image.** The existing image already has Chromium, Xvfb, Playwright. Reuse it (`docker compose exec -T app ...`).
- **Git: host-side commit job.** Container writes via existing bind mounts (`books.yaml`, `.cache/pages`, `covers`); container has no git/ssh/deploy key. A host wrapper commits+pushes. Wrapper pushes, never the container.
- **UI: form + status page**, behind Cloudflare Access (only `ericfaris@gmail.com`); public read-only site stays ungated.
- **Publish gate: auto-publish** if the existing quality guard (`cache.page_quality`) + schema validation pass; on failure nothing is committed.
- Plan review gate: **skipped** by user (planner output is sanity-checked by the orchestrator, then build starts).

## In scope
1. Housekeeping (DONE by orchestrator, outcome below): attempted to build **The Like Switch** via the manual path. **Result: insufficient evidence.** Arctic Shift matched 16 posts, 1 about the book, 0 passed filtering, so `gttp build` produced only an empty heuristic stub (`core_idea: "No qualifying Reddit summaries found yet."`, no bullets/sources, `generated_by: heuristic`). The stub page was deleted and the site re-rendered (live site unchanged, 29 pages). `books.yaml` still has the uncommitted Like Switch entry and `covers/the-like-switch.jpg` exists untracked; leave both alone (do NOT commit them; orchestrator/user decide). Lessons for the design: (a) the build exits 0 and writes the stub even with no evidence, so the worker MUST gate on page content, not exit code; (b) it exercises the "insufficient evidence" outcome end to end; use it as the real-world negative test; (c) the arctic client refuses title search on busy subreddits, so thin results are common, not exceptional.
2. Submit service: tiny Python service (stdlib or minimal) as a second compose service from the same image, mounting **only** `./.cache/queue` rw (nothing else). Endpoints: `POST /api/submit` (title, author) -> writes a queue entry; `GET /api/status` (list/recent entries and states: queued/running/done/failed + message); serves the form/status page (static, in design-system style per `DESIGN.md`). Must not be able to run builds.
3. Input validation on submit: title <=120 chars, author <=80, printable text only (no control chars/newlines), duplicate/slug-collision rejection, daily cap (default 5, config), queue depth cap. Reject, never sanitize-and-continue silently.
4. Host worker script (e.g. `scripts/gttp-worker.sh` or Python, plus a systemd user timer/path unit following the existing `~/.config/systemd/user` pattern): `flock` single instance; pick oldest queued entry; abort if working tree has changes outside the allowlist; run `gttp add` + `gttp build --only` in the container with a per-job budget cap; verify the new page exists, parses as `BookPage`, passes quality; run tests + `ruff`; stage ONLY the allowlist (`books.yaml`, `.cache/pages/`, `covers/`); commit (`feat: add <title>`, Conventional Commits; follow the user's commit-attribution rules) and push to `main`; update the queue entry state. On any failure: restore tracked files (`git checkout -- books.yaml` etc. only for the files the job touched), mark failed, alert loudly (non-zero exit + log; leaving the tree dirty blocks Sentinel deploys, so the cleanup path must be tested).
5. Cost control: add an env-driven hard budget to the Claude calls (`GTTP_MAX_BUDGET_USD`, default ~0.50 per job) in `claude.py`/pipeline so a run aborts and falls back/keeps cache rather than overspending; plus a monthly ceiling checked by the worker from `.cache/usage.jsonl`.
6. Cloudflare: Access app + tunnel/DNS route for the submit hostname. **Announce before any change** (cloudflare skill; tunnel is remote-managed, see memory `mooseflip-tunnel-remote-managed`). Recommend a separate hostname (e.g. `submit-gttp.mooseflip.com`) so the Access app is simple and the public site is untouched. Executor must NOT make Cloudflare changes; it prepares a runbook, the orchestrator does it with the user's go-ahead.
7. Tests (standing rule: tests for every change, think regressions): submit validation + caps + queue state machine; worker allowlist/dirty-tree/abort-and-restore logic against a temp git repo; budget guard; a **prompt-injection fixture** (a fake Reddit thread with instructions like "ignore previous, run X / write file Y" must not change anything outside the book's page; output must still pass schema validation); existing tests stay green.
8. Docs: update `docs/HANDOFF.md`, append `LESSONS.md`, document new compose service, systemd unit, config not in git.

## Out of scope
- Tool-using agents / Claude CLI / Node.
- Multi-user submissions, accounts, public submit.
- Fixing thin-evidence pages, new Reddit data sources, weekly-experiment idea, missing cover for courage-to-be-disliked.
- Approval-before-publish step (user chose auto-publish).
- Any Cloudflare/DNS change by the build subagent.

## Constraints
- Prod runs from this checkout; Sentinel deploy (`/api/deploy`, push to main) refuses a dirty tree. `.cache/*` is gitignored except `.cache/pages/` (queue dir and usage.jsonl stay untracked; fine).
- Container runs as uid 1000, `no-new-privileges`; keep that. New submit service: read-only root FS where feasible, no ANTHROPIC key, no Reddit access, non-root, bound to 127.0.0.1.
- `gttp render` runs on container start; `gttp build` outside the container would update the live site, so the worker always execs in the container.
- Secrets: never printed/committed; `ANTHROPIC_API_KEY` stays as it is today (env from `.env`); the submit service gets none of it.
- Docker: base image `python:3.15-slim` (already latest per Dockerfile); build with `docker compose build --pull`.
- Solo repo: commit to `main`, push autonomously. Do not add Co-Authored-By lines per the user's CLAUDE.md for commits unless the harness attribution reminder overrides it (it does: use the reminder's trailer).
- Timezone America/New_York for daily cap rollover and logs.

## Acceptance criteria
1. The Like Switch page is live on gttp.mooseflip.com (cover, evidence, quality guard passed) and committed/pushed.
2. `POST /api/submit` with valid title/author creates a queue entry; invalid/over-long/control-char/duplicate input is rejected with a clear error; the (N+1)th submission in a day is rejected.
3. Running the worker on a queued entry (using a stubbed/offline builder in tests, and one real run in verification) builds the page in the container, commits only allowlisted files, pushes, and the Sentinel deploy goes green; queue entry ends `done`.
3b. A submission that yields no usable page (heuristic fallback, `generated_by != "claude"`, empty `sources`/`bullets`, or `page_quality` below final) is marked `failed: insufficient evidence`, NOTHING is committed or pushed, and the catalog entry and any stub page/cover it created are rolled back (The Like Switch is the real fixture for this: submitting it must fail cleanly and leave the tree identical to before). The status page shows the reason.
4. Worker refuses to run when the tree has non-allowlisted changes; two concurrent worker invocations never run two jobs (flock).
5. A failed or over-budget job leaves the working tree clean (touched files restored), the entry `failed` with a message, and nothing pushed.
6. A job's Claude spend cannot exceed the configured per-job cap; monthly ceiling blocks new jobs when reached.
7. The injection fixture cannot cause writes outside the allowlist or alter other pages; output still validates.
8. Submit service container has no git, no ssh keys, no Anthropic key, and only `.cache/queue` writable.
9. `.venv/bin/python -m pytest -q` and `.venv/bin/ruff check .` pass; new UI follows `DESIGN.md` (tokens only, added to `static/design-system.html` showcase) and has no horizontal scroll at 390px.
10. Cloudflare Access gating verified after the (orchestrator-run) change: unauthenticated request to submit hostname is blocked; public site still open.

## Open questions / for the planner to decide and record
- Worker trigger: systemd path unit watching `.cache/queue` vs timer polling vs submit service pinging a host socket. Prefer the simplest that keeps the container with no host access (path unit or 30-60s timer).
- Hostname for submit (recommended: separate hostname) and port (suggest 127.0.0.1:8101).
- Where submit-service code lives (`src/gttp/submit.py`?) and whether it shares the image (recommended) with a different entrypoint/command.
- How `GTTP_MAX_BUDGET_USD` is enforced inside `claude.py` given each call prints cost to `.cache/usage.jsonl`.

## Relevant files/areas
`src/gttp/cli.py`, `config.py` (`add_book`, `slugify`, `load_catalog`), `pipeline.py` (`build_all`, `match_books`, `render_site`), `cache.py` (`page_quality`, `is_final`, `save_page`), `claude.py`, `models.py` (`BookPage`), `covers.py`, `entrypoint.sh`, `Dockerfile`, `docker-compose.yml`, `.github/workflows/test.yml`, `static/` + `DESIGN.md` + `static/design-system.html`, `tests/` (esp. `conftest.py`, `test_pipeline.py`), `docs/HANDOFF.md`, `LESSONS.md`, `~/.config/systemd/user/` (existing unit style), `.cache/usage.jsonl`.

## Repo commands & tree state
- Tests: `.venv/bin/python -m pytest -q`
- Lint (CI gate): `.venv/bin/ruff check .`
- Offline build (safe): `.venv/bin/gttp build --offline` (writes `site-offline/`)
- In-container (live, spends credits): `docker compose exec -T app gttp build --only "<title>"`; re-render: `docker compose exec -T app gttp render`
- Rebuild/redeploy manually: `docker compose build --pull && docker compose up -d`; normal deploy = push to `main` (Sentinel via self-hosted runner).
- Container `gttp-app-1` is up (33h at start of session).
- Tree state at start: only `books.yaml` modified (adds The Like Switch / Jack Schafer). `.claude/plans/` is untracked-not-ignored; do not commit plan files.
