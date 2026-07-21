"""Render BookPages to Markdown and a minimal static site.

One Markdown file per book keeps the corpus diffable and versionable in the
repo; a small self-contained HTML index makes it browsable without a static
site generator. Point Hugo/Jekyll/Astro at the Markdown if you want more.
"""

from __future__ import annotations

import html
import shutil
from pathlib import Path

from . import covers
from .config import SITE_DIR, STATIC_DIR, slugify
from .models import BookPage

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


def render_markdown(page: BookPage) -> str:
    lines = [f"# {page.title}"]
    if page.author:
        lines.append(f"*by {page.author}*")
    lines.append("")
    lines.append(f"> {page.core_idea}")
    lines.append("")
    if page.bullets:
        lines.append("## The 5-bullet version")
        lines.extend(f"- {b}" for b in page.bullets)
        lines.append("")
    if page.honest_take:
        lines.append("## The honest take")
        lines.append(page.honest_take)
        lines.append("")
    if page.quotes:
        lines.append("## Worth quoting")
        for q in page.quotes:
            lines.append(f"> {q['text']}")
            lines.append(f">")
            lines.append(f"> — [{q['author']}]({q['url']})")
            lines.append("")
    if page.sources:
        lines.append("## Sources")
        for s in page.sources:
            lines.append(
                f"- [{s['title']}]({s['url']}) "
                f"— r/{s['subreddit']} ({s['score']} upvotes)"
            )
        lines.append("")
    lines.append(f"<sub>{_generated_line(page)}</sub>")
    lines.append("")
    return "\n".join(lines)


def write_site(
    pages: list[BookPage],
    site_dir: Path = SITE_DIR,
    covers_dir: Path | None = None,
) -> Path:
    """Write per-book Markdown + HTML and an index page. Returns index path.

    Any committed cover in `covers_dir` for a catalog book is copied into
    `site_dir/covers/`; books without one render an SVG placeholder.
    """
    if covers_dir is None:
        covers_dir = covers.COVERS_DIR
    site_dir.mkdir(parents=True, exist_ok=True)
    books_dir = site_dir / "books"
    books_dir.mkdir(exist_ok=True)
    site_covers = site_dir / "covers"

    if STATIC_DIR.is_dir():
        for asset in STATIC_DIR.iterdir():
            if asset.is_file():
                shutil.copy2(asset, site_dir / asset.name)

    for page in pages:
        slug = slugify(page.title)
        src = covers.cover_file(slug, covers_dir)
        if src is not None:
            site_covers.mkdir(exist_ok=True)
            shutil.copy2(src, site_covers / f"{slug}.jpg")
        (books_dir / f"{slug}.md").write_text(render_markdown(page))
        (books_dir / f"{slug}.html").write_text(
            _render_book_html(page, covers_dir)
        )

    index = site_dir / "index.html"
    index.write_text(_render_index_html(pages, covers_dir))
    return index


def _has_summary(page: BookPage) -> bool:
    return bool(page.bullets or page.sources)


def _generated_line(page: BookPage) -> str:
    if page.generated_at:
        return f"Synthesized by: {page.generated_by} on {page.generated_at}"
    return f"Synthesized by: {page.generated_by}"


def _truncate(text: str, limit: int = 60) -> str:
    """Truncate raw (unescaped) text to ~limit chars at a word boundary."""
    text = text.strip()
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0].rstrip()
    return (cut or text[:limit]) + "…"


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


def _render_footer(root: str = "") -> str:
    return (
        f"<footer class='site-footer'>"
        f"Crowd-vetted self-help summaries, curated from Reddit. "
        f"<a href='{root}index.html'>All books</a>.</footer>"
    )


def _cover_tile(page: BookPage, covers_dir: Path, root: str, css_class: str) -> str:
    """A cover <img> when one is stored, else a deterministic SVG placeholder."""
    slug = slugify(page.title)
    if covers.cover_file(slug, covers_dir) is not None:
        alt = html.escape(f"Cover of {page.title}")
        return (
            f'<img class="{css_class}" src="{root}covers/{slug}.jpg" '
            f'alt="{alt}" loading="lazy" width="72" height="108">'
        )
    return covers.placeholder_svg(page.title, css_class)


