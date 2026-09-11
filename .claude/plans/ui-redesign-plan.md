# Implementation Plan: gttp UI/UX Redesign

## Summary

Redesign the generated static site (`gttp.mooseflip.com`) from a plain
system-font list into a warm, editorial, cover-forward catalog with a proper
light/dark theme system and a manual, persisted theme toggle. All work happens
in the two render layers only — `src/gttp/publish.py` (the entire inline
CSS/HTML template and the render functions) and a light cosmetic touch to
`src/gttp/covers.py`'s SVG placeholder. No backend, pipeline, data, or tooling
changes. The site stays a single self-contained static bundle with inline
CSS + a few lines of vanilla JS — no framework, no build step, no external
font/CDN requests. Iterate with `gttp build` (zero-cost: all 29 books are
`is_final` and skipped; only `write_site` re-runs).

---

## Design direction

### Concept: "Warm ink editorial"

The header logo is a **deep ink-navy** wordmark with a single **orange arrow**.
Rather than fight it with a new hue (the previous muted terracotta `#e0703c`
was close but grayed-out and never matched the logo's punchier arrow), the
redesign builds a considered palette *around the logo*: warm paper neutrals, an
ink-navy text color that echoes the wordmark, and a single confident orange
accent tuned to sit next to the logo's arrow. This is the current
"editorial / warm-neutral" direction (think Every, Readwise, Stripe Press) —
paper-toned backgrounds instead of clinical `#fff`, a serif display face for
headlines paired with a system sans for body, generous rhythm, and restrained
depth (soft shadow + hairline border, no heavy skeuomorphism). It deliberately
avoids the AI-slop defaults: no Inter, no purple gradient, no uniform square
card grid — the accent is warm, the display type is a serif, and the index is a
scannable two-up card grid rather than a poster wall.

### Palette (CSS custom properties, exact values)

Defined once on `:root` (light) and overridden for dark. Reasoning: warm
paper (`#FBF8F4`) instead of pure white reads softer and more "printed"; ink
text (`#1B2233`) is the logo navy, not black, so body copy feels of-a-piece with
the wordmark; the orange is split into a **display accent** (`--accent`, used for
fills/borders/underlines) and a slightly darker **link ink** (`--accent-ink`,
used for body links) so link text clears WCAG AA on paper while borders/hovers
keep the brighter arrow-orange.

**Light (`:root` default):**

| Token | Value | Use |
|---|---|---|
| `--bg` | `#FBF8F4` | page background (warm paper) |
| `--surface` | `#FFFFFF` | cards |
| `--surface-2` | `#F3EEE7` | inset chips / search field |
| `--ink` | `#1B2233` | primary text (logo navy) |
| `--muted` | `#63697A` | secondary text, meta |
| `--border` | `rgba(27,34,51,.12)` | hairline borders |
| `--border-strong` | `rgba(27,34,51,.22)` | inputs, hover borders |
| `--accent` | `#E4571C` | arrow-orange: fills, hover borders, underlines |
| `--accent-ink` | `#B8420F` | body-link text (AA on paper) |
| `--accent-tint` | `rgba(228,87,28,.10)` | accent wash backgrounds |
| `--shadow` | `0 1px 2px rgba(27,34,51,.06), 0 10px 28px -14px rgba(27,34,51,.20)` | card depth |

**Dark (`--accent` lifts for contrast on dark ground):**

| Token | Value |
|---|---|
| `--bg` | `#0F1522` (blue-black, echoes logo navy — not pure black) |
| `--surface` | `#161D2C` |
| `--surface-2` | `#1E2637` |
| `--ink` | `#ECE9E3` (warm off-white) |
| `--muted` | `#98A0B2` |
| `--border` | `rgba(236,233,227,.12)` |
| `--border-strong` | `rgba(236,233,227,.24)` |
| `--accent` | `#F97A45` (brighter so borders/underlines read on dark) |
| `--accent-ink` | `#FB8C5C` (link text on dark) |
| `--accent-tint` | `rgba(249,122,69,.16)` |
| `--shadow` | `0 1px 2px rgba(0,0,0,.4), 0 12px 32px -16px rgba(0,0,0,.65)` |

Contrast sanity: `#1B2233` on `#FBF8F4` ≈ 14:1; `#B8420F` on `#FBF8F4` ≈ 5.1:1
(AA); `#63697A` on `#FBF8F4` ≈ 5.0:1 (AA). Dark: `#ECE9E3` on `#0F1522` ≈ 15:1;
`#FB8C5C` on `#0F1522` ≈ 7:1; `#98A0B2` on `#0F1522` ≈ 6.5:1. All body/meta text
clears AA in both themes.

### Typography

No external fonts (per brief). Escape the system-font look with a **serif
display + system sans body** pairing using only web-safe / OS-bundled faces:

- Display (h1, hero title, `blockquote.core`, section `h2`):
  `--font-display: "Iowan Old Style", "Palatino Linotype", Palatino, "Book Antiqua", Georgia, "Times New Roman", serif;`
  (Georgia is the universal fallback present on every platform; the Palatino/Iowan
  faces are richer where available.)
- Body / UI:
  `--font-body: system-ui, -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;`

Scale (rem, 17px base body): body 1.0625rem/1.65; index h1 `clamp(2rem, 5vw, 2.75rem)`;
detail h1 `clamp(1.9rem, 4.5vw, 2.6rem)`; `blockquote.core` 1.35rem serif;
h2 1.4rem; meta/tags 0.8rem. Display headings: `letter-spacing:-.01em; line-height:1.12`.

### Spacing & rhythm

8px base. Use a small scale expressed inline (`.5rem/.75rem/1rem/1.5rem/2rem/3rem`).
Radii: `--radius:14px` (cards, hero cover), `--radius-sm:9px` (chips, inputs,
buttons). Main column widths: index `max-width:64rem` (to hold a 2-up grid);
detail page `max-width:46rem` (reading measure — keep the current narrow column).
Since the shell is shared, set `main{max-width:64rem}` and add a
`main.reading{max-width:46rem}` variant applied on detail pages (see task 5).

### Layout concept

- **Index:** replace the single vertical list with a **responsive two-up card
  grid**. `.books` becomes
  `display:grid; grid-template-columns:repeat(auto-fill, minmax(20rem,1fr)); gap:1rem`.
  Each `.card` keeps its internal **cover-left / text-right flex** row (covers
  are 2:3 portrait — a horizontal mini-card is far more scannable for 29 titles
  than tall poster tiles, and reflows to a single column under ~20rem). Cards get
  `--surface` background, hairline border, soft shadow, and a hover lift
  (`translateY(-2px)` + stronger shadow + accent border). The search field sits
  above the grid full-width; the intro (h1 + sub) is a compact hero band.
- **Detail:** keep the `.book-hero` grid (cover + body) but elevate it — cover
  gets `--radius` + shadow, title in display serif, `blockquote.core` becomes a
  serif pull-quote with an accent left-rule and faint `--accent-tint` wash. The
  `.toc` becomes a bordered "In this book" aside card. `h2`s get a small accent
  eyebrow rule. `.back` stays sticky but restyled as a subtle inline link.

---

## Approach & key decisions

### Theme system (light/dark + manual toggle)

Three-layer cascade, ordered so the manual choice always wins without a flash:

1. **Base `:root`** holds the **light** variables and `color-scheme:light`.
2. **System dark, only when the user hasn't chosen:**
   `@media (prefers-color-scheme:dark){ :root:not([data-theme]){ ...dark vars; color-scheme:dark } }`.
   The `:not([data-theme])` guard means a manual choice disables the media query.
3. **Manual override:** `:root[data-theme="dark"]{ ...dark vars }` and
   `:root[data-theme="light"]{ ...light vars }`. Attribute selectors
   (specificity 0,2,0) beat the media block's `:root` (0,1,0), and the `:not()`
   guard removes any ambiguity, so there is no order-dependence.

**Persistence:** `localStorage['gttp-theme']` = `"light"` | `"dark"`.

**No-flash init (in `<head>`, runs before `<body>` paints):** a tiny inline
script reads `localStorage` and stamps `data-theme` on `<html>` synchronously.
Because it runs before first paint and the attribute override wins the cascade,
there is no flash of the wrong theme.

**Toggle control:** a `<button id="theme-toggle">` in the header, right-aligned
(header becomes `display:flex; justify-content:space-between; align-items:center`).
It contains two inline SVGs (sun + moon); CSS shows the moon in light mode
("switch to dark") and the sun in dark mode, keyed off the same
`[data-theme]` / media-query logic as the palette so the icon is correct even
before JS runs. On click, JS computes the current effective theme (attribute,
else `matchMedia`), flips it, writes the attribute + `localStorage`, and updates
`aria-pressed`.

**Why scripts live in the shell, not the per-page `{script}` field:** the theme
init + toggle wiring must appear on *every* page, but `_render_book_html`
currently passes `script=""`. Add two new dedicated placeholders,
`{head_script}` and `{theme_toggle}`, populated from module-level constants that
both render functions pass in. Injecting JS via `.format()` **values** (not baked
into the template literal) means the JS keeps normal single braces — only the
template-literal CSS needs doubled braces. Keep `{script}` for the index-only
search JS.

### Markup / class changes in the render functions

- `_render_header(root)` — add the flex wrapper + theme-toggle button (with the
  two SVGs). Keep the logo `<img>` and its white chip.
- `_HTML_SHELL.format(...)` calls in both `_render_index_html` and
  `_render_book_html` — pass the two new kwargs `head_script=_THEME_INIT` and
  `theme_toggle=_THEME_TOGGLE`.
- `_render_book_html` — add a `reading` class to `<main>` (via a new
  `{main_class}` placeholder or a second `main`-variant; simplest: add
  `{main_class}` placeholder to the shell, index passes `""`, detail passes
  `" reading"`). Wrap the `.toc` in a titled aside (add a heading label).
- `_render_index_html` — wrap the intro in a `<div class="intro">` band; no
  structural change to the `<ul class="books">`/`<li class="card">` pairing (the
  grid is pure CSS, and the search JS still targets `.books li`).
- `_render_index_card` / `_cover_tile` — no structural change required; class
  names (`card`, `card empty`, `cover cover-thumb`, `data-search`, `tag`) all
  stay so the CSS and tests keep matching. Optionally bump the thumb `width`
  attribute; keep `loading="lazy"` and the `width/height` attrs (a test asserts
  `loading="lazy"`).

### The `color-scheme` interaction

`color-scheme` is set *through the same cascade* (light on base, dark in the
guarded media query and in `[data-theme="dark"]`). This keeps native form
controls (the `<input type="search">`) matched to the active theme and prevents
the UA from re-tinting them against the manual choice. Because the head init
script sets `data-theme` before paint, `color-scheme` resolves correctly on
first paint too — no white flash on a dark-preferred device that chose light, or
vice-versa.

---

## Step-by-step tasks

Each task is independently verifiable with `gttp build` + a browser/screenshot.

### Task 1 — Add theme-script constants (`src/gttp/publish.py`, new module-level)

Add two constants near the top (after imports, before `render_markdown`):

```python
_THEME_INIT = (
    "<script>(function(){try{var t=localStorage.getItem('gttp-theme');"
    "if(t)document.documentElement.setAttribute('data-theme',t);}"
    "catch(e){}})();</script>"
)

_THEME_TOGGLE = (
    "<script>(function(){var b=document.getElementById('theme-toggle');"
    "if(!b)return;"
    "function cur(){var a=document.documentElement.getAttribute('data-theme');"
    "if(a)return a;return (window.matchMedia&&"
    "window.matchMedia('(prefers-color-scheme: dark)').matches)?'dark':'light';}"
    "function sync(){b.setAttribute('aria-pressed',cur()==='dark');}"
    "sync();"
    "b.addEventListener('click',function(){var n=cur()==='dark'?'light':'dark';"
    "document.documentElement.setAttribute('data-theme',n);"
    "try{localStorage.setItem('gttp-theme',n);}catch(e){}sync();});})();</script>"
)
```

### Task 2 — Rewrite `_render_header` (`src/gttp/publish.py`, ~line 111)

Header becomes a flex row: logo lockup left, theme toggle right. Replace the
function body with:

```python
def _render_header(root: str) -> str:
    return (
        "<header class='site-header'>"
        f"<a class='site-title' href='{root}index.html'>"
        f"<img src='{root}logo.png' alt='Get To The Point' width='250'></a>"
        "<button id='theme-toggle' class='theme-toggle' type='button' "
        "aria-label='Toggle dark mode' aria-pressed='false' title='Toggle theme'>"
        "<svg class='icon-sun' width='20' height='20' viewBox='0 0 24 24' "
        "fill='none' stroke='currentColor' stroke-width='2' stroke-linecap='round' "
        "aria-hidden='true'><circle cx='12' cy='12' r='4'/>"
        "<path d='M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2"
        "M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4'/></svg>"
        "<svg class='icon-moon' width='20' height='20' viewBox='0 0 24 24' "
        "fill='none' stroke='currentColor' stroke-width='2' stroke-linecap='round' "
        "stroke-linejoin='round' aria-hidden='true'>"
        "<path d='M21 12.8A9 9 0 1 1 11.2 3 7 7 0 0 0 21 12.8z'/></svg>"
        "</button></header>"
    )
```

### Task 3 — Add shell placeholders & wire both render functions

In `_HTML_SHELL` (task 6): add `{head_script}` inside `<head>` (right after the
`<style>...</style>` block, before `</head>`) and change the body line to
`<body>{header}<main class="content{main_class}">{body}</main>{footer}{theme_toggle}{script}</body>`.

- `_render_index_html` `.format(...)` (~line 190): add
  `head_script=_THEME_INIT, theme_toggle=_THEME_TOGGLE, main_class=""`.
  Wrap the intro: change `body` to start with
  `"<div class='intro'><h1>Get To The Point</h1>"
  "<p class='sub'>Crowd-vetted self-help summaries, curated from Reddit.</p></div>"`
  then the existing search input, `<ul class='books'>…`, and no-results `<p>`.
- `_render_book_html` `.format(...)` (~line 244): add
  `head_script=_THEME_INIT, theme_toggle=_THEME_TOGGLE, main_class=" reading"`.
  Change the TOC block to a titled aside: replace
  `'<nav class="toc" aria-label="Ideas"><ol>'` with
  `'<nav class="toc" aria-label="Ideas"><p class="toc-title">In this book</p><ol>'`.

### Task 4 — Cosmetic touch to the SVG placeholder (`src/gttp/covers.py`, `placeholder_svg`, ~line 96)

Keep the deterministic md5 hue logic **unchanged** (offline reproducibility).
Two small cohesion tweaks only:
- Change `fill = f"hsl({hue} 45% 38%)"` → `fill = f"hsl({hue} 40% 40%)"` (very
  slightly richer/less muddy).
- Change the `<text>` `font-family` from the sans stack to
  `"Georgia, 'Times New Roman', serif"` and `font-weight` `600` → `700` so the
  placeholder letter matches the new serif display type.
Do **not** change the `viewBox`, aspect ratio, `role`, `aria-label`, or the
`css_class` passthrough — the `.cover` CSS (2:3 `aspect-ratio`) drives sizing.

### Task 5 — (folded into Tasks 2/3) confirm class names unchanged

No standalone code — just verify after edits that `card`, `card empty`,
`data-search`, `cover cover-thumb`, `cover cover-detail`, `tag`, `toc`,
`site-header`, `site-footer`, `id="q"`, `id="idea-N"`, `href="#idea-N"`,
`loading="lazy"`, and `covers/<slug>.jpg` / `../covers/<slug>.jpg` all still
appear — every one of these is asserted by `test_write_site_html_features`.

### Task 6 — Replace `_HTML_SHELL` CSS (`src/gttp/publish.py`, ~line 253)

**CRITICAL — brace doubling:** `_HTML_SHELL` is consumed by `str.format()`, so
**every literal `{` and `}` in the CSS/HTML below must be doubled** (`{{` `}}`).
The **only** single-brace tokens allowed in the template are these placeholders:
`{title}`, `{head_script}`, `{header}`, `{main_class}`, `{body}`, `{footer}`,
`{theme_toggle}`, `{script}`. The CSS is shown below in **readable single-brace
form** — double every brace when pasting into the Python string. (The existing
file already follows this convention for its current CSS.)

Full shell (structure) — keep the existing `<!doctype>`, `<meta>`, `<title>`,
and favicon `<link>`s exactly as they are; replace the `<style>…</style>` with
the stylesheet below; add `{head_script}` before `</head>`; update the `<body>`
line per Task 3:

```
<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<link rel="icon" type="image/x-icon" href="/favicon.ico">
<link rel="icon" type="image/png" sizes="32x32" href="/favicon-32x32.png">
<link rel="icon" type="image/png" sizes="16x16" href="/favicon-16x16.png">
<link rel="apple-touch-icon" sizes="180x180" href="/apple-touch-icon.png">
<style>  /* ← readable; DOUBLE every brace when pasting */
:root {
  --bg:#FBF8F4; --surface:#FFFFFF; --surface-2:#F3EEE7;
  --ink:#1B2233; --muted:#63697A;
  --border:rgba(27,34,51,.12); --border-strong:rgba(27,34,51,.22);
  --accent:#E4571C; --accent-ink:#B8420F; --accent-tint:rgba(228,87,28,.10);
  --shadow:0 1px 2px rgba(27,34,51,.06), 0 10px 28px -14px rgba(27,34,51,.20);
  --radius:14px; --radius-sm:9px;
  --font-display:"Iowan Old Style","Palatino Linotype",Palatino,"Book Antiqua",Georgia,"Times New Roman",serif;
  --font-body:system-ui,-apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
  color-scheme:light;
}
@media (prefers-color-scheme:dark) {
  :root:not([data-theme]) {
    --bg:#0F1522; --surface:#161D2C; --surface-2:#1E2637;
    --ink:#ECE9E3; --muted:#98A0B2;
    --border:rgba(236,233,227,.12); --border-strong:rgba(236,233,227,.24);
    --accent:#F97A45; --accent-ink:#FB8C5C; --accent-tint:rgba(249,122,69,.16);
    --shadow:0 1px 2px rgba(0,0,0,.4), 0 12px 32px -16px rgba(0,0,0,.65);
    color-scheme:dark;
  }
}
:root[data-theme="dark"] {
  --bg:#0F1522; --surface:#161D2C; --surface-2:#1E2637;
  --ink:#ECE9E3; --muted:#98A0B2;
  --border:rgba(236,233,227,.12); --border-strong:rgba(236,233,227,.24);
  --accent:#F97A45; --accent-ink:#FB8C5C; --accent-tint:rgba(249,122,69,.16);
  --shadow:0 1px 2px rgba(0,0,0,.4), 0 12px 32px -16px rgba(0,0,0,.65);
  color-scheme:dark;
}
:root[data-theme="light"] { color-scheme:light; }

* { box-sizing:border-box; }
html { scroll-behavior:smooth; }
body { font:1.0625rem/1.65 var(--font-body); margin:0;
       background:var(--bg); color:var(--ink);
       -webkit-font-smoothing:antialiased; text-rendering:optimizeLegibility; }
main.content { max-width:64rem; margin:2.5rem auto; padding:0 1.25rem; }
main.reading { max-width:46rem; }

h1 { font-family:var(--font-display); line-height:1.12; letter-spacing:-.01em; font-weight:700; }
h2 { font-family:var(--font-display); font-size:1.4rem; letter-spacing:-.01em;
     margin:2.4rem 0 .6rem; padding-top:.2rem; }
h2::before { content:""; display:block; width:2rem; height:3px; border-radius:2px;
             background:var(--accent); margin-bottom:.6rem; }
a { color:var(--accent-ink); text-decoration-color:var(--border-strong);
    text-underline-offset:2px; }
a:hover { text-decoration-color:var(--accent); }
.sub { color:var(--muted); margin:.4rem 0 0; }
.author { color:var(--muted); }

.site-header { display:flex; align-items:center; justify-content:space-between;
               gap:1rem; padding:.8rem 1.25rem;
               border-bottom:1px solid var(--border); }
.site-title { display:inline-flex; text-decoration:none; }
.site-title img { display:block; width:250px; max-width:56vw; height:auto;
                  background:#fff; border-radius:10px; padding:5px 12px; }

.theme-toggle { display:inline-flex; align-items:center; justify-content:center;
                width:2.4rem; height:2.4rem; flex-shrink:0; cursor:pointer;
                color:var(--ink); background:var(--surface);
                border:1px solid var(--border-strong); border-radius:var(--radius-sm);
                transition:background .15s, border-color .15s, transform .1s; }
.theme-toggle:hover { border-color:var(--accent); background:var(--accent-tint); }
.theme-toggle:active { transform:scale(.94); }
.theme-toggle .icon-sun { display:none; }
.theme-toggle .icon-moon { display:block; }
:root[data-theme="dark"] .theme-toggle .icon-sun { display:block; }
:root[data-theme="dark"] .theme-toggle .icon-moon { display:none; }
@media (prefers-color-scheme:dark) {
  :root:not([data-theme]) .theme-toggle .icon-sun { display:block; }
  :root:not([data-theme]) .theme-toggle .icon-moon { display:none; }
}

.intro { margin:.5rem 0 1.75rem; }
.intro h1 { font-size:clamp(2rem,5vw,2.75rem); margin:.2rem 0 0; }

.search { font:inherit; width:100%; box-sizing:border-box; margin:0 0 1.75rem;
          padding:.7rem .9rem; border-radius:var(--radius-sm);
          border:1px solid var(--border-strong);
          background:var(--surface-2); color:inherit; }
.search::placeholder { color:var(--muted); }
.search:focus-visible { outline:2px solid var(--accent); outline-offset:2px;
                        border-color:var(--accent); }

.books { list-style:none; padding:0; margin:0; display:grid;
         grid-template-columns:repeat(auto-fill, minmax(20rem, 1fr)); gap:1rem; }
.books li { display:flex; gap:1rem; align-items:flex-start; margin:0;
            padding:1rem; border-radius:var(--radius);
            background:var(--surface); border:1px solid var(--border);
            box-shadow:var(--shadow);
            transition:transform .15s ease, border-color .15s, box-shadow .15s; }
.books li:hover { transform:translateY(-2px); border-color:var(--accent);
                  box-shadow:0 2px 4px rgba(27,34,51,.08), 0 16px 34px -16px rgba(27,34,51,.30); }
.card-body { min-width:0; flex:1; }
.card-body a { text-decoration:none; color:var(--ink);
               font-family:var(--font-display); font-size:1.08rem;
               line-height:1.25; display:inline-block; }
.card-body a:hover strong { color:var(--accent-ink); }
.card-body strong { font-weight:700; }
.books p { color:var(--muted); margin:.35rem 0 0; font-size:.95rem; }
.card.empty { opacity:.55; }

.cover { display:block; object-fit:cover; aspect-ratio:2 / 3;
         border-radius:8px; border:1px solid var(--border);
         background:var(--surface-2); box-shadow:0 6px 16px -10px rgba(27,34,51,.5); }
.cover-thumb { width:4.75rem; height:auto; flex-shrink:0; }
.cover-detail { width:12rem; height:auto; flex-shrink:0; border-radius:var(--radius);
                box-shadow:0 12px 30px -14px rgba(27,34,51,.55); }

.book-hero { display:grid; grid-template-columns:auto 1fr; gap:1.75rem;
             align-items:start; margin:.5rem 0 2rem; }
.hero-body h1 { margin-top:0; font-size:clamp(1.9rem,4.5vw,2.6rem); }
blockquote.core { font-family:var(--font-display); font-size:1.35rem; line-height:1.4;
                  border-left:3px solid var(--accent); padding:.6rem 0 .6rem 1.1rem;
                  margin:1rem 0 0; background:var(--accent-tint);
                  border-radius:0 8px 8px 0; }
blockquote { border-left:3px solid var(--border-strong); padding:.2rem 0 .2rem 1.1rem;
             margin:1.2rem 0; color:var(--ink); }
blockquote footer { color:var(--muted); font-size:.9rem; margin-top:.4rem; }

.toc { margin:1.5rem 0; padding:1rem 1.2rem; border-radius:var(--radius);
       background:var(--surface); border:1px solid var(--border); }
.toc-title { margin:0 0 .5rem; font-size:.78rem; letter-spacing:.08em;
             text-transform:uppercase; color:var(--muted); font-weight:600; }
.toc ol { margin:0; padding-left:1.3rem; }
.toc li { margin:.25rem 0; }
.toc a { text-decoration:none; color:var(--accent-ink); }
.toc a:hover { text-decoration:underline; }

.tag { display:inline-block; font-size:.75rem; margin:.35rem .35rem 0 0;
       padding:.15rem .55rem; border-radius:1rem; color:var(--muted);
       background:var(--surface-2); border:1px solid var(--border); }
.tags { margin-top:.3rem !important; }

li[id^='idea-'] { scroll-margin-top:3.5rem; margin:.5rem 0; }
.gen { color:var(--muted); font-size:.85rem; margin-top:2.5rem; }
.back { font-size:.9rem; margin:0 0 1.25rem; position:sticky; top:0; z-index:1;
        padding:.5rem 0; background:var(--bg); }
.back a { color:var(--muted); text-decoration:none; }
.back a:hover { color:var(--accent-ink); }

.site-footer { max-width:64rem; margin:3.5rem auto 2rem; padding:1.25rem 1.25rem 0;
               border-top:1px solid var(--border); color:var(--muted); font-size:.85rem; }
.site-footer a { color:var(--accent-ink); }

:focus-visible { outline:2px solid var(--accent); outline-offset:2px; border-radius:4px; }

@media (max-width:30rem) {
  .book-hero { grid-template-columns:1fr; }
  .cover-detail { width:9rem; }
  main.content { margin:1.5rem auto; }
}
@media (prefers-reduced-motion:reduce) {
  html { scroll-behavior:auto; }
  * { transition:none !important; }
  .books li:hover { transform:none; }
}
</style>
{head_script}
</head>
<body>{header}<main class="content{main_class}">{body}</main>{footer}{theme_toggle}{script}</body></html>
```

---

## Data / model / API changes

**None.** No changes to `models.py`, `pipeline.py`, `ranking.py`,
`synthesize.py`, `cache.py`, `config.py`, the catalog, book content, cover
network logic, `Dockerfile`, `entrypoint.sh`, or `docker-compose.yml`. This is
strictly the render/template layer (`publish.py`) plus one cosmetic SVG tweak
(`covers.py`). `render_markdown` is untouched, so
`test_render_markdown_has_sections` and the byte-identical-Markdown assertion in
`test_write_site_html_features` stay green.

---

## Testing & verification

Use the venv exactly as the brief specifies.

**Build (verbatim from brief):**
```
cd ~/projects/gttp && source .venv/bin/activate && gttp build
```
**Tests (verbatim from brief):**
```
cd ~/projects/gttp && source .venv/bin/activate && python -m pytest -q
```
Baseline is **1 failed, 23 passed** — the single pre-existing failure is
`test_build_book_offline_produces_page`. Success = that same one failure and
nothing new. `test_write_site_html_features` must stay green (it guards every
class/attr the CSS depends on).

**Serve for browser verification** (from the brief):
```
cd ~/projects/gttp/site && python -m http.server 8123
```
Then drive it with the **webapp-testing** skill (Playwright). Capture and
eyeball these six shots:

1. `http://127.0.0.1:8123/` — index, **light** (default).
2. Index **dark** — click `#theme-toggle`, screenshot, then reload and confirm
   it is *still dark* (persistence via `localStorage['gttp-theme']`).
3. Index at **375px** viewport width — confirm the card grid reflows to a single
   column and there is **no horizontal overflow** (check
   `document.documentElement.scrollWidth <= 375`).
4. A detail page, e.g. `http://127.0.0.1:8123/books/atomic-habits.html` —
   **light**: verify hero cover, serif title, author, core-idea pull-quote, the
   "In this book" TOC, quotes, sources, and the sticky "← all books" link.
5. Same detail page **dark**.
6. Detail page at **375px** — hero collapses to one column, cover shrinks, no
   overflow.

**Also assert in-browser:**
- Type in `#q` on the index and confirm cards filter (e.g. type `deep` → only
  matching cards visible, `#no-results` hidden/shown correctly).
- Click a TOC anchor on a detail page and confirm it scrolls to the matching
  `#idea-N` bullet.
- Confirm the SVG-placeholder path still renders: it is covered by
  `test_write_site_html_features` (the coverless "Meditations" fixture asserts an
  inline `<svg`), and all 29 catalog books have JPGs, so also spot-check that
  index thumbnails and a detail cover show real images.

### Acceptance-criteria → proof map

1. **Clean build of index + 29 pages** → `gttp build` exits 0; `ls site/books`
   shows 29 `.html`.
2. **Modernized index, both themes** → shots #1 and #2.
3. **Toggle present, instant, persists** → shot #2 (toggle + reload-still-dark).
4. **Search still filters** → in-browser `#q` type test.
5. **Detail page fully works** → shots #4/#5 + TOC-scroll click test.
6. **No break at 375px** → shots #3 and #6 + `scrollWidth` check.
7. **Covers render (JPG + SVG) at both sizes** → shots + the SVG assertion in
   `test_write_site_html_features`.
8. **No new pytest failures** → `python -m pytest -q` = 1 failed
   (`test_build_book_offline_produces_page` only) / rest passed.
9. **Committed & deployed, HTTP 200** → hand off to the **deploy** skill (lab
   route: `docker compose up -d --build` for `gttp`); confirm 200 at
   `http://127.0.0.1:8100/` and `https://gttp.mooseflip.com/`. Do not
   reimplement deploy steps outside the skill.

---

## Risks & watch-outs

- **Brace doubling.** The `#1` cause of a broken build here: `_HTML_SHELL` is
  `str.format()`-ed, so a single un-doubled `{`/`}` in the CSS raises
  `KeyError`/`ValueError` at build time. Double **all** CSS/HTML braces; keep
  only the eight named placeholders single. Sanity: `gttp build` will fail loudly
  if a brace is wrong — fix before moving on.
- **Don't break the search JS.** Keep `<ul class="books">`, `<li ...>`,
  `id="q"`, `id="no-results"`, and `data-search` exactly. The grid is CSS-only;
  `li[hidden]` is removed from the grid flow correctly. The search `{script}`
  stays index-only.
- **Theme flash / cascade fights.** Keep the `:root:not([data-theme])` guard on
  the `prefers-color-scheme:dark` block so a manual choice always wins, and keep
  the `_THEME_INIT` script **in `<head>` before `<body>`** so `data-theme` is set
  pre-paint. Set `color-scheme` through the same cascade so the search input and
  scrollbars don't flash the wrong tint. Do not remove `color-scheme` — no-JS /
  no-stored-pref clients still need the system default.
- **Cover rendering for both paths.** `_cover_tile` and `placeholder_svg` both
  emit `class="cover cover-thumb"` / `class="cover cover-detail"`; the `.cover`
  rule drives 2:3 sizing for JPG **and** SVG. Don't touch the SVG `viewBox`,
  aspect ratio, or the `width/height`/`loading="lazy"` attrs on the `<img>` (a
  test asserts `loading="lazy"`). The `object-fit:cover` + `aspect-ratio` keeps
  Open Library JPGs of varying pixel sizes from distorting.
- **Focus-visible.** Keep the global `:focus-visible` outline and the search
  field's focus style. If any element gets `outline:none`, it must have a visible
  replacement (accepted here only via the accent outline).
- **Dark-mode contrast via lifted accent.** Dark uses the brighter `#F97A45` /
  `#FB8C5C` specifically so borders/underlines/links clear contrast on
  `#0F1522`; don't reuse the light `--accent` on dark. Muted text was chosen to
  clear AA in both themes — don't lower its lightness further.
- **Header logo chip.** The logo keeps a white background chip (baked-in white
  art), which is intentional and reads as a card in both themes; `max-width:56vw`
  prevents it crowding the toggle on narrow screens. Do not regenerate the image
  assets.
- **Reduced motion.** The hover lift + smooth scroll are gated by
  `prefers-reduced-motion` — keep that block.

---

## Out of scope (do not build)

- No backend/pipeline logic — ranking, synthesis, Reddit/cover **network**
  fetching all untouched. `covers.py` change is cosmetic SVG only.
- No JS framework, bundler, or build step. Inline CSS + a few lines of vanilla
  JS, generated by the existing `publish.py` templating.
- No book content/data/catalog changes; `render_markdown` untouched.
- No external fonts/CDN/network requests of any kind.
- Do **not** "fix" the pre-existing failing `test_build_book_offline_produces_page`
  (test-isolation issue, unrelated, out of scope) — and do not mask it by
  weakening the new UI code.
- Do not regenerate `static/` image assets (logo/favicons); only CSS/markup
  around them changes.
- Do not reimplement deployment — the final step is the `deploy` skill.
```
