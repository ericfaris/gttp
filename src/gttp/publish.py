"""Render BookPages to Markdown and a minimal static site.

One Markdown file per book keeps the corpus diffable and versionable in the
repo; a small self-contained HTML index makes it browsable without a static
site generator. Point Hugo/Jekyll/Astro at the Markdown if you want more.
"""

from __future__ import annotations

import hashlib
import html
import json
import shutil
from datetime import date
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
    if page.verdict:
        lines.append(f"**Verdict:** {page.verdict}")
        lines.append("")
    lines.append(f"> {page.core_idea}")
    lines.append("")
    if page.try_today:
        lines.append("## Try this today")
        lines.append(page.try_today)
        lines.append("")
    if page.for_who:
        lines.append(f"- **For:** {page.for_who}")
    if page.skip_if:
        lines.append(f"- **Skip it if:** {page.skip_if}")
    if page.for_who or page.skip_if:
        lines.append("")
    if page.bullets:
        lines.append("## The 5-bullet version")
        lines.extend(f"- {b}" for b in page.bullets)
        lines.append("")
    if page.honest_take:
        lines.append("## The honest take")
        if page.take_points:
            lines.extend(f"- {t}" for t in page.take_points)
            lines.append("")
        lines.append(page.honest_take)
        lines.append("")
    if page.quotes:
        lines.append("## Worth quoting")
        for q in page.quotes:
            lines.append(f"> {q['text']}")
            lines.append(">")
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
            elif asset.is_dir():  # e.g. static/fonts/
                shutil.copytree(asset, site_dir / asset.name, dirs_exist_ok=True)
    css_version = _css_version(site_dir)
    showcase = site_dir / "design-system.html"
    if showcase.is_file():
        showcase.write_text(_version_css(showcase.read_text(), css_version))

    for page in pages:
        slug = slugify(page.title)
        src = covers.cover_file(slug, covers_dir)
        if src is not None:
            site_covers.mkdir(exist_ok=True)
            shutil.copy2(src, site_covers / f"{slug}.jpg")
        (books_dir / f"{slug}.md").write_text(render_markdown(page))
        (books_dir / f"{slug}.html").write_text(
            _version_css(_render_book_html(page, covers_dir), css_version)
        )

    index = site_dir / "index.html"
    index.write_text(_version_css(_render_index_html(pages, covers_dir), css_version))
    return index


_CSS_LINK = '<link rel="stylesheet" href="/style.css">'


def _css_version(site_dir: Path) -> str:
    """Short content hash of the built stylesheet. The CDN caches style.css
    for hours, so pages link `/style.css?v=<hash>`: a changed stylesheet gets
    a new URL and is never served stale against new markup."""
    css = site_dir / "style.css"
    if not css.is_file():
        return ""
    return hashlib.sha256(css.read_bytes()).hexdigest()[:10]


def _version_css(page_html: str, version: str) -> str:
    if not version:
        return page_html
    return page_html.replace(
        _CSS_LINK, f'<link rel="stylesheet" href="/style.css?v={version}">'
    )


def _has_summary(page: BookPage) -> bool:
    return bool(page.bullets or page.sources)


def _generated_line(page: BookPage) -> str:
    if page.generated_at:
        return f"Synthesized by: {page.generated_by} on {page.generated_at}"
    return f"Synthesized by: {page.generated_by}"


