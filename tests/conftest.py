"""Suite-wide guards."""

import pytest

from gttp import claude


@pytest.fixture(autouse=True)
def _isolate_usage_log(monkeypatch, tmp_path):
    # Never append test calls to the real .cache/usage.jsonl.
    monkeypatch.setattr(claude, "USAGE_LOG", tmp_path / "usage.jsonl")
