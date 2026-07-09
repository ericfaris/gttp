"""Render BookPages to Markdown and a minimal static site.

One Markdown file per book keeps the corpus diffable and versionable in the
repo; a small self-contained HTML index makes it browsable without a static
site generator. Point Hugo/Jekyll/Astro at the Markdown if you want more.
"""

from __future__ import annotations

import html
from pathlib import Path

from .config import SITE_DIR
from .models import BookPage


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
    lines.append(f"<sub>Synthesized by: {page.generated_by}</sub>")
    lines.append("")
    return "\n".join(lines)


def write_site(pages: list[BookPage], site_dir: Path = SITE_DIR) -> Path:
    """Write per-book Markdown + HTML and an index page. Returns index path."""
    site_dir.mkdir(parents=True, exist_ok=True)
    books_dir = site_dir / "books"
    books_dir.mkdir(exist_ok=True)

    from .config import slugify

    for page in pages:
        slug = slugify(page.title)
        (books_dir / f"{slug}.md").write_text(render_markdown(page))
        (books_dir / f"{slug}.html").write_text(_render_book_html(page))

    index = site_dir / "index.html"
    index.write_text(_render_index_html(pages))
    return index


def _render_index_html(pages: list[BookPage]) -> str:
    from .config import slugify

    cards = "\n".join(
        f'<li><a href="books/{slugify(p.title)}.html"><strong>{html.escape(p.title)}</strong>'
        f'{f" — {html.escape(p.author)}" if p.author else ""}</a>'
        f'<p>{html.escape(p.core_idea)}</p></li>'
        for p in pages
    )
    return _HTML_SHELL.format(
        title="gttp — Get To The Point",
        body=f"<h1>Get To The Point</h1>"
        f"<p class='sub'>Crowd-vetted self-help summaries, curated from Reddit.</p>"
        f"<ul class='books'>{cards}</ul>",
    )


def _render_book_html(page: BookPage) -> str:
    parts = [f"<p class='back'><a href='../index.html'>← all books</a></p>"]
    parts.append(f"<h1>{html.escape(page.title)}</h1>")
    if page.author:
        parts.append(f"<p class='author'>by {html.escape(page.author)}</p>")
    parts.append(f"<blockquote class='core'>{html.escape(page.core_idea)}</blockquote>")
    if page.bullets:
        parts.append("<h2>The 5-bullet version</h2><ul>")
        parts.extend(f"<li>{html.escape(b)}</li>" for b in page.bullets)
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
    parts.append(f"<p class='gen'>Synthesized by: {page.generated_by}</p>")
    return _HTML_SHELL.format(title=html.escape(page.title) + " — gttp", body="".join(parts))


_HTML_SHELL = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
  :root {{ color-scheme: light dark; }}
  body {{ font: 17px/1.6 -apple-system, system-ui, sans-serif; max-width: 42rem;
         margin: 2rem auto; padding: 0 1.2rem; }}
  h1 {{ line-height: 1.2; }}
  .sub, .author {{ color: #888; }}
  .books {{ list-style: none; padding: 0; }}
  .books li {{ margin: 1.4rem 0; }}
  .books p {{ color: #888; margin: .3rem 0 0; }}
  blockquote.core {{ font-size: 1.25rem; border-left: 3px solid #e0703c;
                     padding-left: 1rem; margin-left: 0; }}
  blockquote {{ border-left: 3px solid #ccc; padding-left: 1rem; margin-left: 0; }}
  blockquote footer {{ color: #888; font-size: .9rem; }}
  .gen, .back {{ color: #aaa; font-size: .85rem; }}
  a {{ color: #e0703c; }}
</style></head>
<body>{body}</body></html>
"""
