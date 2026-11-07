"""The host submit worker (gttp.worker) against a throwaway git repo with a
bare origin. The app container is replaced by FakeBuilder, so nothing here
runs docker, the network or Claude."""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import textwrap
from datetime import datetime
from pathlib import Path

import pytest

from gttp import __version__, jobs, worker
from gttp.config import ROOT, add_book, slugify
from gttp.worker import WorkerConfig, run, validate_page

BOOKS = """\
# Catalog for the worker tests.
defaults:
  min_score: 10  # keep this comment
books:
- title: Atomic Habits
  author: James Clear
- title: Deep Work
  author: Cal Newport
"""


def _git(cwd, *args):
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


def _page(title, author, **over):
    d = {
        "title": title, "author": author,
        "core_idea": "The one idea.",
        "bullets": ["Do one thing.", "Then another."],
        "honest_take": "Reddit likes it, mostly.",
        "quotes": [{"text": "q", "author": "u1",
                    "url": "https://www.reddit.com/r/books/comments/abc/x/"}],
        "sources": [{"title": "Notes on it", "url": "https://www.reddit.com/r/books/comments/abc/",
                     "subreddit": "books", "score": 120}],
        "generated_by": "claude", "generated_at": "2026-11-07", "model": "claude-sonnet-5-5",
    }
    d.update(over)
    return d


LIKE_SWITCH_STUB = {
    "title": "The Like Switch", "author": "Jack Schafer",
    "core_idea": "No qualifying Reddit summaries found yet.",
    "bullets": [], "honest_take": "", "quotes": [], "sources": [],
    "generated_by": "heuristic", "generated_at": "2026-11-07", "model": "",
    "verdict": "", "try_today": "", "for_who": "", "skip_if": "", "take_points": [],
    "enriched_at": "", "enrich_model": "",
}


@pytest.fixture
def repo(tmp_path):
    origin = tmp_path / "origin.git"
    work = tmp_path / "work"
    _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    _git(tmp_path, "init", "-q", "-b", "main", str(work))
    for k, v in [("user.name", "Test"), ("user.email", "test@example.com"),
                 ("commit.gpgsign", "false")]:
        _git(work, "config", k, v)
    (work / "books.yaml").write_text(BOOKS)
    pages = work / ".cache" / "pages"
    pages.mkdir(parents=True)
    for title, author in [("Atomic Habits", "James Clear"), ("Deep Work", "Cal Newport")]:
        (pages / f"{slugify(title)}.json").write_text(json.dumps(_page(title, author), indent=2))
    (work / "covers").mkdir()
    (work / "covers" / "atomic-habits.jpg").write_bytes(b"\xff\xd8" + b"a" * 2000)
    shutil.copy(ROOT / ".gitignore", work / ".gitignore")
    (work / "src").mkdir()
    (work / "src" / "dummy.py").write_text("x = 1\n")
    _git(work, "add", "-A")
    _git(work, "commit", "-q", "-m", "init")
    _git(work, "remote", "add", "origin", str(origin))
    _git(work, "push", "-q", "-u", "origin", "main")
    return work


@pytest.fixture
def cfg(repo, tmp_path):
    q = tmp_path / "queue"
    q.mkdir()
    return WorkerConfig(
        repo=repo, queue_dir=q, usage_log=repo / ".cache" / "usage.jsonl",
        lock_path=tmp_path / "worker.lock", sentinel_lock_dir=tmp_path / "locks",
        job_cap_usd=0.50, monthly_cap_usd=5.00, build_timeout_s=60,
        sentinel_wait_s=1.0, sentinel_poll_s=0.05,
    )


