"""Hard per-job Claude budget (GTTP_MAX_BUDGET_USD). A fake client stands in
for the API, so nothing here makes a network call."""

import json
import random
from types import SimpleNamespace

import anthropic
import pytest

from gttp import cache, claude, enrich, ranking, synthesize
from gttp.claude import BudgetExceeded, check_budget, record_usage, worst_case_usd
from gttp.config import MODEL, Book
from gttp.models import RankedThread, RedditThread
from gttp.pipeline import build_book
from gttp.reddit_client import FixtureRedditClient


def _usage_resp(tin=1000, tout=500, model=MODEL, text="{}"):
    return SimpleNamespace(
        model=model,
        stop_reason="end_turn",
        content=[SimpleNamespace(type="text", text=text)],
        usage=SimpleNamespace(
            input_tokens=tin, output_tokens=tout,
            cache_creation_input_tokens=0, cache_read_input_tokens=0,
        ),
    )


class _FakeClient:
    def __init__(self, resp):
        self.calls = []
        self.messages = SimpleNamespace(create=self._create)
        self._resp = resp

    def _create(self, **kw):
        self.calls.append(kw)
        return self._resp


def _patch_client(monkeypatch, resp):
    fake = _FakeClient(resp)
    monkeypatch.setattr(anthropic, "Anthropic", lambda *a, **k: fake)
    return fake


def _thread():
    return RedditThread(
        id="t1", title="t", subreddit="s", author="u", score=1, permalink="/r/s/t1",
        selftext="body", top_comments=[],
    )


SYNTH_OUT = json.dumps({"core_idea": "c", "bullets": ["b"], "honest_take": "h", "quotes": []})


def _usage_lines():
    if not claude.USAGE_LOG.exists():
        return []
    return [json.loads(line) for line in claude.USAGE_LOG.read_text().splitlines()]


def test_no_cap_means_no_guard(monkeypatch):
    fake = _patch_client(monkeypatch, _usage_resp(text=SYNTH_OUT))
    synthesize._synthesize_with_claude(Book(title="Grit"), [RankedThread(_thread(), 1.0)])
    assert len(fake.calls) == 1
    assert _usage_lines()[0]["job"] is None


def test_tiny_cap_refuses_before_calling(monkeypatch):
    monkeypatch.setenv("GTTP_MAX_BUDGET_USD", "0.01")
    monkeypatch.setenv("GTTP_JOB_ID", "1794067200000-1a2b3c4d")
    fake = _patch_client(monkeypatch, _usage_resp(text=SYNTH_OUT))
    with pytest.raises(BudgetExceeded):
        synthesize._synthesize_with_claude(Book(title="Grit"), [RankedThread(_thread(), 1.0)])
    assert fake.calls == []
    (line,) = _usage_lines()
    assert line["step"] == "budget_refused" and line["refused_step"] == "synthesize"
    assert line["job"] == "1794067200000-1a2b3c4d"
    assert line["usd"] == 0 and line["limit"] == 0.01


def test_trip_is_sticky_across_steps(monkeypatch):
    monkeypatch.setenv("GTTP_MAX_BUDGET_USD", "0.20")
    with pytest.raises(BudgetExceeded):
        check_budget("x" * 200_000, 8000, "rank", "Grit")  # worst case ~$0.49
    # A call that would fit on its own is still refused once tripped.
    with pytest.raises(BudgetExceeded, match="already exhausted"):
        check_budget("small", 100, "synthesize", "Grit")


def test_spend_accumulates_until_the_cap(monkeypatch):
    monkeypatch.setenv("GTTP_MAX_BUDGET_USD", "0.30")
    # Each call: worst case = (5 + 4000) x $2/M + 12000 x $10/M ~ $0.128;
    # actual 10k in / 10k out = $0.12.
    resp = _usage_resp(tin=10_000, tout=10_000, text=SYNTH_OUT)
    fake = _patch_client(monkeypatch, resp)

    def prompt_call():
        check_budget("hello", 12000, "synthesize", "Grit")
        record_usage(fake.messages.create(), "synthesize", "Grit")

    prompt_call()  # spent 0.12
    prompt_call()  # 0.12 + 0.128 <= 0.30 -> allowed, spent 0.24
    with pytest.raises(BudgetExceeded):
        prompt_call()  # 0.24 + 0.128 > 0.30
    assert len(fake.calls) == 2
    assert claude._spent_usd == pytest.approx(0.24)


