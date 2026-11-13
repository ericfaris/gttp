"""Suite-wide guards."""

import pytest

from gttp import claude


@pytest.fixture(autouse=True)
def _isolate_usage_log(monkeypatch, tmp_path):
    # Never append test calls to the real .cache/usage.jsonl.
    monkeypatch.setattr(claude, "USAGE_LOG", tmp_path / "usage.jsonl")


@pytest.fixture(autouse=True)
def _isolate_budget(monkeypatch):
    # The budget guard is process-wide state driven by env vars; start every
    # test with no cap, no job id and nothing spent.
    monkeypatch.delenv("GTTP_MAX_BUDGET_USD", raising=False)
    monkeypatch.delenv("GTTP_JOB_ID", raising=False)
    claude.reset_budget()
    yield
    claude.reset_budget()


@pytest.fixture(autouse=True)
def _isolate_openlibrary_cache():
    # The Open Library cache is process-wide; a hit from one test must not
    # stand in for another test's stubbed response.
    from gttp import openlibrary

    openlibrary.clear_cache()
    yield
    openlibrary.clear_cache()