class FakeBuilder:
    def __init__(self, cfg, scenario="good"):
        self.cfg = cfg
        self.repo = cfg.repo
        self.scenario = scenario
        self.calls = []
        self.version_override = None
        self.sha_override = None

    def version(self):
        self.calls.append("version")
        return self.version_override or f"gttp {__version__}"

    def catalog_sha256(self):
        self.calls.append("catalog_sha256")
        return self.sha_override or hashlib.sha256((self.repo / "books.yaml").read_bytes()).hexdigest()

    def add(self, title, author):
        self.calls.append(("add", title, author))
        add_book(title, author, path=self.repo / "books.yaml")
        if self.scenario != "no_cover":
            (self.repo / "covers" / f"{slugify(title)}.jpg").write_bytes(b"\xff\xd8" + b"c" * 3000)

    def build(self, title, job_id, cap_usd):
        self.calls.append(("build", title, job_id, cap_usd))
        slug = slugify(title)
        author = self.calls[-2][2]
        page = self.repo / ".cache" / "pages" / f"{slug}.json"
        site_books = self.repo / "site" / "books"
        site_books.mkdir(parents=True, exist_ok=True)
        (site_books / f"{slug}.html").write_text("<html>stub</html>")
        (site_books / f"{slug}.md").write_text("stub")
        s = self.scenario
        if s == "boom":
            raise RuntimeError("container went away")
        if s in ("good", "no_cover"):
            data = _page(title, author)
            self._usage({"t": int(datetime.now().timestamp()), "step": "synthesize",
                         "usd": 0.07, "job": job_id})
        elif s == "like_switch":
            data = dict(LIKE_SWITCH_STUB, title=title, author=author)
        elif s in ("injection", "injection_no_evil"):
            data = _page(title, author)
            other = self.repo / ".cache" / "pages" / "deep-work.json"
            other.write_text(json.dumps(_page("Deep Work", "Cal Newport", core_idea="PWNED")))
            if s == "injection":
                (self.repo / "evil.txt").write_text("ran X")
        elif s == "bad_url":
            data = _page(title, author, quotes=[{"text": "q", "author": "u", "url": "javascript:alert(1)"}])
        elif s == "over_budget":
            self._usage({"t": int(datetime.now().timestamp()), "step": "budget_refused",
                         "refused_step": "rank", "spent": 0.0, "worst": 0.6,
                         "limit": cap_usd, "usd": 0, "job": job_id})
            data = dict(LIKE_SWITCH_STUB, title=title, author=author, bullets=["b"],
                        sources=[{"title": "t", "url": "https://www.reddit.com/r/x/1",
                                  "subreddit": "x", "score": 10}])
        elif s == "slow":
            data = _page(title, author)
        else:  # pragma: no cover
            raise AssertionError(s)
        page.write_text(json.dumps(data, indent=2))

    def render(self):
        self.calls.append("render")

    def _usage(self, line):
        self.cfg.usage_log.parent.mkdir(parents=True, exist_ok=True)
        with self.cfg.usage_log.open("a") as f:
            f.write(json.dumps(line) + "\n")


def ok_checks(repo):
    return True, ""


def enqueue(cfg, title="The Power of Habit", author="Charles Duhigg", **over):
    err, slug = jobs.validate_submission(title, author)
    e = {**jobs.new_entry(title, author, slug or "x"), **over}
    jobs.write_entry(cfg.queue_dir, e)
    return e


def entry(cfg, job_id):
    return json.loads((cfg.queue_dir / f"{job_id}.json").read_text())


def porcelain(repo):
    return _git(repo, "status", "--porcelain", "--untracked-files=all")


def origin_head(repo):
    return _git(repo, "ls-remote", "origin", "refs/heads/main").split()[0]


def snapshot(repo):
    tracked = {
        p: hashlib.sha256((repo / p).read_bytes()).hexdigest()
        for p in _git(repo, "ls-files").splitlines()
    }
    untracked = _git(repo, "ls-files", "--others", "--exclude-standard")
    return tracked, untracked, _git(repo, "rev-parse", "HEAD")


def assert_failed_cleanly(cfg, e, before_origin, message_start):
    got = entry(cfg, e["id"])
    assert got["state"] == "failed", got
    assert got["message"].startswith(message_start), got["message"]
    assert got["finished_at"]
    assert porcelain(cfg.repo) == ""
    assert origin_head(cfg.repo) == before_origin
    assert _git(cfg.repo, "rev-parse", "HEAD") == before_origin


# ---------------------------------------------------------------- outcomes


