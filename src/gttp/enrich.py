"""Add the "get to the point" layer to a synthesized page.

A finished page tells you what a book says; this layer tells you what to do
with it: a one-line verdict, one thing to try today, who it's for, and the
honest take broken into short points. It is derived only from the page's own
content (no Reddit fetch), and it only ever *adds* fields — core_idea,
bullets, honest_take, quotes and sources are left byte-for-byte untouched —
so it is safe to run on finalized pages without `--force`.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import date

from .claude import record_usage, response_text
from .config import MODEL, anthropic_key
from .models import BookPage

_ENRICH_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string"},
        "try_today": {"type": "string"},
        "for_who": {"type": "string"},
        "skip_if": {"type": "string"},
        "take_points": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["verdict", "try_today", "for_who", "skip_if", "take_points"],
    "additionalProperties": False,
}


def is_enriched(page: BookPage) -> bool:
    return bool(page.verdict and page.try_today)


def can_enrich(page: BookPage) -> bool:
    """Only real Claude summaries are worth enriching; placeholders, error
    pages and heuristic fallbacks have nothing trustworthy to build on."""
    return page.generated_by == "claude" and bool(page.bullets)


def enrich(page: BookPage) -> BookPage:
    """Return a copy of `page` with the enrichment fields filled in.

    Raises if no API key is set or the Claude call fails — callers decide
    whether that's fatal (the CLI reports it; the build pipeline ignores it).
    """
    if not anthropic_key():
        raise RuntimeError("ANTHROPIC_API_KEY is not set")
    data = _enrich_with_claude(page)
    return replace(
        page,
        verdict=data["verdict"].strip(),
        try_today=data["try_today"].strip(),
        for_who=data["for_who"].strip(),
        skip_if=data["skip_if"].strip(),
        take_points=[p.strip() for p in data["take_points"] if p.strip()][:4],
        enriched_at=date.today().isoformat(),
        enrich_model=data.get("model", ""),
    )


def _enrich_with_claude(page: BookPage) -> dict:
    import anthropic

    client = anthropic.Anthropic()
    bullets = "\n".join(f"- {b}" for b in page.bullets)
    prompt = (
        f'Here is a crowd-sourced summary of "{page.title}"'
        f'{f" by {page.author}" if page.author else ""}, distilled from '
        "Reddit. The reader has collected summaries like this but never acts "
        "on them. Turn it into something they will actually use. "
        "Requirements:\n"
        "- verdict: one sentence, at most 20 words. Should they read the "
        "whole book, or is this page enough — and why. Blunt, like a friend.\n"
        "- try_today: ONE concrete action they can do today in under 10 "
        "minutes, taken from the ideas below. Start with a verb. Exactly one "
        "action, not a menu of options — specific enough that there is no "
        "question what to do. 1-2 sentences.\n"
        "- for_who: one short sentence — the reader who gets the most out of "
        "this book.\n"
        "- skip_if: one short sentence — who can safely skip it.\n"
        "- take_points: the honest take rewritten as 3-4 short points, one "
        "sentence each, keeping its candid voice. Do not add claims that are "
        "not in it.\n\n"
        "Use only what is in the summary below; do not invent facts about "
        "the book.\n\n"
        f"Core idea: {page.core_idea}\n\n"
        f"Key ideas:\n{bullets}\n\n"
        f"Honest take: {page.honest_take}"
    )
    resp = client.messages.create(
        model=MODEL,
        # Same headroom as synthesis: adaptive thinking spends tokens before
        # the JSON, and a tight cap returns empty output.
        max_tokens=8000,
        thinking={"type": "adaptive"},
        output_config={
            "effort": "medium",
            "format": {"type": "json_schema", "schema": _ENRICH_SCHEMA},
        },
        messages=[{"role": "user", "content": prompt}],
    )
    record_usage(resp, "enrich", page.title)
    data = json.loads(response_text(resp))
    data["model"] = resp.model
    return data
