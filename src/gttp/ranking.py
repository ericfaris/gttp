"""Filtering and ranking of candidate threads.

`filter_threads` applies cheap mechanical rules (score, length, not deleted).
`rank_threads` scores what survives on fidelity / actionability /
comprehensiveness — using Claude when a key is available, and a transparent
heuristic otherwise so the pipeline always produces a ranking.
"""

from __future__ import annotations

import json

from .config import MODEL, Book, anthropic_key
from .models import RankedThread, RedditThread

_RANK_SCHEMA = {
    "type": "object",
    "properties": {
        "rankings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "fidelity": {"type": "integer"},
                    "actionability": {"type": "integer"},
                    "comprehensiveness": {"type": "integer"},
                    "rationale": {"type": "string"},
                },
                "required": [
                    "id",
                    "fidelity",
                    "actionability",
                    "comprehensiveness",
                    "rationale",
                ],
                "additionalProperties": False,
            },
        }
    },
    "required": ["rankings"],
    "additionalProperties": False,
}


def filter_threads(threads: list[RedditThread], book: Book) -> list[RedditThread]:
    """Drop low-signal candidates before spending tokens on ranking."""
    kept = [
        t
        for t in threads
        if t.score >= book.min_score
        and t.char_count >= book.min_chars
        and t.selftext not in ("[deleted]", "[removed]", "")
    ]
    kept.sort(key=lambda t: t.score, reverse=True)
    return kept[: book.max_threads_per_book]


def rank_threads(threads: list[RedditThread], book: Book) -> list[RankedThread]:
    if not threads:
        return []
    if anthropic_key():
        try:
            return _rank_with_claude(threads, book)
        except Exception as exc:  # fall back rather than fail the whole build
            print(f"    ranking: Claude call failed ({exc}); using heuristic")
    return _rank_heuristic(threads)


def _rank_with_claude(threads: list[RedditThread], book: Book) -> list[RankedThread]:
    import anthropic

    client = anthropic.Anthropic()
    catalog = "\n\n".join(
        f"[{t.id}] r/{t.subreddit} (score {t.score})\n"
        f"Title: {t.title}\n"
        f"Body: {t.selftext[:1500]}\n"
        f"Comments: {' | '.join(c[:300] for c in t.top_comments[:3])}"
        for t in threads
    )
    prompt = (
        f'You are curating Reddit threads that summarize the book "{book.title}"'
        f'{f" by {book.author}" if book.author else ""}. Score each thread 0-10 on:\n'
        "- fidelity: does it accurately represent the book's actual ideas?\n"
        "- actionability: does it give the reader something concrete to do?\n"
        "- comprehensiveness: does it cover the core, not just one slice?\n\n"
        "Reward honest, opinionated summaries (e.g. 'skip chapters 4-9'); "
        "penalize vague reviews, rants, and thin one-liners.\n\n"
        f"Threads:\n{catalog}"
    )
    resp = client.messages.create(
        model=MODEL,
        max_tokens=4000,
        output_config={"format": {"type": "json_schema", "schema": _RANK_SCHEMA}},
        messages=[{"role": "user", "content": prompt}],
    )
    text = next(b.text for b in resp.content if b.type == "text")
    scored = {r["id"]: r for r in json.loads(text)["rankings"]}
    by_id = {t.id: t for t in threads}

    ranked: list[RankedThread] = []
    for tid, r in scored.items():
        thread = by_id.get(tid)
        if not thread:
            continue
        fidelity = float(r["fidelity"])
        actionability = float(r["actionability"])
        comprehensiveness = float(r["comprehensiveness"])
        # Blend the LLM quality signal (0-30 -> 0-100) with Reddit's crowd
        # signal (log-damped upvotes), weighted toward quality.
        llm = (fidelity + actionability + comprehensiveness) / 30 * 100
        crowd = _crowd_score(thread.score)
        ranked.append(
            RankedThread(
                thread=thread,
                score=round(0.7 * llm + 0.3 * crowd, 1),
                fidelity=fidelity,
                actionability=actionability,
                comprehensiveness=comprehensiveness,
                rationale=r.get("rationale", ""),
            )
        )
    ranked.sort(key=lambda r: r.score, reverse=True)
    return ranked


def _rank_heuristic(threads: list[RedditThread]) -> list[RankedThread]:
    """Deterministic fallback: crowd score plus lightweight content heuristics."""
    ranked = []
    for t in threads:
        text = (t.title + " " + t.selftext).lower()
        # Proxies for actionability/structure without an LLM.
        has_list = any(m in t.selftext for m in ("\n-", "\n1.", "\n*", "•"))
        summary_signal = any(
            w in text for w in ("takeaway", "summary", "key point", "in short", "tl;dr")
        )
        content = _crowd_score(t.score)
        bonus = (15 if has_list else 0) + (15 if summary_signal else 0)
        score = min(100.0, content + bonus)
        ranked.append(
            RankedThread(
                thread=t,
                score=round(score, 1),
                rationale="heuristic: crowd score + structure/summary signals",
            )
        )
    ranked.sort(key=lambda r: r.score, reverse=True)
    return ranked


def _crowd_score(reddit_score: int) -> float:
    """Map raw upvotes to 0-70 with diminishing returns (log scale)."""
    import math

    if reddit_score <= 0:
        return 0.0
    # log10(1000)=3 -> ~70; keeps a 50-upvote post from being buried by a 5000.
    return min(70.0, math.log10(reddit_score + 1) / 3 * 70)
