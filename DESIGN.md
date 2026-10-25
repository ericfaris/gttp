# gttp — Design System

**Direction:** Switched On — v2.0 (evolved from v1 "Marginalia")
**Status:** live, wired into `static/style.css`, shown at `/design-system.html`

## What gttp is

gttp ("Get To The Point") finds the highest-signal Reddit threads about a
self-help book, ranks and synthesizes them with Claude, and publishes a
one-page digest per book. Each page leads with what to *do* with the book:
a one-line verdict, one thing to try today, and who it's for. Then come the
core idea, the 5-bullet version and the honest take, with the full take,
quotes and sources folded below. It's a static site: no accounts, no server
state. The only interactivity is search, today's-idea shuffle, an "I tried
it" tick saved in the browser, and a light/dark toggle.

The audience has usually collected self-help ideas without applying them.
The design's job is to make opening a page feel like a small, exciting
"lightbulb" moment and to make the try-today action the thing you remember.

## Direction narrative

**Switched On**: the moment an idea lights up. Warm cream paper and deep navy
ink (carried over from v1) with one sunny **marigold** light that marks
wherever the point is:

- the period in the wordmark (a small sun) and the full stop in the mark
- the glow behind today's idea and behind each book's cover
- the marker stroke under a verdict, drawing itself in
- the sticky note that says what to try today

Handwritten notes in **Caveat** ("Today's idea", "Try this today", "For",
"Skip it if") read like a reader's own margin annotations. Burnt orange
stays as the secondary accent for links, the wordmark's swipe and the sparks
when you tick "I tried it".

The user picked this from three Ideogram mood boards: Switched On (chosen),
Spark Studio (loud yellow, toy-like) and Golden Hour Desk (twilight amber).
Switched On won because it adds warmth and excitement without fighting
long-form reading, and it builds on v1's palette instead of replacing it.

Mood-board prompt (for consistent follow-up assets):
*"Design system mood board, 16:9 presentation board, for "Switched On", a
joyful book-idea website called "Get To The Point". Warm cream paper
background, deep navy ink, sunny marigold yellow glow, one burnt-orange
accent. Big friendly serif display headline "Get To The Point" with a
glowing lightbulb doodle and hand-drawn rays. Palette swatch chips, a rounded
marigold pill button labeled "Light up an idea", a book card with a soft
yellow halo glow, sticky-note card with a checkbox reading "Try this today".
Subtle paper grain. Mood: bright, curious, optimistic. Design presentation
board."* (Ideogram 4.0, 2560x1440.)

### Key moments this was designed around

1. **Landing on the homepage**: the "Today's idea" card. Its glow switches
   on with a small flicker. It shows one book's verdict and try-today, with
   a marigold "Read it in 2 minutes" button and a Shuffle button.
2. **Shuffle**: draws another book; the glow flickers on again and the
   verdict's marker stroke redraws.
3. **Opening a book page**: the cover sits in a pool of light and the
   verdict's marker stroke draws in.
4. **"I tried it"**: the sticky note's button ticks with a spring and a
   burst of orange and marigold sparks; the book's index card then shows
   "✓ Tried it".
5. **Scanning the grid**: hovering a card lights it with a marigold halo.

## Color

All colors are CSS custom properties in `static/style.css` and flip under
`prefers-color-scheme: dark` or an explicit `data-theme` attribute.

| Token | Light | Dark | Role |
|---|---|---|---|
| `--color-bg` | `#FBF6EC` | `#0F1526` | Page background (cream paper / night navy), with `--grain` |
| `--color-surface` | `#FFFDF8` | `#172038` | Cards, today card, search, core-idea card |
| `--color-surface-2` | `#F4ECDD` | `#1E2944` | Tag pills, cover placeholder, folded-section toggles |
| `--color-ink` | `#14213D` | `#F5EEDF` | Primary text; today card's border and hard shadow |
| `--color-muted` | `#5B6176` | `#A4ABBE` | Secondary text, metadata, footer |
| `--color-border` | `rgba(20,33,61,.12)` | `rgba(245,238,223,.12)` | Hairline borders |
| `--color-border-strong` | `rgba(20,33,61,.24)` | `rgba(245,238,223,.24)` | Input/toggle borders, dashed dividers |
| `--color-marigold` | `#F5C518` | `#F7CF3D` | **The light.** Primary button, h2 dash, core-idea edge, focus ring |
| `--color-marigold-ink` | `#14213D` | `#14213D` | Text and hard shadow on marigold (same in both themes) |
| `--color-glow` | `rgba(245,197,24,.42)` | `rgba(247,207,61,.26)` | Halos: today card, cover, card hover |
| `--color-highlight` | `rgba(245,197,24,.45)` | `rgba(247,207,61,.30)` | Marker stroke under the verdict and the index h1 |
| `--color-accent` | `#E4571C` | `#F07A45` | Bullet markers, sparks |
| `--color-accent-ink` | `#B8420F` | `#F6A27C` | Links, handwritten labels |
| `--color-accent-tint` | `rgba(228,87,28,.10)` | `rgba(240,122,69,.14)` | Reserved tint |
| `--color-sticky` | `#FFE27A` | `#F2D56B` | Try-this-today sticky note |
| `--color-sticky-ink` | `#2A2306` | `#1F1A04` | Text and "I tried it" button on the sticky note |
| `--color-done` | `#2F7D4F` | `#7BD49C` | "✓ Tried it" badge on index cards |
| `--color-accent-highlight` | alias of `--color-highlight` | | Kept for older markup |