def _render_index_card(page: BookPage, covers_dir: Path) -> str:
    has_summary = _has_summary(page)
    cls = "card" if has_summary else "card empty"
    search = html.escape(
        " ".join(filter(None, [page.title, page.author, page.core_idea])).lower()
    )
    author = f" — {html.escape(page.author)}" if page.author else ""
    subs = list(dict.fromkeys(s["subreddit"] for s in page.sources))[:3]
    tags = "".join(
        f'<span class="tag">r/{html.escape(sub)}</span>' for sub in subs
    )
    cover = _cover_tile(page, covers_dir, "", "cover cover-thumb")
    return (
        f'<li class="{cls}" data-search="{search}">'
        f'{cover}'
        f'<div class="card-body">'
        f'<a href="books/{slugify(page.title)}.html">'
        f"<strong>{html.escape(page.title)}</strong>{author}</a>"
        f'<p>{html.escape(page.core_idea)}</p>'
        f'{f"<p class=tags>{tags}</p>" if tags else ""}'
        f'</div></li>'
    )


def _render_index_html(pages: list[BookPage], covers_dir: Path) -> str:
    # The index is a view: stable-sort summary books first, catalog order within.
    ordered = sorted(pages, key=lambda p: not _has_summary(p))
    cards = "\n".join(_render_index_card(p, covers_dir) for p in ordered)
    script = (
        "<script>"
        "var q=document.getElementById('q');"
        "if(q){"
        "var items=document.querySelectorAll('.books li');"
        "var none=document.getElementById('no-results');"
        "q.addEventListener('input',function(){"
        "var v=q.value.toLowerCase();var shown=0;"
        "items.forEach(function(li){"
        "var hit=li.dataset.search.includes(v);"
        "li.hidden=!hit;if(hit)shown++;});"
        "if(none)none.hidden=shown>0;});"
        "}"
        "</script>"
    )
    body = (
        "<div class='intro'><h1>Get To The Point</h1>"
        "<p class='sub'>Crowd-vetted self-help summaries, curated from Reddit.</p></div>"
        '<input type="search" id="q" class="search" placeholder="Search books…"'
        ' autocomplete="off" aria-label="Search books">'
        f"<ul class='books'>{cards}</ul>"
        '<p id="no-results" hidden>No books match.</p>'
    )
    return _HTML_SHELL.format(
        title="gttp — Get To The Point",
        head_script=_THEME_INIT,
        header=_render_header(""),
        main_class="",
        body=body,
        footer=_render_footer(""),
        theme_toggle=_THEME_TOGGLE,
        script=script,
    )


