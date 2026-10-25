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