def test_good_job_commits_exactly_the_book_and_pushes(cfg):
    e = enqueue(cfg)
    b = FakeBuilder(cfg)
    before = origin_head(cfg.repo)
    ino = (cfg.repo / "books.yaml").stat().st_ino

    assert run(cfg, b, ok_checks) == 0

    got = entry(cfg, e["id"])
    assert got["state"] == "done" and got["message"] == "published; deploy runs from the push"
    assert got["usd"] == 0.07
    assert porcelain(cfg.repo) == ""
    after = origin_head(cfg.repo)
    assert after != before and _git(cfg.repo, "rev-parse", f"{after}~1") == before
    assert got["commit"] == _git(cfg.repo, "rev-parse", "--short", "HEAD")
    files = _git(cfg.repo, "show", "--name-only", "--format=", "HEAD").splitlines()
    assert sorted(files) == [".cache/pages/the-power-of-habit.json", "books.yaml",
                             "covers/the-power-of-habit.jpg"]
    msg = _git(cfg.repo, "log", "-1", "--format=%B")
    assert msg.splitlines()[0] == "feat: add The Power of Habit"
    assert "Author: Charles Duhigg" in msg and e["id"] in msg
    assert "Co-Authored-By" not in msg
    assert "# keep this comment" in (cfg.repo / "books.yaml").read_text()
    assert (cfg.repo / "books.yaml").stat().st_ino == ino
    # Title with spaces reached the builder intact, with the exact-match build.
    assert ("add", "The Power of Habit", "Charles Duhigg") in b.calls
    assert ("build", "The Power of Habit", e["id"], 0.5) in b.calls
    assert not (cfg.sentinel_lock_dir / worker.sentinel_lock_name(cfg.repo)).exists()


def test_good_job_without_a_cover(cfg):
    e = enqueue(cfg)
    assert run(cfg, FakeBuilder(cfg, "no_cover"), ok_checks) == 0
    assert entry(cfg, e["id"])["state"] == "done"
    files = _git(cfg.repo, "show", "--name-only", "--format=", "HEAD").splitlines()
    assert sorted(files) == [".cache/pages/the-power-of-habit.json", "books.yaml"]


def test_like_switch_fails_and_leaves_the_tree_byte_identical(cfg):
    e = enqueue(cfg, "The Like Switch", "Jack Schafer")
    b = FakeBuilder(cfg, "like_switch")
    before = snapshot(cfg.repo)
    ino = (cfg.repo / "books.yaml").stat().st_ino
    origin = origin_head(cfg.repo)

    assert run(cfg, b, ok_checks) == 1

    assert_failed_cleanly(cfg, e, origin,
                          "insufficient evidence: no qualifying Reddit summaries found")
    assert entry(cfg, e["id"])["message"] == "insufficient evidence: no qualifying Reddit summaries found"
    assert snapshot(cfg.repo) == before
    assert (cfg.repo / "books.yaml").stat().st_ino == ino
    assert not (cfg.repo / "covers" / "the-like-switch.jpg").exists()
    assert not (cfg.repo / ".cache" / "pages" / "the-like-switch.json").exists()
    assert not (cfg.repo / "site" / "books" / "the-like-switch.html").exists()
    assert not (cfg.repo / "site" / "books" / "the-like-switch.md").exists()
    assert "render" in b.calls


def test_injection_touching_another_page_and_writing_a_file(cfg):
    e = enqueue(cfg)
    other = cfg.repo / ".cache" / "pages" / "deep-work.json"
    other_before = other.read_bytes()
    origin = origin_head(cfg.repo)

    assert run(cfg, FakeBuilder(cfg, "injection"), ok_checks) == 3

    got = entry(cfg, e["id"])
    assert got["state"] == "failed"
    assert got["message"].startswith("unexpected changes outside this book: ")
    assert ".cache/pages/deep-work.json" in got["message"] and "evil.txt" in got["message"]
    assert "tree NOT clean after restore" in got["message"]
    assert other.read_bytes() == other_before  # restored byte for byte
    assert (cfg.repo / "evil.txt").exists()  # unknown files are never deleted...
    assert porcelain(cfg.repo) == "?? evil.txt"  # ...only reported
    assert origin_head(cfg.repo) == origin


def test_injection_without_a_stray_file_restores_cleanly(cfg):
    e = enqueue(cfg)
    origin = origin_head(cfg.repo)
    assert run(cfg, FakeBuilder(cfg, "injection_no_evil"), ok_checks) == 1
    assert_failed_cleanly(cfg, e, origin, "unexpected changes outside this book: .cache/pages/deep-work.json")


def test_bad_url(cfg):
    e = enqueue(cfg)
    origin = origin_head(cfg.repo)
    assert run(cfg, FakeBuilder(cfg, "bad_url"), ok_checks) == 1
    assert_failed_cleanly(cfg, e, origin, "page failed validation: quote url is not a reddit link")


