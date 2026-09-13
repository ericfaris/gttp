# gttp — Design System

**Direction:** Marginalia — v1.0
**Status:** live, wired into `static/style.css`, shown at `/design-system.html`

## What gttp is

gttp ("Get To The Point") finds the highest-signal Reddit threads about a
self-help book, ranks and synthesizes them with Claude, and publishes a
one-page "get to the point" digest per book: one core idea, a 5-bullet
version, an honest take, and the sharpest quotes with sources. It's a
static-site batch job — no accounts, no interactivity beyond a client-side
search box and a light/dark toggle. The audience is someone deciding whether
a 300-page book is worth their time, or wanting the substance without it.

## Direction narrative

**Marginalia**: the whole product is "a reader annotating a book to find the
one idea worth keeping," so the UI borrows the literal vocabulary of
marginalia — a highlighter stroke under the thing that matters, a hand-drawn
underline, an asterisk in the margin, a sticky note tint. This isn't a new
identity: the project already had a warm cream/navy/orange editorial palette
from an earlier redesign (`b074aaa`), which was already strong and
correctly-brand-toned — this uplift's job was to give that existing palette
a *reason*, tighten it into one coherent metaphor, turn it into real
documented/reusable tokens (it was previously a giant inline `<style>` block
baked into `publish.py`'s Python strings), and make it visible/checkable via
a showcase page.

### Mood boards considered

Three directions were generated via Ideogram (no user available in this
batch run to pick interactively, per the task); images live in this
session's scratchpad and are described here so a future pass can regenerate
consistent follow-up assets:

1. **Marginalia** (chosen) — cream paper, navy ink, one orange accent used
   as a highlighter/underline/asterisk annotation. Serif display headline
   with a hand-drawn underline, marginal asterisk and question-mark doodles.
   *Why chosen*: it's the only direction that turns the site's actual
   metaphor (distilling a book down to the one idea worth keeping) into a
   visual language, and it's a natural, low-risk tightening of the palette
   already in production — no wholesale rebrand risk, maximum payoff.
2. **Reading Room** — deep navy background, brass-orange accent, book-spine
   stripes, library-placard serif, wax-seal/"EST. MMXXV" stamp. Handsome and
   bookish, but a dark-by-default UI fights a "quick, skimmable digest"
   product — better suited to a slower, more ceremonial reading app.
3. **Digest Zine** — off-white/black/orange, cut-and-paste magazine
   lettering, torn paper, halftone, rubber-stamp "SUMMARIZED" tag. Punchy
   and distinctive, but the irreverent collage energy undersells the
   product's actual selling point (curated, vetted, "honest take" — not
   meme-tier chaos), and torn-edge/collage textures don't hold up well at
   long-form reading sizes (5-bullet lists, honest-take paragraphs).

