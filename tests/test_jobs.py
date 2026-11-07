"""Queue validation, state machine and I/O (gttp.jobs)."""

import json
from datetime import datetime, timezone

import pytest

from gttp import jobs
from gttp.jobs import (
    TZ,
    active_count,
    count_today,
    find_duplicate,
    new_entry,
    note,
    read_entries,
    transition,
    validate_field,
    validate_submission,
    write_entry,
)


@pytest.mark.parametrize(
    "value, message",
    [
        (None, "title must be text"),
        (42, "title must be text"),
        ("", "title is required"),
        (" Grit", "title has leading or trailing spaces"),
        ("Grit ", "title has leading or trailing spaces"),
        ("x" * 121, "title must be at most 120 characters"),
        ("Bad\nTitle", "title contains control or invisible characters"),
        ("Bad\tTitle", "title contains control or invisible characters"),
        ("Bad\x00Title", "title contains control or invisible characters"),
        ("Bad​Title", "title contains control or invisible characters"),
        ("Bad‮Title", "title contains control or invisible characters"),
        ("Bad Title", "title contains control or invisible characters"),
        ("-rf", "title must not start with '-'"),
        ("--help", "title must not start with '-'"),
    ],
)
def test_validate_field_messages(value, message):
    assert validate_field("title", value, 120) == message


def test_length_limits_are_inclusive():
    title = "ab - " * 23 + "abcde"  # 120 chars, short slug
    assert len(title) == 120
    assert validate_submission(title, "a" * 80)[0] is None
    assert validate_field("title", "x" * 120, 120) is None
    assert validate_field("author", "x" * 80, 80) is None
    assert validate_field("author", "x" * 81, 80) == "author must be at most 80 characters"
    assert validate_submission("x" * 121, "a")[0] == "title must be at most 120 characters"
    assert validate_submission("x", "a" * 81)[0] == "author must be at most 80 characters"


def test_author_is_required_and_validated():
    assert validate_submission("Grit", "")[0] == "author is required"
    assert validate_submission("Grit", "A\nB")[0] == "author contains control or invisible characters"


def test_validate_submission_slug():
    assert validate_submission("The 7 Habits: Revisited!", "Stephen Covey") == (
        None, "the-7-habits-revisited",
    )
    # Non-ASCII letters would make a non-ASCII page address.
    msg = "title must contain letters or digits (A-Z, 0-9) for its page address"
    assert validate_submission("Café", "x") == (msg, None)
    assert validate_submission("禅", "x") == (msg, None)
    assert validate_submission("!!!", "x") == (msg, None)
    assert validate_submission("x" * 101, "a")[0] == (
        "title is too long for its page address (at most 100 letters and digits)"
    )


def test_new_entry_schema():
    now = datetime(2026, 11, 7, 10, 0, tzinfo=TZ)
    e = new_entry("The Power of Habit", "Charles Duhigg", "the-power-of-habit", now=now)
    assert set(e) == {
        "v", "id", "title", "author", "slug", "state", "message", "created_at",
        "updated_at", "started_at", "finished_at", "commit", "usd",
    }
    assert e["v"] == 1 and e["state"] == "queued" and e["message"] == ""
    assert jobs.ID_RE.match(e["id"]) and e["id"].startswith(str(int(now.timestamp() * 1000)))
    assert e["created_at"] == "2026-11-07T10:00:00-05:00"
    assert e["started_at"] is None and e["commit"] is None and e["usd"] is None


ALL_STATES = ["queued", "running", "done", "failed"]


@pytest.mark.parametrize("old", ALL_STATES)
@pytest.mark.parametrize("new", ALL_STATES)
def test_transitions(old, new):
    e = {**new_entry("Grit", "A", "grit"), "state": old}
    allowed = {("queued", "running"), ("queued", "failed"), ("running", "done"),
               ("running", "failed")}
    if (old, new) in allowed:
        out = transition(e, new, "msg")
        assert out["state"] == new and e["state"] == old  # copy, not mutation
        if new == "running":
            assert out["started_at"]
        if new in ("done", "failed"):
            assert out["finished_at"]
    else:
        with pytest.raises(ValueError):
            transition(e, new)


def test_transition_truncates_message_and_sets_results():
    e = transition(new_entry("Grit", "A", "grit"), "running")
    done = transition(e, "done", "x" * 500, commit="abc1234", usd=0.07)
    assert len(done["message"]) == 300 and done["commit"] == "abc1234" and done["usd"] == 0.07
    with pytest.raises(ValueError):
        transition(e, "done", state="queued")