**Semantic color**: the only states are "not yet summarized"
(`.card.empty { opacity: .55 }`) and "tried it" (`--color-done`). There are
no forms, so there is no error red.

**Contrast** (WCAG, computed):

| Pair | Light | Dark |
|---|---|---|
| ink on bg | 14.8:1 | 15.7:1 |
| muted on bg | 5.7:1 | 7.9:1 |
| accent-ink (links) on bg | 5.1:1 | 9.0:1 |
| navy on marigold (primary button) | 9.8:1 | 9.8:1 |
| sticky-ink on sticky | 12.2:1 | 12.0:1 |
| done on bg | 4.7:1 | 10.2:1 |

Everything passes AA for normal text; body text passes AAA.

## Type

Three self-hosted faces, all SIL Open Font License, served from
`static/fonts/` (copied to `site/fonts/` at build) with `font-display: swap`:

- **Display**: `--font-display`: `"Young Serif", "Iowan Old Style",
  Palatino, Georgia, serif`. One weight (400), which already reads bold:
  headings, card titles, the core idea, folded-section summaries. The
  wordmark's letters are Young Serif outlines.
- **Body**: `--font-body`: `"Instrument Sans", system-ui, …, sans-serif`.
  Variable 400–700 plus italic: paragraphs, UI, buttons, the verdict (600).
- **Hand**: `--font-hand`: `"Caveat", "Segoe Print", "Bradley Hand",
  cursive`. 600 only: margin notes ("Today's idea", "Try this today",
  "For", "Skip it if", "29 ideas and counting").

### Type scale

| Token | Value | Use |
|---|---|---|
| `--text-xs` | `600 .75rem/1.4` sans | Tags, uppercase eyebrows ("TRY TODAY") |
| `--text-sm` | `400 .875rem/1.5` sans | Footer, "Synthesized by…" line |
| `--text-base` | `400 1.0625rem/1.65` sans | Body copy |
| `--text-md` | `400 1.15rem/1.25` serif | Card titles, folded-section summaries |
| `--text-lg` | `400 1.5rem/1.25` serif | Section `h2` |
| `--text-quote` | `400 1.18rem/1.55` serif | The core-idea card |
| `--text-xl` | `400 clamp(2rem,5vw,2.9rem)/1.08` serif | Book `h1`, today's-idea title |
| `--text-2xl` | `400 clamp(2.3rem,6vw,3.4rem)/1.04` serif | Index `h1` |
| `--text-hand` | `600 1.45rem/1.1` Caveat | Margin notes |

## Spacing, radius, shadow, motion

**Spacing** (`--space-1` … `--space-14`, rem): `.25, .5, .75, 1, 1.25, 1.5,
2, 2.5, 3.5`.

**Radius**: `--radius-xs: 6px` (covers, focus ring, logo link),
`--radius-sm: 9px` (detail-page cover), `--radius: 14px` (cards),
`--radius-lg: 22px` (today card), `--radius-pill: 999px` (buttons, search,
tags, theme toggle). Deliberate one-offs, not tokens: the sticky note's
curled corner `3px 3px 18px 3px`, the core-idea card's squared marigold edge
`4px 14px 14px 4px`, the h2 dash (`3px`) and the tick box (`5px`).

**Shadow**: `--shadow-sm`, `--shadow` (cards), `--shadow-lg` (card hover),
`--shadow-cover` / `--shadow-cover-lg` (covers), `--shadow-sticky` (sticky
note lift). The today card and primary button use a **hard offset ink
shadow** (`6px 6px 0` / `3px 3px 0`) for a tactile, pressable feel; pressing
the button translates it into its shadow.

