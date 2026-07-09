"""Turn the top-ranked threads into one publishable BookPage.

This is the value-add: not a bookmark folder, but a single distilled page that
keeps Reddit's honest voice. Claude does the synthesis when a key is present;
otherwise a heuristic assembles a serviceable page straight from the threads so
the pipeline still produces output.
"""

from __future__ import annotations

import json

from .config import MODEL, Book, anthropic_key
from .models import BookPage, RankedThread

_PAGE_SCHEMA = {
    "type": "object",
    "properties": {
        "core_idea": {"type": "string"},
        "bullets": {"type": "array", "items": {"type": "string"}},
        "honest_take": {"type": "string"},
        "quotes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "author": {"type": "string"},
                    "url": {"type": "string"},
                },
                "required": ["text", "author", "url"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["core_idea", "bullets", "honest_take", "quotes"],
    "additionalProperties": False,
}


def synthesize(book: Book, ranked: list[RankedThread]) -> BookPage:
    top = ranked[: book.synthesis_top_n]
    sources = [
        {
            "title": r.thread.title,
            "url": r.thread.url,
            "subreddit": r.thread.subreddit,
            "score": r.thread.score,
        }
        for r in top
    ]
    if not top:
        return BookPage(
            title=book.title,
            author=book.author,
            core_idea="No qualifying Reddit summaries found yet.",
            bullets=[],
            honest_take="",
            quotes=[],
            sources=[],
        )

    if anthropic_key():
        try:
            page = _synthesize_with_claude(book, top)
            page.sources = sources
            return page
        except Exception as exc:
            print(f"    synthesis: Claude call failed ({exc}); using heuristic")

    return _synthesize_heuristic(book, top, sources)


def _synthesize_with_claude(book: Book, top: list[RankedThread]) -> BookPage:
    import anthropic

    client = anthropic.Anthropic()
    threads_blob = "\n\n---\n\n".join(
        f"Source: {r.thread.url} (r/{r.thread.subreddit}, u/{r.thread.author}, "
        f"score {r.thread.score})\n"
        f"Title: {r.thread.title}\n"
        f"Body: {r.thread.selftext[:2500]}\n"
        f"Comments: {' | '.join(c[:400] for c in r.thread.top_comments[:4])}"
        for r in top
    )
    prompt = (
        f'Synthesize these Reddit threads about "{book.title}"'
        f'{f" by {book.author}" if book.author else ""} into one "get to the '
        "point\" page a reader can absorb in two minutes. Requirements:\n"
        "- core_idea: the single load-bearing idea of the book, 1-2 sentences.\n"
        "- bullets: the 5-bullet version — concrete, actionable, no filler.\n"
        "- honest_take: what Reddit really thinks, including the sharpest "
        "criticism (e.g. which chapters to skip). Keep the candid, opinionated "
        "voice — do not sand it into generic praise.\n"
        "- quotes: 1-3 verbatim lines worth quoting, each with the redditor's "
        "username as author and the thread url. Quote exactly; do not invent.\n\n"
        "Write in your own words except for the quotes. Be specific to THIS "
        "book, not self-help in general.\n\n"
        f"Threads:\n{threads_blob}"
    )
    resp = client.messages.create(
        model=MODEL,
        max_tokens=4000,
        thinking={"type": "adaptive"},
        output_config={
            "effort": "high",
            "format": {"type": "json_schema", "schema": _PAGE_SCHEMA},
        },
        messages=[{"role": "user", "content": prompt}],
    )
    text = next(b.text for b in resp.content if b.type == "text")
    data = json.loads(text)
    return BookPage(
        title=book.title,
        author=book.author,
        core_idea=data["core_idea"],
        bullets=data["bullets"][:5],
        honest_take=data["honest_take"],
        quotes=data.get("quotes", [])[:3],
        sources=[],
        generated_by="claude",
    )


def _synthesize_heuristic(
    book: Book, top: list[RankedThread], sources: list[dict]
) -> BookPage:
    """Assemble a page directly from the top thread without an LLM."""
    best = top[0].thread
    bullets: list[str] = []
    for line in best.selftext.splitlines():
        stripped = line.lstrip("-*•0123456789. \t")
        if 20 <= len(stripped) <= 200 and stripped != line.strip():
            bullets.append(stripped.strip())
        if len(bullets) >= 5:
            break
    if not bullets:
        # Fall back to the first few sentences of the highest-ranked thread.
        sentences = best.selftext.replace("\n", " ").split(". ")
        bullets = [s.strip() + "." for s in sentences[:5] if len(s.strip()) > 20]

    quotes = []
    if best.top_comments:
        quotes.append(
            {"text": best.top_comments[0][:280], "author": "a commenter", "url": best.url}
        )

    return BookPage(
        title=book.title,
        author=book.author,
        core_idea=(best.title.strip() + "."),
        bullets=bullets[:5],
        honest_take=(
            f"Distilled from the top {len(top)} Reddit threads. "
            "Enable an Anthropic API key for a synthesized, opinionated take."
        ),
        quotes=quotes,
        sources=sources,
        generated_by="heuristic",
    )