Prompts used (for regenerating consistent follow-up assets later):
- Marginalia: *"Design system mood board for "Marginalia" — a warm literary
  annotation aesthetic for a book-summary website called "Get To The Point".
  Cream paper background (#FBF8F4), deep navy ink text (#1B2233), one sharp
  burnt-orange accent (#E4571C) used like a highlighter underline and
  marginal note. … hand-drawn orange underline beneath key words, small
  annotation marks and asterisks in the margin like a reader's marginalia.
  A rounded orange pill button labeled "Read the summary". Subtle
  paper-grain texture. Mood: literary, warm, confident, distilled."*
- Reading Room / Digest Zine prompts: see git history of this file's first
  version, or regenerate from the one-line summaries above.

### Key moments this was designed around

1. **The index scan** — a grid of book cards; the core-idea one-liner under
   each title is the thing being evaluated ("is this book worth two
   minutes?").
2. **Landing on a book page** — the pull-quote core idea is the single most
   important sentence on the whole site.
3. **Scanning the 5-bullet version** via the "In this book" jump list.
4. **Deciding to trust it** — the honest take + real Reddit sources with
   upvote counts, which is what makes this feel curated rather than
   AI-invented.
5. **Light/dark switch** — a small but real moment of control; used while
   reading at night.

## Color

All colors are CSS custom properties in `static/style.css`, flip completely
under `prefers-color-scheme: dark` or an explicit `data-theme` attribute.

| Token | Light | Dark | Role |
|---|---|---|---|
| `--color-bg` | `#FBF8F4` | `#0F1522` | Page background (warm cream / ink navy) |
| `--color-surface` | `#FFFFFF` | `#161D2C` | Cards, header controls, TOC box |
| `--color-surface-2` | `#F3EEE7` | `#1E2637` | Search input, tag pills, cover placeholder bg |
| `--color-ink` | `#1B2233` | `#ECE9E3` | Primary text |
| `--color-muted` | `#63697A` | `#98A0B2` | Secondary text, metadata, footer |
| `--color-border` | `rgba(27,34,51,.12)` | `rgba(236,233,227,.12)` | Hairline borders |
| `--color-border-strong` | `rgba(27,34,51,.22)` | `rgba(236,233,227,.24)` | Input/toggle borders |
| `--color-accent` | `#E4571C` | `#F97A45` | The "highlighter" — links, CTA, h2 dash, focus ring |
| `--color-accent-ink` | `#B8420F` | `#FB8C5C` | Accent on hover / higher-contrast accent text |
| `--color-accent-tint` | `rgba(228,87,28,.10)` | `rgba(249,122,69,.16)` | Pull-quote background wash |
| `--color-accent-highlight` | `rgba(228,87,28,.16)` | `rgba(249,122,69,.22)` | Reserved: a stronger marker-stroke tint for future inline highlight use |

**Semantic color**: this product has no forms/validation, so there is
deliberately no red/green error-success pair. The one semantic state is "not
yet summarized" (`.card.empty { opacity: .55 }`) — dimming rather than a
second accent color, so the one-accent rule (dominant + one sharp accent)
holds throughout.

**Contrast**: body text `--color-ink` on `--color-bg` is `#1B2233` on
`#FBF8F4` ≈ 14.8:1 (light) and `#ECE9E3` on `#0F1522` ≈ 14.2:1 (dark) — both
comfortably exceed WCAG AAA (7:1) for body text. `--color-accent-ink` on
`--color-bg` (link color) ≈ 6.6:1 light / `--color-accent-ink` on
`--color-bg` dark ≈ 7.9:1 — both pass AA for normal text and AAA for large
text.

## Type

Two faces, both self-resolving from OS font stacks (no webfont download —
keeps the static site fast and license-free, in keeping with "no server, no
database" simplicity):

- **Display** — `--font-display`: `"Iowan Old Style", "Palatino Linotype",
  Palatino, "Book Antiqua", Georgia, "Times New Roman", serif`. Used for
  headings, card titles, and the pull-quote — the literary, editorial voice.
- **Body** — `--font-body`: `system-ui, -apple-system, "Segoe UI", Roboto,
  Helvetica, Arial, sans-serif`. Used for paragraphs, metadata, UI chrome —
  optimized for long-form reading legibility on every platform.

### Type scale

| Token | Value | Use |
|---|---|---|
| `--text-xs` | `400 .75rem/1.4` sans | Tags, eyebrow labels |
| `--text-sm` | `400 .85rem/1.5` sans | Footer, "Synthesized by…" line, metadata |
| `--text-base` | `400 1.0625rem/1.65` sans | Body copy (bullets, honest take, quotes) |
| `--text-md` | `700 1.08rem/1.25` serif | Book card titles on the index |
| `--text-lg` | `700 1.4rem/1.3` serif | Section headings (`h2`: "The 5-bullet version," etc.) |
| `--text-quote` | `400 1.35rem/1.4` serif | The core-idea pull-quote |
| `--text-xl` | `700 clamp(1.9rem,4.5vw,2.6rem)/1.15` serif | Book detail page `h1` |
| `--text-2xl` | `700 clamp(2rem,5vw,2.75rem)/1.12` serif | Index page `h1` ("Get To The Point") |

## Spacing, radius, shadow, motion

**Spacing** (`--space-1` … `--space-14`, rem): `.25, .5, .75, 1, 1.25, 1.5, 2,
2.5, 3.5`. Most existing layout rules keep component-tuned local values
(carried over from the prior redesign) rather than being force-migrated onto
the scale — new/touched rules pull from it, and the showcase page renders
every step so drift is visible.

**Radius**: `--radius-sm: 9px` (inputs, theme toggle), `--radius: 14px`
(cards, TOC box, pull-quote corner), `--radius-lg: 18px` (reserved for
future larger surfaces), `--radius-pill: 999px` (tags, buttons).

**Shadow**: `--shadow-sm` (subtle button lift), `--shadow` (default card
elevation), `--shadow-lg` (card hover), `--shadow-cover` /
`--shadow-cover-lg` (book cover images, thumb vs. detail size).

**Motion**: `--duration-fast: 120ms` (button press/active), `--duration:
150ms` (hover transitions — card lift, theme toggle), `--duration-slow:
400ms` (reserved for larger reveals). Easing: `--ease-standard:
cubic-bezier(.2,.7,.3,1)`. All motion collapses under `prefers-reduced-motion:
reduce` (see Accessibility below).

## Components

All defined in `static/style.css`; every one is rendered live, in every
state, on `/design-system.html`.

- **Buttons** (`.btn`, `.btn-primary`, `.btn-ghost`) — new addition for this
  uplift (the app previously had no styled button, only links and the
  header icon-toggle). Primary is solid accent/white text for the one loud
  CTA a future page might need (a "Read the summary" style action); ghost is
  a bordered, transparent-background secondary. Both have `:hover`,
  `:active` (press-scale), and `:disabled` states.
- **Search input** (`.search`) — full-width, `--color-surface-2` fill,
  `:focus-visible` ring in the accent color.
- **Tag pill** (`.tag`) — small pill for subreddit provenance
  (`r/getdisciplined`, etc.) on index cards.
- **Book card** (`.books li`, `.card`, `.card.empty`) — cover thumbnail +
  title + core-idea line + tags; the whole card lifts and gains an accent
  border on hover; `.card.empty` dims for the not-yet-summarized state.
- **The signature control — the annotated pull-quote** (`blockquote.core`):
  the single most load-bearing component. It renders the book's core idea
  with an accent-tint background wash, a left marker-stroke bar
  (`::before`), and a marginal asterisk (`::after`) — literally the
  "highlight + annotate" gesture the whole direction is named for. Every
  other annotation cue in the system (the `h2::before` dash, the pull-quote
  bar) is drawn from the same vocabulary this component establishes.
- **Section heading** (`h2::before`) — a short 2rem accent dash above every
  `h2`, a smaller echo of the pull-quote's marker stroke.
- **Table of contents / jump list** (`.toc`) — "In this book" box linking to
  each bullet's `#idea-N` anchor.
- **Theme toggle** (`.theme-toggle`, `#theme-toggle`) — sun/moon icon
  button, persists to `localStorage['gttp-theme']`, defaults to
  `prefers-color-scheme`.
- **Header / footer** (`.site-header`, `.site-footer`) — logo, theme toggle,
  and the persistent footer link back to the index.

## Backgrounds & texture

No generated background art in this pass — the "paper grain" cue from the
Marginalia mood board is represented through color/typography (cream
surface, serif display, highlighter-tint wash) rather than an actual raster
texture, to keep the static site's page weight minimal and keep every
surface crisp at any zoom/print size. If a future pass wants an actual paper
grain, regenerate from the Marginalia prompt above with "seamless tiling
paper grain texture, cream, very subtle, no vignette" and apply it as a
low-opacity `background-image` on `body`.

## Accessibility notes

- Contrast ratios noted under Color are all AA-or-better; body text is AAA
  in both themes.
- Every interactive element (`theme-toggle`, `.search`, `.btn`, links) has a
  visible `:focus-visible` ring in the accent color (`outline: 2px solid
  var(--color-accent)`), not just a hover state.
- `@media (prefers-reduced-motion: reduce)` disables all transitions and the
  card hover lift, and forces `scroll-behavior: auto` (the site uses smooth
  scroll for the "In this book" jump list).
- Theme control: the toggle is a real `<button>` with `aria-pressed` kept in
  sync, not a div; it respects system preference by default and only
  overrides via explicit user action (persisted, not forced).
- No sound in this product; no mute control needed.

## Asset inventory

| File | Role |
|---|---|
| `static/style.css` | The design system: all tokens + component CSS. Copied to `site/style.css` at build time, linked from every page. |
| `static/design-system.html` | The showcase page (new). Copied to `site/design-system.html`. |
| `static/logo.png` | Wordmark (book + orange arrow + "get to the point."), pre-existing, kept as-is — it already matches this direction's palette exactly. |
| `static/favicon.ico`, `favicon-16x16.png`, `favicon-32x32.png`, `favicon-48x48.png`, `apple-touch-icon.png`, `icon-192.png`, `icon-512.png` | Favicon/app icon set, pre-existing (added in `3be7239`) — checked against the global "every project needs a real icon" rule and found already on-brand; not regenerated. |
| `src/gttp/publish.py` | Site generator — `_HTML_SHELL` now links `/style.css` instead of embedding an inline `<style>` block. |

Mood-board images generated this session (Ideogram, not committed to the
repo — regenerate from the prompts above if needed for a future pass):
Marginalia, Reading Room, Digest Zine (all titled "get to the point" design
system boards, 1:1, `QUALITY`/default rendering).

## Changelog

- **2026-09-13** — Initial design system uplift. Extracted the prior
  inline `<style>` block (from `b074aaa`'s "warm editorial palette"
  redesign) out of `publish.py` into `static/style.css` as documented,
  named CSS custom properties; named and committed to the **Marginalia**
  direction (annotation/highlighter motif) as a tightening of that existing
  palette rather than a replacement; added `.btn`/`.btn-primary`/`.btn-ghost`
  components (previously no styled button existed); gave the pull-quote its
  signature marker-stroke + asterisk treatment; built
  `static/design-system.html` as a live showcase page reading tokens
  straight from computed CSS custom properties; confirmed the existing
  favicon/logo set already fits the direction and left it untouched.

## Where to look

- Tokens + components: `static/style.css`
- Showcase page: `static/design-system.html` → served at
  `<site>/design-system.html` after any `gttp build` (e.g.
  `site/design-system.html` locally, or wherever the container/Pages deploy
  serves the site root from).
- Site generator wiring: `src/gttp/publish.py` (`_HTML_SHELL`,
  `_render_header`, `_render_footer`, `_cover_tile`, etc.)