def _render_book_html(page: BookPage, covers_dir: Path) -> str:
    parts = [f"<p class='back'><a href='../index.html'>← all books</a></p>"]
    cover = _cover_tile(page, covers_dir, "../", "cover cover-detail")
    hero = [cover, '<div class="hero-body">']
    hero.append(f"<h1>{html.escape(page.title)}</h1>")
    if page.author:
        hero.append(f"<p class='author'>by {html.escape(page.author)}</p>")
    hero.append(
        f"<blockquote class='core'>{html.escape(page.core_idea)}</blockquote>"
    )
    hero.append("</div>")
    parts.append(f'<div class="book-hero">{"".join(hero)}</div>')
    if page.bullets:
        parts.append(
            '<nav class="toc" aria-label="Ideas">'
            '<p class="toc-title">In this book</p><ol>'
        )
        parts.extend(
            f'<li><a href="#idea-{i}">{html.escape(_truncate(b))}</a></li>'
            for i, b in enumerate(page.bullets, 1)
        )
        parts.append("</ol></nav>")
        parts.append("<h2>The 5-bullet version</h2><ul>")
        parts.extend(
            f'<li id="idea-{i}">{html.escape(b)}</li>'
            for i, b in enumerate(page.bullets, 1)
        )
        parts.append("</ul>")
    if page.honest_take:
        parts.append("<h2>The honest take</h2>")
        parts.append(f"<p>{html.escape(page.honest_take)}</p>")
    if page.quotes:
        parts.append("<h2>Worth quoting</h2>")
        for q in page.quotes:
            parts.append(
                f"<blockquote>{html.escape(q['text'])}"
                f"<footer>— <a href='{html.escape(q['url'])}'>{html.escape(q['author'])}</a></footer>"
                f"</blockquote>"
            )
    if page.sources:
        parts.append("<h2>Sources</h2><ul>")
        for s in page.sources:
            parts.append(
                f"<li><a href='{html.escape(s['url'])}'>{html.escape(s['title'])}</a> "
                f"— r/{html.escape(s['subreddit'])} ({s['score']} upvotes)</li>"
            )
        parts.append("</ul>")
    parts.append(f"<p class='gen'>{html.escape(_generated_line(page))}</p>")
    return _HTML_SHELL.format(
        title=html.escape(page.title) + " — gttp",
        head_script=_THEME_INIT,
        header=_render_header("../"),
        main_class=" reading",
        body="".join(parts),
        footer=_render_footer("../"),
        theme_toggle=_THEME_TOGGLE,
        script="",
    )


_HTML_SHELL = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<link rel="icon" type="image/x-icon" href="/favicon.ico">
<link rel="icon" type="image/png" sizes="32x32" href="/favicon-32x32.png">
<link rel="icon" type="image/png" sizes="16x16" href="/favicon-16x16.png">
<link rel="apple-touch-icon" sizes="180x180" href="/apple-touch-icon.png">
<style>
:root {{
  --bg:#FBF8F4; --surface:#FFFFFF; --surface-2:#F3EEE7;
  --ink:#1B2233; --muted:#63697A;
  --border:rgba(27,34,51,.12); --border-strong:rgba(27,34,51,.22);
  --accent:#E4571C; --accent-ink:#B8420F; --accent-tint:rgba(228,87,28,.10);
  --shadow:0 1px 2px rgba(27,34,51,.06), 0 10px 28px -14px rgba(27,34,51,.20);
  --radius:14px; --radius-sm:9px;
  --font-display:"Iowan Old Style","Palatino Linotype",Palatino,"Book Antiqua",Georgia,"Times New Roman",serif;
  --font-body:system-ui,-apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
  color-scheme:light;
}}
@media (prefers-color-scheme:dark) {{
  :root:not([data-theme]) {{
    --bg:#0F1522; --surface:#161D2C; --surface-2:#1E2637;
    --ink:#ECE9E3; --muted:#98A0B2;
    --border:rgba(236,233,227,.12); --border-strong:rgba(236,233,227,.24);
    --accent:#F97A45; --accent-ink:#FB8C5C; --accent-tint:rgba(249,122,69,.16);
    --shadow:0 1px 2px rgba(0,0,0,.4), 0 12px 32px -16px rgba(0,0,0,.65);
    color-scheme:dark;
  }}
}}
:root[data-theme="dark"] {{
  --bg:#0F1522; --surface:#161D2C; --surface-2:#1E2637;
  --ink:#ECE9E3; --muted:#98A0B2;
  --border:rgba(236,233,227,.12); --border-strong:rgba(236,233,227,.24);
  --accent:#F97A45; --accent-ink:#FB8C5C; --accent-tint:rgba(249,122,69,.16);
  --shadow:0 1px 2px rgba(0,0,0,.4), 0 12px 32px -16px rgba(0,0,0,.65);
  color-scheme:dark;
}}
:root[data-theme="light"] {{ color-scheme:light; }}

