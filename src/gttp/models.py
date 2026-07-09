"""Data structures passed between pipeline stages."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class RedditThread:
    """A single Reddit post plus its most useful comments."""

    id: str
    title: str
    subreddit: str
    author: str
    score: int
    permalink: str
    selftext: str
    top_comments: list[str] = field(default_factory=list)

    @property
    def url(self) -> str:
        if self.permalink.startswith("http"):
            return self.permalink
        return f"https://www.reddit.com{self.permalink}"

    @property
    def char_count(self) -> int:
        return len(self.selftext) + sum(len(c) for c in self.top_comments)


@dataclass
class RankedThread:
    thread: RedditThread
    # 0-100 blended score; higher is better.
    score: float
    # Per-dimension LLM scores (0-10) plus rationale, for transparency.
    fidelity: float = 0.0
    actionability: float = 0.0
    comprehensiveness: float = 0.0
    rationale: str = ""


@dataclass
class BookPage:
    """The synthesized, publishable summary for one book."""

    title: str
    author: str | None
    core_idea: str
    bullets: list[str]
    honest_take: str
    quotes: list[dict]  # {"text": ..., "author": ..., "url": ...}
    sources: list[dict]  # {"title": ..., "url": ..., "subreddit": ..., "score": ...}
    generated_by: str = "heuristic"  # "claude" or "heuristic"