def test_over_budget(cfg):
    e = enqueue(cfg)
    origin = origin_head(cfg.repo)
    assert run(cfg, FakeBuilder(cfg, "over_budget"), ok_checks) == 1
    assert_failed_cleanly(cfg, e, origin, "over budget: refused rank")


def test_builder_exception(cfg):
    e = enqueue(cfg)
    origin = origin_head(cfg.repo)
    assert run(cfg, FakeBuilder(cfg, "boom"), ok_checks) == 1
    assert_failed_cleanly(cfg, e, origin, "worker error: RuntimeError: container went away")


def test_failing_checks(cfg):
    e = enqueue(cfg)
    origin = origin_head(cfg.repo)
    assert run(cfg, FakeBuilder(cfg), lambda repo: (False, "pytest failed: 1 failed")) == 1
    assert_failed_cleanly(cfg, e, origin, "pytest failed: 1 failed")


def test_checks_that_write_files_fail_the_job(cfg):
    e = enqueue(cfg)
    origin = origin_head(cfg.repo)

    def sneaky(repo):
        (repo / "src" / "dummy.py").write_text("x = 2\n")
        return True, ""

    assert run(cfg, FakeBuilder(cfg), sneaky) == 1
    assert_failed_cleanly(cfg, e, origin, "the checks changed files")


def test_push_failure_undoes_the_local_commit(cfg):
    _git(cfg.repo, "remote", "set-url", "--push", "origin", str(cfg.repo.parent / "nope.git"))
    e = enqueue(cfg)
    origin = origin_head(cfg.repo)
    ino = (cfg.repo / "books.yaml").stat().st_ino
    assert run(cfg, FakeBuilder(cfg), ok_checks) == 1
    assert_failed_cleanly(cfg, e, origin, "push failed")
    assert (cfg.repo / "books.yaml").stat().st_ino == ino
    assert (cfg.repo / "books.yaml").read_text() == BOOKS


# ---------------------------------------------------------------- preflight


def _assert_waiting(cfg, e, b, fragment):
    got = entry(cfg, e["id"])
    assert got["state"] == "queued"
    assert got["message"].startswith("waiting: "), got["message"]
    assert fragment in got["message"], got["message"]
    assert not [c for c in b.calls if isinstance(c, tuple)]  # never added or built


def test_dirty_tracked_file_refuses(cfg):
    e = enqueue(cfg)
    (cfg.repo / "src" / "dummy.py").write_text("x = 2\n")
    b = FakeBuilder(cfg)
    assert run(cfg, b, ok_checks) == 2
    _assert_waiting(cfg, e, b, "working tree is not clean: ")
    assert b.calls == []


def test_untracked_file_refuses(cfg):
    e = enqueue(cfg)
    (cfg.repo / "notes.txt").write_text("hi")
    b = FakeBuilder(cfg)
    assert run(cfg, b, ok_checks) == 2
    _assert_waiting(cfg, e, b, "?? notes.txt")


def test_modified_books_yaml_alone_refuses(cfg):
    """Today's real-world state: books.yaml has an uncommitted entry."""
    e = enqueue(cfg)
    with (cfg.repo / "books.yaml").open("a") as f:
        f.write("- title: The Like Switch\n  author: Jack Schafer\n")
    b = FakeBuilder(cfg)
    assert run(cfg, b, ok_checks) == 2
    _assert_waiting(cfg, e, b, "books.yaml")


def test_version_mismatch_refuses(cfg):
    e = enqueue(cfg)
    b = FakeBuilder(cfg)
    b.version_override = "gttp 0.0.0"
    assert run(cfg, b, ok_checks) == 2
    _assert_waiting(cfg, e, b, "app container runs 'gttp 0.0.0'")


def test_container_catalog_mismatch_refuses(cfg):
    e = enqueue(cfg)
    b = FakeBuilder(cfg)
    b.sha_override = "0" * 64
    assert run(cfg, b, ok_checks) == 2
    _assert_waiting(cfg, e, b, "restart the app container")


def test_catalog_book_without_a_page_refuses(cfg):
    with (cfg.repo / "books.yaml").open("a") as f:
        f.write("- title: Grit\n  author: Angela Duckworth\n")
    _git(cfg.repo, "commit", "-q", "-am", "add grit")
    _git(cfg.repo, "push", "-q")
    e = enqueue(cfg)
    b = FakeBuilder(cfg)
    assert run(cfg, b, ok_checks) == 2
    _assert_waiting(cfg, e, b, "catalog books without a page")