* {{ box-sizing:border-box; }}
html {{ scroll-behavior:smooth; }}
body {{ font:1.0625rem/1.65 var(--font-body); margin:0;
       background:var(--bg); color:var(--ink);
       -webkit-font-smoothing:antialiased; text-rendering:optimizeLegibility; }}
main.content {{ max-width:64rem; margin:2.5rem auto; padding:0 1.25rem; }}
main.reading {{ max-width:46rem; }}

h1 {{ font-family:var(--font-display); line-height:1.12; letter-spacing:-.01em; font-weight:700; }}
h2 {{ font-family:var(--font-display); font-size:1.4rem; letter-spacing:-.01em;
     margin:2.4rem 0 .6rem; padding-top:.2rem; }}
h2::before {{ content:""; display:block; width:2rem; height:3px; border-radius:2px;
             background:var(--accent); margin-bottom:.6rem; }}
a {{ color:var(--accent-ink); text-decoration-color:var(--border-strong);
    text-underline-offset:2px; }}
a:hover {{ text-decoration-color:var(--accent); }}
.sub {{ color:var(--muted); margin:.4rem 0 0; }}
.author {{ color:var(--muted); }}

.site-header {{ display:flex; align-items:center; justify-content:space-between;
               gap:1rem; padding:.8rem 1.25rem;
               border-bottom:1px solid var(--border); }}