@pytest.mark.parametrize("value", ["abc", "0", "-1", "nan"])
def test_invalid_cap_fails_closed(monkeypatch, value):
    monkeypatch.setenv("GTTP_MAX_BUDGET_USD", value)
    with pytest.raises(BudgetExceeded, match="invalid GTTP_MAX_BUDGET_USD"):
        check_budget("hi", 10, "rank", "Grit")


def test_unknown_model_price_fails_closed(monkeypatch):
    monkeypatch.setenv("GTTP_MAX_BUDGET_USD", "5")
    with pytest.raises(BudgetExceeded, match="no price"):
        check_budget("hi", 10, "rank", "Grit", model="mystery-model")


def test_unknown_cost_under_a_cap_trips(monkeypatch):
    monkeypatch.setenv("GTTP_MAX_BUDGET_USD", "5")
    record_usage(SimpleNamespace(model="mystery", usage=SimpleNamespace(
        input_tokens=1, output_tokens=1)), "rank", "Grit")
    with pytest.raises(BudgetExceeded):
        check_budget("hi", 10, "synthesize", "Grit")


def test_enrich_is_guarded(monkeypatch):
    monkeypatch.setenv("GTTP_MAX_BUDGET_USD", "0.001")
    fake = _patch_client(monkeypatch, _usage_resp())
    monkeypatch.setattr(enrich, "anthropic_key", lambda: "test-key")
    from gttp.models import BookPage

    page = BookPage(title="Grit", author=None, core_idea="c", bullets=["b"],
                    honest_take="h", quotes=[], sources=[], generated_by="claude")
    with pytest.raises(BudgetExceeded):
        enrich.enrich(page)
    assert fake.calls == []


def test_build_book_over_budget_falls_back_without_calling(monkeypatch, tmp_path):
    monkeypatch.setattr(cache, "THREADS_DIR", tmp_path / "threads")
    monkeypatch.setattr(cache, "PAGES_DIR", tmp_path / "pages")
    monkeypatch.setattr(ranking, "anthropic_key", lambda: "test-key")
    monkeypatch.setattr(synthesize, "anthropic_key", lambda: "test-key")
    monkeypatch.setenv("GTTP_MAX_BUDGET_USD", "0.0001")
    fake = _patch_client(monkeypatch, _usage_resp(text=SYNTH_OUT))
    book = Book(title="Atomic Habits", author="James Clear",
                subreddits=["BettermentBookClub"], queries=["{title} summary"],
                min_score=10, min_chars=100)

    page = build_book(book, FixtureRedditClient())  # must not raise

    assert fake.calls == []
    assert page.generated_by == "heuristic"
    refused = [line["refused_step"] for line in _usage_lines()]
    assert refused == ["rank"]  # tripped once; synthesis then refused silently


def test_worst_case_bounds_the_recorded_cost():
    """For any prompt, a call whose input is at most bytes + overhead tokens
    and whose output is at most max_tokens costs no more than the reserve."""
    rng = random.Random(1234)
    alphabet = "abc xyz—é漢🙂\n"
    for _ in range(50):
        prompt = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 5000)))
        max_tokens = rng.randint(1, 16000)
        worst = worst_case_usd(prompt, max_tokens)
        tin = len(prompt.encode("utf-8")) + rng.randint(0, claude.PROMPT_OVERHEAD_TOKENS)
        tout = rng.randint(0, max_tokens)
        claude.reset_budget()
        record_usage(_usage_resp(tin=tin, tout=tout), "rank", "Grit")
        assert claude._spent_usd <= worst + 1e-12
