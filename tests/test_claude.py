"""Claude response handling and model recording. A fake client stands in for
the API, so nothing here makes a network call."""

import json
from types import SimpleNamespace

import anthropic
import pytest

from gttp import enrich, synthesize
from gttp.claude import response_text
from gttp.config import MODEL, Book
from gttp.models import BookPage, RankedThread, RedditThread
from gttp.publish import render_markdown


def _resp(text=None, stop_reason="end_turn", model=MODEL, stop_details=None):
    content = [SimpleNamespace(type="thinking", thinking="")]
    if text is not None:
        content.append(SimpleNamespace(type="text", text=text))
    return SimpleNamespace(
        content=content, stop_reason=stop_reason, model=model, stop_details=stop_details
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


def test_response_text_skips_thinking_blocks():
    assert response_text(_resp('{"a": 1}')) == '{"a": 1}'


def test_response_text_reports_refusal():
    resp = _resp(stop_reason="refusal", stop_details=SimpleNamespace(category="cyber"))
    with pytest.raises(RuntimeError, match="declined.*cyber"):
        response_text(resp)


def test_response_text_reports_truncation():
    with pytest.raises(RuntimeError, match="max_tokens"):
        response_text(_resp('{"core_', stop_reason="max_tokens"))


def test_response_text_without_text_block():
    with pytest.raises(RuntimeError, match="no text"):
        response_text(_resp())


def test_synthesis_uses_configured_model_and_records_served_model(monkeypatch):
    out = {"core_idea": "c", "bullets": ["b"], "honest_take": "h", "quotes": []}
    fake = _patch_client(monkeypatch, _resp(json.dumps(out), model="claude-sonnet-5-5"))
    thread = RedditThread(
        id="t1", title="t", subreddit="s", author="u", score=1, permalink="/r/s/t1",
        selftext="body", top_comments=[],
    )
    page = synthesize._synthesize_with_claude(
        Book(title="Grit", author=None), [RankedThread(thread=thread, score=1.0)]
    )
    assert fake.calls[0]["model"] == MODEL
    assert page.model == "claude-sonnet-5-5"
    assert page.generated_by == "claude"


def test_enrich_records_served_model(monkeypatch):
    out = {"verdict": "v", "try_today": "t", "for_who": "f", "skip_if": "s", "take_points": ["p"]}
    _patch_client(monkeypatch, _resp(json.dumps(out), model="claude-sonnet-5-5"))
    monkeypatch.setattr(enrich, "anthropic_key", lambda: "test-key")
    page = BookPage(
        title="Grit", author=None, core_idea="c", bullets=["b"], honest_take="h",
        quotes=[], sources=[], generated_by="claude",
    )
    assert enrich.enrich(page).enrich_model == "claude-sonnet-5-5"


def test_generated_line_names_the_model_only_when_known():
    base = dict(
        title="Grit", author=None, core_idea="c", bullets=[], honest_take="",
        quotes=[], sources=[], generated_by="claude", generated_at="2026-10-25",
    )
    assert "Synthesized by: claude on 2026-10-25" in render_markdown(BookPage(**base))
    assert "Synthesized by: claude (claude-sonnet-5-5) on 2026-10-25" in render_markdown(
        BookPage(**base, model="claude-sonnet-5-5")
    )


def test_old_cached_pages_still_load(tmp_path, monkeypatch):
    """Pages written before the model fields existed must load unchanged."""
    from gttp import cache

    monkeypatch.setattr(cache, "PAGES_DIR", tmp_path)
    (tmp_path / "grit.json").write_text(json.dumps({
        "title": "Grit", "author": None, "core_idea": "c", "bullets": ["b"],
        "honest_take": "h", "quotes": [], "sources": [], "generated_by": "claude",
    }))
    page = cache.load_cached_page("grit")
    assert page.model == "" and page.enrich_model == ""


def test_record_usage_logs_tokens_and_cost(capsys):
    from gttp import claude

    resp = SimpleNamespace(
        model="claude-sonnet-5-5",
        usage=SimpleNamespace(input_tokens=30000, output_tokens=5000,
                              cache_creation_input_tokens=0, cache_read_input_tokens=0),
    )
    claude.record_usage(resp, "rank", "Grit")
    line = json.loads(claude.USAGE_LOG.read_text())
    assert line["book"] == "Grit" and line["step"] == "rank"
    assert line["usd"] == 0.11  # 30k x $2/M + 5k x $10/M
    assert "~$0.110" in capsys.readouterr().out


def test_record_usage_tolerates_missing_usage_and_unknown_model():
    from gttp import claude

    claude.record_usage(SimpleNamespace(model="x"), "rank", "Grit")  # no usage: no-op
    resp = SimpleNamespace(model="mystery", usage=SimpleNamespace(input_tokens=1, output_tokens=2))
    claude.record_usage(resp, "rank", "Grit")
    assert json.loads(claude.USAGE_LOG.read_text())["usd"] is None