def test_note_keeps_state():
    e = new_entry("Grit", "A", "grit")
    out = note(e, "waiting: dirty tree")
    assert out["state"] == "queued" and out["message"] == "waiting: dirty tree"


def test_write_entry_is_atomic(tmp_path):
    e = new_entry("Grit", "A", "grit")
    path = write_entry(tmp_path, e)
    assert path.name == f"{e['id']}.json"
    assert [p.name for p in tmp_path.iterdir()] == [path.name]  # no .tmp left
    assert json.loads(path.read_text()) == e
    with pytest.raises(ValueError):
        write_entry(tmp_path, {**e, "id": "../evil"})


def test_read_entries_skips_junk(tmp_path, capsys):
    good = new_entry("Grit", "A", "grit")
    write_entry(tmp_path, good)
    (tmp_path / "notes.json").write_text("{}")  # bad name
    (tmp_path / "1794067200000-deadbeef.json").write_text("not json")
    big = {**new_entry("Big", "A", "big"), "id": "1794067200001-deadbeef",
           "message": "x" * 20000}
    (tmp_path / "1794067200001-deadbeef.json").write_text(json.dumps(big))
    bad_state = {**new_entry("S", "A", "s"), "id": "1794067200002-deadbeef", "state": "hacked"}
    (tmp_path / "1794067200002-deadbeef.json").write_text(json.dumps(bad_state))
    wrong_id = {**new_entry("W", "A", "w"), "id": "1794067200009-deadbeef"}
    (tmp_path / "1794067200003-deadbeef.json").write_text(json.dumps(wrong_id))
    not_str = {**new_entry("N", "A", "n"), "id": "1794067200004-deadbeef", "title": 5}
    (tmp_path / "1794067200004-deadbeef.json").write_text(json.dumps(not_str))
    (tmp_path / ".1794067200005-deadbeef.tmp").write_text("{}")  # in-flight write
    (tmp_path / "1794067200006-deadbeef.json").mkdir()

    assert [e["id"] for e in read_entries(tmp_path)] == [good["id"]]
    out = capsys.readouterr().out
    assert out.count("skipping") == 7
    assert ".tmp" not in out


def test_read_entries_sorted_oldest_first(tmp_path):
    a = new_entry("A", "x", "a", now=datetime(2026, 11, 7, 9, 0, tzinfo=TZ))
    b = new_entry("B", "x", "b", now=datetime(2026, 11, 7, 8, 0, tzinfo=TZ))
    write_entry(tmp_path, a)
    write_entry(tmp_path, b)
    assert [e["title"] for e in read_entries(tmp_path)] == ["B", "A"]
    assert read_entries(tmp_path / "missing") == []


def test_count_today_rolls_over_at_new_york_midnight():
    # 23:59 and 00:01 New York time on consecutive days are the same UTC day.
    late = datetime(2026, 11, 7, 23, 59, tzinfo=TZ)
    early = datetime(2026, 11, 8, 0, 1, tzinfo=TZ)
    assert late.astimezone(timezone.utc).date() == early.astimezone(timezone.utc).date()
    entries = [new_entry("A", "x", "a", now=late), new_entry("B", "x", "b", now=late)]
    assert count_today(entries, now=late) == 2
    assert count_today(entries, now=early) == 0
    entries.append({**new_entry("C", "x", "c", now=early), "state": "failed"})
    assert count_today(entries, now=early) == 1  # failed entries still count


def test_active_count():
    states = ["queued", "running", "done", "failed", "queued"]
    entries = [{**new_entry(s, "x", s), "state": s} for s in states]
    assert active_count(entries) == 3


def test_find_duplicate():
    def entry(title, state):
        return {**new_entry(title, "x", "s"), "state": state}

    assert find_duplicate("grit", [], {"grit"}, "Grit") == "'Grit' is already in the catalog"
    assert find_duplicate("grit", [entry("Grit", "queued")], set(), "Grit") == "'Grit' is already queued"
    assert find_duplicate("grit", [entry("GRIT", "running")], set(), "Grit") == "'Grit' is already queued"
    assert find_duplicate("grit", [entry("Grit", "done")], set(), "Grit") == "'Grit' was already published"
    assert find_duplicate("grit", [entry("Grit", "failed")], set(), "Grit") is None
    assert find_duplicate("grit", [entry("Mindset", "queued")], set(), "Grit") is None