def test_out_of_sync_with_origin_refuses(cfg):
    (cfg.repo / "src" / "dummy.py").write_text("x = 3\n")
    _git(cfg.repo, "commit", "-q", "-am", "local only")
    e = enqueue(cfg)
    b = FakeBuilder(cfg)
    assert run(cfg, b, ok_checks) == 2
    _assert_waiting(cfg, e, b, "is not origin/main")


def test_waiting_message_written_once(cfg):
    e = enqueue(cfg)
    (cfg.repo / "notes.txt").write_text("hi")
    run(cfg, FakeBuilder(cfg), ok_checks)
    first = entry(cfg, e["id"])
    run(cfg, FakeBuilder(cfg), ok_checks)
    assert entry(cfg, e["id"])["updated_at"] == first["updated_at"]


def test_preflight_command_reports_each_check(cfg, capsys):
    (cfg.repo / "notes.txt").write_text("hi")
    b = FakeBuilder(cfg)
    b.version_override = "gttp 0.0.0"
    assert worker._cmd_preflight(cfg, b) == 2
    out = capsys.readouterr().out
    assert "✗ clean tree" in out and "✗ container version" in out
    assert "✓ on branch" in out and "✓ every catalog book has a page" in out


# ---------------------------------------------------------------- locks


def test_busy_worker_lock_returns_zero(cfg):
    import fcntl

    e = enqueue(cfg)
    fd = os.open(cfg.lock_path, os.O_RDWR | os.O_CREAT)
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        b = FakeBuilder(cfg)
        assert run(cfg, b, ok_checks) == 0
        assert b.calls == []
        assert entry(cfg, e["id"])["state"] == "queued"
    finally:
        os.close(fd)


def test_two_worker_processes_never_overlap(cfg, tmp_path):
    enqueue(cfg, "Book One", "A")
    enqueue(cfg, "Book Two", "B")
    marker = tmp_path / "marker.log"
    script = tmp_path / "w.py"
    script.write_text(textwrap.dedent(f"""
        import hashlib, os, sys, time
        from pathlib import Path
        from gttp import __version__
        from gttp.worker import WorkerConfig, run
        repo = Path({str(cfg.repo)!r})
        cfg = WorkerConfig(repo=repo, queue_dir=Path({str(cfg.queue_dir)!r}),
            usage_log=repo / ".cache" / "usage.jsonl", lock_path=Path({str(cfg.lock_path)!r}),
            sentinel_lock_dir=Path({str(cfg.sentinel_lock_dir)!r}), job_cap_usd=0.5,
            monthly_cap_usd=5.0, build_timeout_s=60, sentinel_wait_s=30, sentinel_poll_s=0.05)
        marker = Path({str(marker)!r})
        class Sleepy:
            def version(self): return f"gttp {{__version__}}"
            def catalog_sha256(self): return hashlib.sha256((repo / "books.yaml").read_bytes()).hexdigest()
            def add(self, title, author):
                with marker.open("a") as f: f.write(f"start {{os.getpid()}}\\n")
                time.sleep(1.5)
                with marker.open("a") as f: f.write(f"end {{os.getpid()}}\\n")
                raise RuntimeError("stop here")
            def build(self, *a): pass
            def render(self): pass
        sys.exit(run(cfg, Sleepy(), lambda repo: (True, "")))
    """))
    procs = [subprocess.Popen([sys.executable, str(script)]) for _ in range(2)]
    codes = [p.wait(timeout=60) for p in procs]
    lines = marker.read_text().splitlines()
    # Strictly start/end pairs from the same process: no two jobs ever overlapped.
    assert lines and len(lines) % 2 == 0
    for i in range(0, len(lines), 2):
        assert lines[i].startswith("start ") and lines[i + 1] == "end " + lines[i].split()[1]
    assert sorted(codes) in ([0, 1], [1, 1])
    assert porcelain(cfg.repo) == ""


def test_live_sentinel_lock_makes_the_job_wait(cfg):
    lockfile = cfg.sentinel_lock_dir / worker.sentinel_lock_name(cfg.repo)
    lockfile.parent.mkdir(parents=True)
    lockfile.write_text(str(os.getpid()))
    e = enqueue(cfg)
    b = FakeBuilder(cfg)
    assert run(cfg, b, ok_checks) == 2
    got = entry(cfg, e["id"])
    assert got["state"] == "queued" and got["message"] == "waiting: a Sentinel deploy is in progress"
    assert not [c for c in b.calls if isinstance(c, tuple)]
    assert lockfile.read_text() == str(os.getpid())  # someone else's lock: untouched