.site-title {{ display:inline-flex; text-decoration:none; }}
.site-title img {{ display:block; width:250px; max-width:56vw; height:auto;
                  background:#fff; border-radius:10px; padding:5px 12px; }}

.theme-toggle {{ display:inline-flex; align-items:center; justify-content:center;
                width:2.4rem; height:2.4rem; flex-shrink:0; cursor:pointer;
                color:var(--ink); background:var(--surface);
                border:1px solid var(--border-strong); border-radius:var(--radius-sm);
                transition:background .15s, border-color .15s, transform .1s; }}
.theme-toggle:hover {{ border-color:var(--accent); background:var(--accent-tint); }}
.theme-toggle:active {{ transform:scale(.94); }}
.theme-toggle .icon-sun {{ display:none; }}
.theme-toggle .icon-moon {{ display:block; }}
:root[data-theme="dark"] .theme-toggle .icon-sun {{ display:block; }}
:root[data-theme="dark"] .theme-toggle .icon-moon {{ display:none; }}
@media (prefers-color-scheme:dark) {{
  :root:not([data-theme]) .theme-toggle .icon-sun {{ display:block; }}
  :root:not([data-theme]) .theme-toggle .icon-moon {{ display:none; }}
}}

.intro {{ margin:.5rem 0 1.75rem; }}
.intro h1 {{ font-size:clamp(2rem,5vw,2.75rem); margin:.2rem 0 0; }}

.search {{ font:inherit; width:100%; box-sizing:border-box; margin:0 0 1.75rem;
          padding:.7rem .9rem; border-radius:var(--radius-sm);
          border:1px solid var(--border-strong);
          background:var(--surface-2); color:inherit; }}
.search::placeholder {{ color:var(--muted); }}
.search:focus-visible {{ outline:2px solid var(--accent); outline-offset:2px;
                        border-color:var(--accent); }}

.books {{ list-style:none; padding:0; margin:0; display:grid;
         grid-template-columns:repeat(auto-fill, minmax(20rem, 1fr)); gap:1rem; }}
.books li {{ display:flex; gap:1rem; align-items:flex-start; margin:0;
            padding:1rem; border-radius:var(--radius);
            background:var(--surface); border:1px solid var(--border);
            box-shadow:var(--shadow);
            transition:transform .15s ease, border-color .15s, box-shadow .15s; }}
.books li:hover {{ transform:translateY(-2px); border-color:var(--accent);
                  box-shadow:0 2px 4px rgba(27,34,51,.08), 0 16px 34px -16px rgba(27,34,51,.30); }}
.card-body {{ min-width:0; flex:1; }}
.card-body a {{ text-decoration:none; color:var(--ink);
               font-family:var(--font-display); font-size:1.08rem;
               line-height:1.25; display:inline-block; }}
.card-body a:hover strong {{ color:var(--accent-ink); }}
.card-body strong {{ font-weight:700; }}
.books p {{ color:var(--muted); margin:.35rem 0 0; font-size:.95rem; }}
.card.empty {{ opacity:.55; }}

.cover {{ display:block; object-fit:cover; aspect-ratio:2 / 3;
         border-radius:8px; border:1px solid var(--border);
         background:var(--surface-2); box-shadow:0 6px 16px -10px rgba(27,34,51,.5); }}
.cover-thumb {{ width:4.75rem; height:auto; flex-shrink:0; }}
.cover-detail {{ width:12rem; height:auto; flex-shrink:0; border-radius:var(--radius);
                box-shadow:0 12px 30px -14px rgba(27,34,51,.55); }}

.book-hero {{ display:grid; grid-template-columns:auto 1fr; gap:1.75rem;
             align-items:start; margin:.5rem 0 2rem; }}
.hero-body h1 {{ margin-top:0; font-size:clamp(1.9rem,4.5vw,2.6rem); }}
blockquote.core {{ font-family:var(--font-display); font-size:1.35rem; line-height:1.4;
                  border-left:3px solid var(--accent); padding:.6rem 0 .6rem 1.1rem;
                  margin:1rem 0 0; background:var(--accent-tint);
                  border-radius:0 8px 8px 0; }}
blockquote {{ border-left:3px solid var(--border-strong); padding:.2rem 0 .2rem 1.1rem;
             margin:1.2rem 0; color:var(--ink); }}
blockquote footer {{ color:var(--muted); font-size:.9rem; margin-top:.4rem; }}

.toc {{ margin:1.5rem 0; padding:1rem 1.2rem; border-radius:var(--radius);
       background:var(--surface); border:1px solid var(--border); }}
.toc-title {{ margin:0 0 .5rem; font-size:.78rem; letter-spacing:.08em;
             text-transform:uppercase; color:var(--muted); font-weight:600; }}
.toc ol {{ margin:0; padding-left:1.3rem; }}
.toc li {{ margin:.25rem 0; }}
.toc a {{ text-decoration:none; color:var(--accent-ink); }}
.toc a:hover {{ text-decoration:underline; }}

.tag {{ display:inline-block; font-size:.75rem; margin:.35rem .35rem 0 0;
       padding:.15rem .55rem; border-radius:1rem; color:var(--muted);
       background:var(--surface-2); border:1px solid var(--border); }}
.tags {{ margin-top:.3rem !important; }}

li[id^='idea-'] {{ scroll-margin-top:3.5rem; margin:.5rem 0; }}
.gen {{ color:var(--muted); font-size:.85rem; margin-top:2.5rem; }}
.back {{ font-size:.9rem; margin:0 0 1.25rem; position:sticky; top:0; z-index:1;
        padding:.5rem 0; background:var(--bg); }}
.back a {{ color:var(--muted); text-decoration:none; }}
.back a:hover {{ color:var(--accent-ink); }}

.site-footer {{ max-width:64rem; margin:3.5rem auto 2rem; padding:1.25rem 1.25rem 0;
               border-top:1px solid var(--border); color:var(--muted); font-size:.85rem; }}
.site-footer a {{ color:var(--accent-ink); }}

:focus-visible {{ outline:2px solid var(--accent); outline-offset:2px; border-radius:4px; }}

@media (max-width:30rem) {{
  .book-hero {{ grid-template-columns:1fr; }}
  .cover-detail {{ width:9rem; }}
  main.content {{ margin:1.5rem auto; }}
}}
@media (prefers-reduced-motion:reduce) {{
  html {{ scroll-behavior:auto; }}
  * {{ transition:none !important; }}
  .books li:hover {{ transform:none; }}
}}
</style>
{head_script}
</head>
<body>{header}<main class="content{main_class}">{body}</main>{footer}{theme_toggle}{script}</body></html>
"""