**Motion**:

| Token | Value | For |
|---|---|---|
| `--duration-fast` | 120ms | Button press, toggle press |
| `--duration` | 180ms | Hovers, the tick pop |
| `--duration-slow` | 480ms | Verdict marker draw-in, page-load rise |
| `--duration-glow` | 900ms | Glow switch-on / flicker |
| `--ease-standard` | `cubic-bezier(.2,.7,.3,1)` | Most motion |
| `--ease-spring` | `cubic-bezier(.34,1.56,.64,1)` | Card lift, checkmark pop, toggle rotate |

Keyframes: `switch-on` (glow fades in with one flicker), `flicker` (the
shuffle re-light), `marker` (highlight grows left to right), `pop`
(checkmark), `spark` (burst particles), `rise` (load-in, staggered with
`.rise-2` / `.rise-3`). Every animation ends in a visible state; nothing
waits at opacity 0.

## Components

All defined in `static/style.css` and rendered live on `/design-system.html`.

- **Today's idea** (`.today`): the signature moment. A surface card with a
  2px ink border, hard offset shadow and a radial marigold glow
  (`::before`). Contains a tilted cover, a Caveat "Today's idea" label with
  a spark icon, the title, author, verdict, a "TRY TODAY" line, and two
  buttons. The pick rotates once a day (`pick_today()` in `publish.py`, the
  same formula in the page script with the viewer's local date); Shuffle
  picks a random different book client-side from the embedded `#ideas`
  JSON. Only enriched books are candidates; without any, the hero is left
  out.
- **Buttons** (`.btn-primary`, `.btn-ghost`): primary is a marigold pill
  with an ink border and hard shadow that presses in on `:active`; ghost is
  a surface pill with a strong border. Both have hover, active and disabled
  states.
- **Book card** (`.books li`, `.card`, `.card.empty`): cover thumb, title
  in the display face, author on its own muted `--text-sm` line
  (`.card-author`, inside the link), core idea clamped to 3 lines
  (`.card-idea`), subreddit tags. The whole
  card is the link (`a::after` overlay). Hover and focus-within light it
  with a marigold halo. A "✓ Tried it" badge (`.tried-badge`) appears when
  the viewer has ticked that book.
- **Verdict** (`.verdict`): 600-weight sans with a marigold marker stroke
  that draws in.
- **Core idea** (`blockquote.core`): display face on a surface card with a
  6px marigold left edge.
- **Try this today** (`.try`): a yellow sticky note, rotated -0.8°, with a
  translucent tape strip (`::before`), a Caveat label, and the **"I tried
  it"** toggle (`.tried`, `aria-pressed`). Ticking it saves
  `localStorage['gttp-tried-<slug>']`, pops a checkmark and bursts ten
  `.spark` particles (skipped under reduced motion).
- **For / skip it if** (`.fit`): Caveat labels beside short lines; stacks
  on phones.
- **Folded section** (`details.more`): dashed top rule, round `+` toggle
  that turns marigold and rotates to `−` when open.
- **Section heading** (`h2::before`): a 6px marigold dash, tilted -2°.
- **Search** (`.search`): surface pill; border goes ink on hover, marigold
  focus ring. An empty filter shows a muted, centered `.no-results` line.
- **Cover placeholder** (`.cover-placeholder`, from
  `covers.placeholder_svg`): for a book with no stored cover. The title's
  first letter in Young Serif plus a marigold full stop, on an ink tile
  (`--color-ink` / `--color-bg` / `--color-marigold` via CSS classes, so it
  flips with the theme). Replaced v1's random-hue Georgia tile.
- **Tag pill** (`.tag`, `--text-xs` at its token weight), **theme toggle** (`.theme-toggle`, round), and
  **header/footer** (`.site-header` with the SVG wordmark swapped per theme
  via `.logo-light` / `.logo-dark`; `.site-footer` with a dashed rule).

## Logo

Picked by the user from an Ideogram round (alongside a sliced page stack, a
book-to-a-point and a lightbulb dot) and redrawn as clean SVG:

- **Wordmark** (`static/logo.svg`, `static/logo-dark.svg`): "gttp" in Young
  Serif outlines (extracted with fontTools, slightly tightened). The period
  is a marigold sun with four rays above it and a burnt-orange swipe under
  it, so everything leads to the point. Navy letters on light, cream
  `#F5EEDF` on dark. Used in the header at 128px wide.
- **Mark, "say it in one point"** (`static/mark.svg`): a speech bubble
  holding one marigold full stop with a small sparkle. On the app tile
  (`scripts/icons/icon.svg`) it's a cream bubble on the house-standard navy
  tile with a soft marigold glow; `#mark-small` drops the sparkle and scales
  up for favicons. Rebuild with `python3 scripts/icons/build.py`.

## Backgrounds & texture

- **Paper grain** (`--grain`): a 160px SVG `feTurbulence` tile as a data URI,
  ~5% navy, layered on `body` with `background-attachment: fixed`. No
  raster file. (The book page's back link is deliberately not sticky: any
  bar over the grain showed as a visible box.)
- **Glows**: radial gradients from `--color-glow` on `.today::before` and
  `.cover-glow::before`. `main.content` clips horizontal overflow
  (`overflow-x: clip` with a 1.25rem clip margin) so the halos never widen
  the page on phones.

## Accessibility notes

- Contrast table above; all text pairs pass AA.
- Focus ring everywhere: `outline: 3px solid var(--color-marigold)`. Cards
  show the halo on `:focus-within`.
- `prefers-reduced-motion: reduce` disables every animation and transition
  (including glows, marker and sparks) and smooth scrolling.
- The "I tried it" control is a real `<button>` with `aria-pressed` and a
  text change ("Tried it. Nice."). The today's-idea cover link is
  `aria-hidden` and `tabindex=-1` because the title link goes to the same
  page.
- Theme toggle: real `<button>` with `aria-pressed`, persists to
  `localStorage['gttp-theme']`, defaults to the OS preference.
- No sound.

## Asset inventory

| File | Role |
|---|---|
| `static/style.css` | All tokens and components. Copied to `site/style.css`. |
| `static/design-system.html` | Live showcase page. |
| `static/fonts/young-serif.woff2` | Display face (OFL, Google Fonts latin subset). |
| `static/fonts/instrument-sans.woff2`, `instrument-sans-italic.woff2` | Body face, variable 400–700 (OFL). |
| `static/fonts/caveat-600.woff2` | Handwritten face (OFL). |
| `static/logo.svg`, `static/logo-dark.svg` | Wordmark, light and dark. |
| `static/mark.svg` | Speech-bubble mark, standalone. |
| `scripts/icons/icon.svg` → `static/favicon.ico`, `apple-touch-icon.png`, `icon-192.png`, `icon-512.png`, `icon-maskable-512.png`, `manifest.webmanifest` | App icon on the house standard. `tests/test_icons.py` guards the wiring. |
| `src/gttp/publish.py` | Site generator: header, today's idea, shuffle and tried scripts. |

## Changelog

- **2026-09-13** — v1 **Marginalia**. Extracted the inline `<style>` from
  `publish.py` into `static/style.css` as named tokens; committed to an
  annotation/highlighter motif on the existing cream/navy/orange palette;
  added buttons; built the showcase page.
- **2026-10-25** — Point-first book page: verdict, try-today card, for/skip
  lines, folded sections for the full take, quotes and sources; removed the
  "In this book" TOC, which repeated the bullets below it.
- **2026-10-25** — v2 **Switched On**. Added marigold as "the light", plus
  glow, highlight, sticky and done tokens; self-hosted Young Serif,
  Instrument Sans and Caveat; new SVG wordmark and speech-bubble mark
  (replacing `logo.png` and the bullseye icon); homepage "Today's idea"
  hero with daily rotation and Shuffle; sticky-note try-today with "I tried
  it"; card halos, line-clamped card text, paper grain, and switch-on,
  marker and spark motion.

- **2026-10-25** — Compliance pass. Cover placeholder redrawn on-system
  (ink tile, Young Serif letter, marigold full stop) instead of random
  `hsl()` hues and Georgia. Card author split onto a muted line. Added
  `--radius-xs` and replaced hardcoded 6px/8px radii; dropped the unused,
  undocumented `--color-quiet`; tags use the `--text-xs` weight. Search
  hover state and a styled no-results line. Showcase: h1 in ink (it had
  inherited the muted lede color), plus `--color-marigold-ink`,
  `--radius-xs`, `--shadow-cover-lg`, spring and glow motion demos, the
  today-label spark, the tried badge and the placeholder cover.

## Where to look

- Tokens and components: `static/style.css`
- Showcase page: `static/design-system.html`, served at
  `<site>/design-system.html` after `gttp build`
- Site generator: `src/gttp/publish.py` (`_HTML_SHELL`, `_render_header`,
  `_render_today`, `_INDEX_SCRIPT`, `_BOOK_SCRIPT`, `_cover_tile`)