def test_dead_sentinel_lock_is_taken_over(cfg):
    dead = 999_999
    while worker.pid_alive(dead):  # pragma: no cover - vanishingly unlikely
        dead += 1
    lockfile = cfg.sentinel_lock_dir / worker.sentinel_lock_name(cfg.repo)
    lockfile.parent.mkdir(parents=True)
    lockfile.write_text(str(dead))
    e = enqueue(cfg)
    assert run(cfg, FakeBuilder(cfg), ok_checks) == 0
    assert entry(cfg, e["id"])["state"] == "done"
    assert not lockfile.exists()
    assert not list(cfg.sentinel_lock_dir.iterdir())  # no temp files left


def test_sentinel_lock_name_matches_sentinel():
    assert worker.sentinel_lock_name(Path("/home/eric/projects/gttp")) == "_home_eric_projects_gttp.lock"


# ---------------------------------------------------------------- queue hygiene


def test_stale_running_entry_is_failed(cfg):
    e = enqueue(cfg)
    jobs.write_entry(cfg.queue_dir, jobs.transition(e, "running"))
    assert run(cfg, FakeBuilder(cfg), ok_checks) == 0
    got = entry(cfg, e["id"])
    assert got["state"] == "failed" and got["message"].startswith("interrupted:")


def test_monthly_budget(cfg):
    now = datetime(2026, 11, 15, 12, 0, tzinfo=jobs.TZ)
    this_month = int(datetime(2026, 11, 2, 9, 0, tzinfo=jobs.TZ).timestamp())
    last_month = int(datetime(2026, 10, 20, 9, 0, tzinfo=jobs.TZ).timestamp())
    # Oct 31 23:30 New York is already Nov 1 in UTC: must count as October.
    edge = int(datetime(2026, 10, 31, 23, 30, tzinfo=jobs.TZ).timestamp())
    lines = [{"t": this_month, "usd": 2.40}, {"t": this_month, "usd": 2.40},
             {"t": this_month, "usd": None}, {"t": last_month, "usd": 9.0},
             {"t": edge, "usd": 1.0}]
    cfg.usage_log.parent.mkdir(parents=True, exist_ok=True)
    cfg.usage_log.write_text("\n".join(json.dumps(x) for x in lines) + "\nnot json\n")
    e = enqueue(cfg)
    b = FakeBuilder(cfg)
    assert run(cfg, b, ok_checks, now=now) == 1
    got = entry(cfg, e["id"])
    assert got["state"] == "failed" and got["message"] == "monthly budget reached ($4.80 of $5.00)"
    assert not [c for c in b.calls if isinstance(c, tuple)]
    assert porcelain(cfg.repo) == ""


def test_junk_queue_files_are_ignored(cfg):
    (cfg.queue_dir / "evil.json").write_text(json.dumps({"state": "queued", "title": "x"}))
    (cfg.queue_dir / "1794067200000-aaaaaaaa.json").write_text("x" * 20_000)
    (cfg.queue_dir / "1794067200001-aaaaaaaa.json").write_text("{bad json")
    done = {**jobs.new_entry("Grit", "Angela Duckworth", "grit"), "state": "done"}
    jobs.write_entry(cfg.queue_dir, done)
    b = FakeBuilder(cfg)
    assert run(cfg, b, ok_checks) == 0
    assert b.calls == []
    assert entry(cfg, done["id"])["state"] == "done"


def test_queued_entry_with_a_bad_title_is_failed(cfg):
    e = enqueue(cfg, "Bad\nTitle", "Someone")
    b = FakeBuilder(cfg)
    assert run(cfg, b, ok_checks) == 1
    got = entry(cfg, e["id"])
    assert got["state"] == "failed"
    assert got["message"] == "title contains control or invisible characters"
    assert b.calls == []


def test_dash_title_never_reaches_argv(cfg):
    e = enqueue(cfg, "--force", "Someone")
    b = FakeBuilder(cfg)
    assert run(cfg, b, ok_checks) == 1
    assert entry(cfg, e["id"])["message"] == "title must not start with '-'"
    assert b.calls == []


