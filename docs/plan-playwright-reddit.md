# Plan: Playwright-based Reddit fetching for gttp

## Why

Reddit blocks all non-browser HTTP clients from its `.json` endpoints (403 via
Cloudflare-style TLS/HTTP fingerprinting — User-Agent spoofing does not help),
and its OAuth API now sits behind a manual "Responsible Builder Policy"
application that hobby projects rarely clear. `JsonRedditClient`
(src/gttp/reddit_client.py), which uses `requests`, therefore never gets data.

**Validated recipe** (tested 2026-07-11 on this machine, Playwright Chromium):

| Method | Result |
|---|---|
| `requests` / `curl`, any User-Agent | 403 |
| Playwright **headless** | 403 |
| Playwright headed, navigating directly to a `.json` URL | 403 |
| Playwright **headed**: `goto` an HTML page (e.g. `reddit.com/r/selfimprovement/`) first, then same-origin `page.evaluate(fetch('/r/.../search.json?...'))` | **200 with full results** |

So: launch headed Chromium (under Xvfb in the container), warm once by loading
a real Reddit HTML page, then issue every JSON request as an in-page
same-origin `fetch()`. No login or captcha was encountered.

The neighboring **bookhunt** project (`/home/eric/projects/bookhunt`) already
runs this exact pattern in production against Mobilism's Cloudflare — use
`bookhunt/src/searcher.js` (launch args, error handling) and
`bookhunt/Dockerfile` + `entrypoint.sh` (Xvfb setup, non-root uid 1000,
`PLAYWRIGHT_BROWSERS_PATH=/ms-playwright`) as reference implementations.

## What to build

### 1. `PlaywrightRedditClient` in `src/gttp/reddit_client.py`

Python (`playwright` package, sync API is fine — gttp is a synchronous batch
job). Implements the existing `RedditClient` protocol (`search(book) ->
list[RedditThread]`) so pipeline/ranking/synthesis need no changes.

- **Lifecycle**: lazy-launch on first `search()`; reuse the browser across all
  books in a build; close cleanly when the build ends (context manager or
  `close()` called from `pipeline.build_all`).
- **Launch**: `chromium.launch_persistent_context(PROFILE_DIR, headless=False, ...)`
  with bookhunt's proven args: `--disable-blink-features=AutomationControlled`,
  `--disable-gpu`, `--disable-gpu-compositing`, `--disable-dev-shm-usage`,
  `--no-sandbox`, `--disable-setuid-sandbox`, a real Chrome User-Agent string,
  viewport 1280x900. Profile dir `.cache/browser-profile/` (persists cookies
  across runs; add to the existing `.cache` bind mount — already gitignored).
- **Warm**: on startup, `page.goto("https://www.reddit.com/", wait_until="domcontentloaded")`
  + a ~3s settle. Verify with one probe fetch; if it 403s, retry the warm once,
  then raise a clear error.
- **Fetching**: every JSON call runs as
  `page.evaluate("url => fetch(url).then(r => r.ok ? r.text() : String(r.status))", path)`
  with a **relative** path (same-origin). Parse JSON in Python, not in the page.
- **Reuse existing parsing**: `_search_one`'s post-filtering
  (`removed_by_category`, empty `selftext`) and `_top_comments`' comment
  extraction in `JsonRedditClient` are already correct for this JSON shape —
  factor them into shared module-level helpers
  (`_parse_search_json(data, subreddit) -> list[dict]`,
  `_parse_comments_json(data, limit) -> list[str]`) that both clients use, so
  the parsing gets unit tests without a browser.
- **Rate limiting**: keep ~6s between requests (`REQUEST_INTERVAL`), and keep
  the 429-retry-with-backoff behavior.
- **Keep `JsonRedditClient`** (it's small, and shares the parsing helpers) but
  switch `make_reddit_client` in `src/gttp/pipeline.py` to
  `CachingRedditClient(PlaywrightRedditClient(...), refresh=refresh)`.
  If Playwright isn't installed or the browser fails to launch, print a clear
  message and fall back to fixtures (mirror the existing graceful-degradation
  style — one book's failure must never sink the build, and a missing browser
  must not break `--offline` or tests).

### 2. Dependencies

- Add `playwright>=1.40` to `pyproject.toml` dependencies and `requirements.txt`.
- Document the one-time `playwright install chromium` step in README (host
  dev); the Docker image bakes it in.

### 3. Docker changes

Model on `bookhunt/Dockerfile` (it solved the same problems):

- `Dockerfile`: keep `python:3.12-slim` base; add
  `ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright`; apt-install `xvfb` (fluxbox /
  x11vnc / novnc are **not** needed — no interactive warming required, no
  login); `RUN playwright install --with-deps chromium && chmod -R a+rX /ms-playwright`
  (runs as root at build; container runs as uid 1000). Set `DISPLAY=:99`,
  `HOME=/tmp` (Chromium needs a writable HOME as non-root).
- `entrypoint.sh`: start Xvfb on `:99` before the build loop, waiting for the
  X socket like bookhunt's entrypoint does (copy its stale-lock cleanup +
  socket-wait loop; also clear stale `Singleton*` locks in the browser
  profile). Keep the existing serve-and-rebuild structure unchanged.
- `docker-compose.yml`: no new services. `.cache` mount already covers the
  browser profile. Ensure `/tmp/.X11-unix` handling matches bookhunt
  (`mkdir -p /tmp/.X11-unix && chmod 1777` at image build).

### 4. Tests

- Unit-test the shared JSON parsing helpers with small inline dicts (search
  payload with a removed post, an empty-selftext post, a good post; comments
  payload with `[deleted]` entries).
- Existing 9 tests must keep passing; `FixtureRedditClient` and `--offline`
  are untouched. Do not launch a browser in any test.

### 5. Verification (must actually run these)

1. `python -m pytest` — all green.
2. Host smoke test (WSLg provides `DISPLAY=:0`):
   `gttp build --only "Atomic Habits" --refresh` → expect
   `found N candidate threads` with N > 0, and Claude-ranked pages if
   `ANTHROPIC_API_KEY` is set (currently empty in `.env` — heuristic fallback
   is expected without it).
3. `docker compose build && docker compose run --rm --entrypoint gttp app build --only "Atomic Habits" --refresh`
   → same expectation, now under Xvfb inside the container.
4. `docker compose up -d` → `curl -s http://127.0.0.1:8100/` serves the site;
   the Atomic Habits page cites real Reddit permalinks.

## Constraints / notes

- Repo state: the Docker scaffolding (Dockerfile, docker-compose.yml,
  entrypoint.sh, .dockerignore) and the `JsonRedditClient` refactor are
  **uncommitted** on `main`. Fold the Playwright work into that; commit when
  done (no Co-Authored-By line, per global rules).
- README's "Configuration" section and `.env.example` should be updated to
  describe the browser-based fetcher (no Reddit credentials; Playwright +
  Chromium requirement; `playwright install chromium` for host runs).
- The GitHub Actions workflow (`.github/workflows/build.yml`) cannot fetch
  live Reddit regardless (datacenter IPs + this same fingerprinting) — leave
  it building from fixtures/cache, or note that live builds are local-only.
- Be polite: this is low-volume batch curation that links back to Reddit.
  Keep the 6s spacing; never parallelize requests.
- If Reddit later starts challenging the warmed session, the escalation path
  is bookhunt's full pattern (persistent profile + noVNC manual warm) — note
  this in a comment but do not build it now.