def _render_header(root: str) -> str:
    return (
        "<header class='site-header'>"
        f"<a class='site-title' href='{root}index.html'>"
        f"<img class='logo-light' src='{root}logo.svg' alt='gttp — get to the point' width='128' height='60'>"
        f"<img class='logo-dark' src='{root}logo-dark.svg' alt='gttp — get to the point' width='128' height='60'>"
        "</a>"
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
    author = (
        f'<span class="card-author">{html.escape(page.author)}</span>'
        if page.author else ""
    )
    subs = list(dict.fromkeys(s["subreddit"] for s in page.sources))[:3]
    tags = "".join(
        f'<span class="tag">r/{html.escape(sub)}</span>' for sub in subs
    )
    cover = _cover_tile(page, covers_dir, "", "cover cover-thumb")
    return (
        f'<li class="{cls}" data-search="{search}" data-slug="{slugify(page.title)}">'
        f'{cover}'
        f'<div class="card-body">'
        f'<a href="books/{slugify(page.title)}.html">'
        f"<strong>{html.escape(page.title)}</strong>{author}</a>"
        f'<p class="card-idea">{html.escape(page.core_idea)}</p>'
        f'{f"<p class=tags>{tags}</p>" if tags else ""}'
        f'</div></li>'
    )


_SPARK_SVG = (
    "<svg width='18' height='18' viewBox='0 0 24 24' aria-hidden='true'>"
    "<path d='M12 2q1.2 8.8 10 10q-8.8 1.2-10 10q-1.2-8.8-10-10q8.8-1.2 10-10z' "
    "fill='currentColor'/></svg>"
)
_SHUFFLE_SVG = (
    "<svg width='18' height='18' viewBox='0 0 24 24' fill='none' stroke='currentColor' "
    "stroke-width='2.2' stroke-linecap='round' stroke-linejoin='round' aria-hidden='true'>"
    "<path d='M3 6h3.5c2 0 3.2 1 4.3 2.6l2.4 3.8C14.3 14 15.5 15 17.5 15H21'/>"
    "<path d='M3 15h3.5c1.3 0 2.2-.4 3-1.2M14 7.2c.9-.8 1.9-1.2 3.5-1.2H21'/>"
    "<path d='M18 3l3 3-3 3M18 12l3 3-3 3'/></svg>"
)


def _idea_list(pages: list[BookPage], covers_dir: Path) -> list[dict]:
    """The enriched books that can be "today's idea", as plain data for the
    index's shuffle script."""
    ideas = []
    for page in pages:
        if not (page.verdict and page.try_today):
            continue
        slug = slugify(page.title)
        has_cover = covers.cover_file(slug, covers_dir) is not None
        ideas.append({
            "slug": slug,
            "title": page.title,
            "author": page.author or "",
            "verdict": page.verdict,
            "try": page.try_today,
            "cover": f"covers/{slug}.jpg" if has_cover else "",
        })
    return ideas


def pick_today(ideas: list[dict], today: date | None = None) -> int:
    """Index of the day's idea: rotates through the list one per day. The
    index page's script uses the same formula with the viewer's local date."""
    if not ideas:
        return -1
    day = (today or date.today()).toordinal() - date(1970, 1, 1).toordinal()
    return day % len(ideas)


def _render_today(idea: dict) -> str:
    e = html.escape
    cover = (
        f'<img class="cover cover-detail" id="today-cover" src="{e(idea["cover"])}" '
        f'alt="" width="144" height="216">'
        if idea["cover"] else '<img class="cover cover-detail" id="today-cover" alt="" hidden>'
    )
    href = f'books/{e(idea["slug"])}.html'
    author = f'by {e(idea["author"])}' if idea["author"] else ""
    return (
        '<section class="today rise" id="today" aria-labelledby="today-title">'
        f'<a href="{href}" id="today-cover-link" tabindex="-1" aria-hidden="true">{cover}</a>'
        '<div class="today-body">'
        f'<p class="today-label hand">{_SPARK_SVG}Today\'s idea</p>'
        f'<h2 id="today-title"><a id="today-link" href="{href}">{e(idea["title"])}</a></h2>'
        f'<p class="author" id="today-author">{author}</p>'
        f'<p class="verdict"><span id="today-verdict">{e(idea["verdict"])}</span></p>'
        f'<p class="today-try"><b>Try today</b><span id="today-try">{e(idea["try"])}</span></p>'
        '<div class="today-actions">'
        f'<a class="btn btn-primary" id="today-go" href="{href}">Read it in 2 minutes</a>'
        f'<button class="btn btn-ghost" id="shuffle" type="button">{_SHUFFLE_SVG}Shuffle</button>'
        "</div></div></section>"
    )


# Index behaviour: search filter, today's-idea rotation + shuffle, and the
# "Tried" badge for books the viewer ticked off on their page.
_INDEX_SCRIPT = """<script>(function(){
var q=document.getElementById('q');
if(q){var items=document.querySelectorAll('.books li');var none=document.getElementById('no-results');
q.addEventListener('input',function(){var v=q.value.toLowerCase();var shown=0;
items.forEach(function(li){var hit=li.dataset.search.includes(v);li.hidden=!hit;if(hit)shown++;});
if(none)none.hidden=shown>0;});}
function tried(slug){try{return localStorage.getItem('gttp-tried-'+slug)==='1';}catch(e){return false;}}
document.querySelectorAll('.books li[data-slug]').forEach(function(li){
if(!tried(li.dataset.slug))return;var b=document.createElement('p');b.className='tried-badge';
b.textContent='\u2713 Tried it';li.querySelector('.card-body').appendChild(b);});
var data=document.getElementById('ideas');var card=document.getElementById('today');
if(!data||!card)return;var ideas=JSON.parse(data.textContent);if(!ideas.length)return;
var d=new Date();var day=Math.floor(Date.UTC(d.getFullYear(),d.getMonth(),d.getDate())/864e5);
var cur=day%ideas.length;
function $(id){return document.getElementById(id);}
function show(i,anim){var it=ideas[i];cur=i;var href='books/'+it.slug+'.html';
$('today-link').textContent=it.title;$('today-link').href=href;$('today-go').href=href;
$('today-cover-link').href=href;$('today-author').textContent=it.author?'by '+it.author:'';
$('today-verdict').textContent=it.verdict;$('today-try').textContent=it.try;
var c=$('today-cover');if(it.cover){c.src=it.cover;c.hidden=false;}else{c.hidden=true;}
if(anim){card.classList.remove('flicker');void card.offsetWidth;card.classList.add('flicker');
var v=$('today-verdict');v.style.animation='none';void v.offsetWidth;v.style.animation='';}}
show(cur,false);
$('shuffle').addEventListener('click',function(){if(ideas.length<2)return;
var n=cur;while(n===cur)n=Math.floor(Math.random()*ideas.length);show(n,true);});
})();</script>"""


def _render_index_html(pages: list[BookPage], covers_dir: Path) -> str:
    # The index is a view: stable-sort summary books first, catalog order within.
    ordered = sorted(pages, key=lambda p: not _has_summary(p))
    cards = "\n".join(_render_index_card(p, covers_dir) for p in ordered)
    ideas = _idea_list(pages, covers_dir)
    today = _render_today(ideas[pick_today(ideas)]) if ideas else ""
    # "</" can't appear inside the JSON <script> block; escape the slash.
    data = json.dumps(ideas).replace("</", "<\\/")
    n = sum(1 for p in pages if _has_summary(p))
    body = (
        "<div class='intro rise'>"
        "<h1>Every book has <span class='lit'>one idea</span> worth keeping.</h1>"
        "<p class='sub'>Distilled from Reddit's best threads about each book. "
        "Pick one, get the point in two minutes, and try it today.</p></div>"
        f"{today}"
        f'<script type="application/json" id="ideas">{data}</script>'
        "<div class='section-label'><h2>All the books</h2>"
        f"<p class='hand'>{n} ideas and counting</p></div>"
        '<input type="search" id="q" class="search" placeholder="Search books or ideas…"'
        ' autocomplete="off" aria-label="Search books">'
        f"<ul class='books'>{cards}</ul>"
        '<p id="no-results" class="no-results" hidden>No books match.</p>'
    )
    return _HTML_SHELL.format(
        title="gttp — Get To The Point",
        head_script=_THEME_INIT,
        header=_render_header(""),
        main_class="",
        body=body,
        footer=_render_footer(""),
        theme_toggle=_THEME_TOGGLE,
        script=_INDEX_SCRIPT,
    )


def _render_book_html(page: BookPage, covers_dir: Path) -> str:
    parts = ["<p class='back'><a href='../index.html'>← all books</a></p>"]
    cover = _cover_tile(page, covers_dir, "../", "cover cover-detail")
    hero = [f'<div class="cover-glow">{cover}</div>', '<div class="hero-body">']
    hero.append(f"<h1>{html.escape(page.title)}</h1>")
    if page.author:
        hero.append(f"<p class='author'>by {html.escape(page.author)}</p>")
    if page.verdict:
        hero.append(
            f"<p class='verdict'><span>{html.escape(page.verdict)}</span></p>"
        )
    hero.append(
        f"<blockquote class='core'>{html.escape(page.core_idea)}</blockquote>"
    )
    hero.append("</div>")
    parts.append(f'<div class="book-hero">{"".join(hero)}</div>')
    if page.try_today:
        parts.append(
            "<section class='try' aria-label='Try this today'>"
            "<p class='try-label'>Try this today</p>"
            f"<p class='try-text'>{html.escape(page.try_today)}</p>"
            f"<button class='tried' type='button' aria-pressed='false' "
            f"data-slug='{slugify(page.title)}'><span class='box'></span>"
            "<span class='tried-text'>I tried it</span></button></section>"
        )
    if page.for_who or page.skip_if:
        parts.append("<dl class='fit'>")
        if page.for_who:
            parts.append(f"<div><dt>For</dt><dd>{html.escape(page.for_who)}</dd></div>")
        if page.skip_if:
            parts.append(
                f"<div><dt>Skip it if</dt><dd>{html.escape(page.skip_if)}</dd></div>"
            )
        parts.append("</dl>")
    if page.bullets:
        parts.append("<h2>The 5-bullet version</h2><ul>")
        parts.extend(
            f'<li id="idea-{i}">{html.escape(b)}</li>'
            for i, b in enumerate(page.bullets, 1)
        )
        parts.append("</ul>")
    if page.honest_take:
        parts.append("<h2>The honest take</h2>")
        if page.take_points:
            # The short version up front; the full paragraph is kept, folded.
            parts.append("<ul class='take'>")
            parts.extend(f"<li>{html.escape(t)}</li>" for t in page.take_points)
            parts.append("</ul>")
            parts.append(
                "<details class='more'><summary>Read the full take</summary>"
                f"<p>{html.escape(page.honest_take)}</p></details>"
            )
        else:
            parts.append(f"<p>{html.escape(page.honest_take)}</p>")
    # Supporting material stays on the page, folded away from the main read.
    if page.quotes:
        parts.append(
            "<details class='more'>"
            f"<summary>Worth quoting ({len(page.quotes)})</summary>"
        )
        for q in page.quotes:
            parts.append(
                f"<blockquote>{html.escape(q['text'])}"
                f"<footer>— <a href='{html.escape(q['url'])}'>{html.escape(q['author'])}</a></footer>"
                f"</blockquote>"
            )
        parts.append("</details>")
    if page.sources:
        parts.append(
            "<details class='more'>"
            f"<summary>Sources ({len(page.sources)} Reddit threads)</summary><ul>"
        )
        for s in page.sources:
            parts.append(
                f"<li><a href='{html.escape(s['url'])}'>{html.escape(s['title'])}</a> "
                f"— r/{html.escape(s['subreddit'])} ({s['score']} upvotes)</li>"
            )
        parts.append("</ul></details>")
    parts.append(f"<p class='gen'>{html.escape(_generated_line(page))}</p>")
    return _HTML_SHELL.format(
        title=html.escape(page.title) + " — gttp",
        head_script=_THEME_INIT,
        header=_render_header("../"),
        main_class=" reading",
        body="".join(parts),
        footer=_render_footer("../"),
        theme_toggle=_THEME_TOGGLE,
        script=_BOOK_SCRIPT if page.try_today else "",
    )


# "I tried it": remembered per book in this browser only, with a small burst
# of sparks when ticked. The index reads the same key to show a "Tried" badge.
_BOOK_SCRIPT = """<script>(function(){
var b=document.querySelector('.tried');if(!b)return;var key='gttp-tried-'+b.dataset.slug;
var t=b.querySelector('.tried-text');
function set(on){b.setAttribute('aria-pressed',on?'true':'false');t.textContent=on?'Tried it. Nice.':'I tried it';}
var on=false;try{on=localStorage.getItem(key)==='1';}catch(e){}set(on);
b.addEventListener('click',function(){on=!on;set(on);try{on?localStorage.setItem(key,'1'):localStorage.removeItem(key);}catch(e){}
if(!on||matchMedia('(prefers-reduced-motion: reduce)').matches)return;
var box=b.querySelector('.box'),r=box.getBoundingClientRect(),pr=b.offsetParent.getBoundingClientRect();
for(var i=0;i<10;i++){var s=document.createElement('span');s.className='spark';
var a=Math.PI*2*i/10,dist=28+Math.random()*22;
s.style.left=(r.left-pr.left+r.width/2-4)+'px';s.style.top=(r.top-pr.top+r.height/2-4)+'px';
s.style.setProperty('--dx',Math.cos(a)*dist+'px');s.style.setProperty('--dy',Math.sin(a)*dist+'px');
if(i%2)s.style.background='var(--color-marigold)';b.offsetParent.appendChild(s);
setTimeout(function(x){return function(){x.remove();};}(s),650);}});
})();</script>"""


_HTML_SHELL = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<meta name="theme-color" content="#14213D">
<link rel="icon" href="/favicon.ico" sizes="any">
<link rel="apple-touch-icon" href="/apple-touch-icon.png">
<link rel="manifest" href="/manifest.webmanifest">
<link rel="stylesheet" href="/style.css">
{head_script}
</head>
<body>{header}<main class="content{main_class}">{body}</main>{footer}{theme_toggle}{script}</body></html>
"""