def test_forged_slug_is_ignored(cfg):
    """The worker recomputes the slug; a forged one can't steer paths."""
    e = enqueue(cfg, slug="../../etc/passwd")
    assert run(cfg, FakeBuilder(cfg), ok_checks) == 0
    assert entry(cfg, e["id"])["state"] == "done"
    files = _git(cfg.repo, "show", "--name-only", "--format=", "HEAD").splitlines()
    assert ".cache/pages/the-power-of-habit.json" in files


def test_already_in_catalog(cfg):
    e = enqueue(cfg, "Deep Work", "Cal Newport")
    b = FakeBuilder(cfg)
    assert run(cfg, b, ok_checks) == 1
    assert entry(cfg, e["id"])["message"] == "'Deep Work' is already in the catalog"
    assert not [c for c in b.calls if isinstance(c, tuple)]


def test_cancel(cfg):
    e = enqueue(cfg)
    assert worker._cmd_cancel(cfg, e["id"]) == 0
    got = entry(cfg, e["id"])
    assert got["state"] == "failed" and got["message"] == "cancelled by operator"
    assert worker._cmd_cancel(cfg, e["id"]) == 1  # terminal now
    assert worker._cmd_cancel(cfg, "../x") == 1


def test_main_refuses_inside_a_container(monkeypatch):
    real_exists = Path.exists
    monkeypatch.setattr(Path, "exists", lambda p: str(p) == "/.dockerenv" or real_exists(p))
    assert worker.main(["status"]) == 2


# ---------------------------------------------------------------- validate_page


def _write(tmp_path, data):
    p = tmp_path / "page.json"
    p.write_text(json.dumps(data) if not isinstance(data, str) else data)
    return p


T, A = "The Power of Habit", "Charles Duhigg"


@pytest.mark.parametrize("data, message", [
    (dict(LIKE_SWITCH_STUB, title=T, author=A),
     "insufficient evidence: no qualifying Reddit summaries found"),
    (_page(T, A, generated_by="error", bullets=[], sources=[], honest_take="Build error: boom"),
     "build error: Build error: boom"),
    (_page(T, A, generated_by="heuristic"),
     "insufficient evidence: page was not synthesized by Claude (generated_by=heuristic)"),
    (_page(T, A, generated_by="claude_unranked"),
     "insufficient evidence: page was not synthesized by Claude (generated_by=claude_unranked)"),
    (_page(T, A, surprise="x"), "page failed validation: does not match the BookPage schema"),
    ([1, 2], "page failed validation: not a JSON object"),
    ("{nope", "page failed validation: not a JSON object"),
    (_page("Other Title", A), "page failed validation: title or author does not match the submission"),
    (_page(T, A, bullets=["b"] * 6), "page failed validation: needs 1-5 bullets"),
    (_page(T, A, bullets=["x" * 601]), "page failed validation: each bullet must be non-empty text of at most 600 characters"),
    (_page(T, A, core_idea=""), "page failed validation: core_idea must be non-empty text of at most 1000 characters"),
    (_page(T, A, sources=[]), "page failed validation: needs at least one source"),
    (_page(T, A, sources=[{"title": "t", "url": "https://evil.example/x"}]),
     "page failed validation: source url is not a reddit link"),
    (_page(T, A, quotes=[{"text": "q", "author": "u", "url": "https://reddit.com.evil.example/"}]),
     "page failed validation: quote url is not a reddit link"),
    (_page(T, A, quotes=[{"text": "q", "author": "u", "url": "x"}] * 4),
     "page failed validation: at most 3 quotes"),
])
def test_validate_page_rejects(tmp_path, data, message):
    assert validate_page(_write(tmp_path, data), T, A) == (False, message)


def test_validate_page_missing_and_oversized(tmp_path):
    assert validate_page(tmp_path / "nope.json", T, A) == (False, "insufficient evidence: build wrote no page")
    big = _write(tmp_path, _page(T, A, honest_take="x" * 210_000))
    assert validate_page(big, T, A) == (False, "page failed validation: not a JSON object")


def test_validate_page_accepts_a_good_page(tmp_path):
    assert validate_page(_write(tmp_path, _page(T, A)), T, A) == (True, "")
    old = _page(T, A, sources=[{"title": "t", "url": "https://old.reddit.com/r/x/1"}])
    assert validate_page(_write(tmp_path, old), T, A) == (True, "")
