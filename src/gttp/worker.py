"""Host-side submit worker: build one queued book, check it, commit and push.

Runs on the host (never in a container), once a minute from the
`gttp-worker.timer` systemd user unit. Each run:

1. takes a single-instance flock, fails stale `running` entries, and picks
   the oldest `queued` entry from `.cache/queue/` (untrusted: the submit
   container can write anything there, so it's all re-validated);
2. preflight: on `main`, tree fully clean, in sync with origin, the app
   container runs this exact gttp version and sees the same books.yaml,
   and every catalog book already has a page;
3. checks the monthly budget and catalog conflicts, then takes Sentinel's
   per-repo deploy lock so a deploy waits for the job instead of refusing
   the dirty tree;
4. runs `gttp add` + `gttp build --exact --only=<title>` inside the running
   `app` container with a hard per-job Claude budget;
5. requires exactly the allowlisted files changed (books.yaml, the book's
   page, optionally its cover), the page to be a real Claude synthesis that
   passes `validate_page`, and the tests + ruff to pass;
6. commits exactly those files and pushes `main` (Sentinel deploys it).

Any failure rolls back everything the job touched. books.yaml is a
single-file bind mount, so it is always restored in place (never by
`git checkout`, which replaces the inode). Untrusted text only ever reaches
argv lists (never a shell), JSON files and escaped HTML.

Exit codes: 0 nothing to do / done / another worker running; 1 job failed,
tree clean; 2 preflight refused, entry left queued ("waiting: ..."); 3
restore failed, tree still dirty, Sentinel deploys blocked.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import secrets
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Protocol

import yaml

from . import __version__, jobs
from .cache import page_quality
from .config import ROOT, load_catalog
from .models import BookPage

EXIT_OK, EXIT_FAILED, EXIT_WAITING, EXIT_DIRTY = 0, 1, 2, 3
MAX_PAGE_BYTES = 200 * 1024
REDDIT_URL_RE = re.compile(r"^https://(www\.|old\.)?reddit\.com/")
DEFAULT_SENTINEL_LOCK_DIR = "/tmp/sentinel-deploy-locks"
INTERRUPTED = "interrupted: the worker stopped mid-job; check git status"


class JobFailed(Exception):
    """A job failed for a reason worth showing on the status page."""


@dataclass
class WorkerConfig:
    repo: Path
    queue_dir: Path
    usage_log: Path
    lock_path: Path
    sentinel_lock_dir: Path
    job_cap_usd: float
    monthly_cap_usd: float
    build_timeout_s: int
    remote: str = "origin"
    branch: str = "main"
    sentinel_wait_s: float = 600.0
    sentinel_poll_s: float = 2.0

    @classmethod
    def from_env(cls) -> "WorkerConfig":
        env = os.environ
        repo = ROOT
        runtime = env.get("XDG_RUNTIME_DIR")
        lock = Path(runtime) / "gttp-worker.lock" if runtime else repo / ".cache" / "worker.lock"
        return cls(
            repo=repo,
            queue_dir=Path(env.get("GTTP_QUEUE_DIR") or repo / ".cache" / "queue"),
            usage_log=repo / ".cache" / "usage.jsonl",
            lock_path=lock,
            sentinel_lock_dir=Path(env.get("SENTINEL_DEPLOY_LOCK_DIR") or DEFAULT_SENTINEL_LOCK_DIR),
            job_cap_usd=_positive_float(env, "GTTP_MAX_BUDGET_USD", 0.50),
            monthly_cap_usd=_positive_float(env, "GTTP_MONTHLY_BUDGET_USD", 5.00),
            build_timeout_s=int(_positive_float(env, "GTTP_WORKER_BUILD_TIMEOUT", 600)),
        )


def _positive_float(env, name: str, default: float) -> float:
    raw = (env.get(name) or "").strip()
    if not raw:
        return default
    value = float(raw)  # ValueError -> main() refuses to start
    if not value > 0:
        raise ValueError(f"{name} must be a positive number")
    return value


def log(msg: str) -> None:
    print(msg, flush=True)


# --------------------------------------------------------------------------
# git helpers (argv lists only; never a shell)
# --------------------------------------------------------------------------


def git(repo: Path, *args: str, check: bool = True, timeout: float = 60) -> subprocess.CompletedProcess:
    r = subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, timeout=timeout
    )
    if check and r.returncode != 0:
        raise JobFailed(f"git {args[0]} failed: {_last_line(r.stderr or r.stdout)}")
    return r


def _last_line(text: str) -> str:
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    return lines[-1][:200] if lines else "(no output)"


def porcelain_paths(repo: Path) -> dict[str, str]:
    """Changed paths -> two-letter status, untracked files included. Uses -z
    so names with spaces or newlines parse correctly; a rename lists both
    the old and the new path."""
    out = git(repo, "status", "--porcelain=v1", "-z", "--untracked-files=all").stdout
    items = out.split("\0")
    paths: dict[str, str] = {}
    i = 0
    while i < len(items):
        item = items[i]
        i += 1
        if len(item) < 4:
            continue
        status, path = item[:2], item[3:]
        paths[path] = status
        if status[0] in "RC" and i < len(items):
            paths[items[i]] = status
            i += 1
    return paths


def head(repo: Path, ref: str = "HEAD") -> str:
    return git(repo, "rev-parse", ref).stdout.strip()


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# --------------------------------------------------------------------------
# Locks
# --------------------------------------------------------------------------


class WorkerLock:
    """Single-instance flock. Lives outside the queue dir so the submit
    container can't hold it."""

    def __init__(self, path: Path):
        self.path = path
        self.fd: int | None = None

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(fd)
            return False
        self.fd = fd
        return True

    def release(self) -> None:
        if self.fd is not None:
            fcntl.flock(self.fd, fcntl.LOCK_UN)
            os.close(self.fd)
            self.fd = None


def sentinel_lock_name(repo: Path) -> str:
    """Same name as sentinel/src/deployLock.js lockFileName()."""
    return re.sub(r"[^A-Za-z0-9._-]+", "_", str(repo.resolve())) + ".lock"


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class SentinelLock:
    """Sentinel's per-repo deploy lock, same protocol as deployLock.js: write
    our pid to a temp file, hard-link it into place (atomic, fails if it
    exists), and take over a lock whose pid is dead. While the worker holds
    it, a Sentinel deploy waits instead of refusing the job's dirty tree."""

    def __init__(self, lock_dir: Path, repo: Path):
        self.file = lock_dir / sentinel_lock_name(repo)
        self.held = False

    def acquire(self, wait_s: float, poll_s: float = 2.0) -> bool:
        self.file.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + wait_s
        me = str(os.getpid())
        while True:
            tmp = Path(f"{self.file}.{me}.{secrets.token_hex(4)}")
            tmp.write_text(me)
            try:
                os.link(tmp, self.file)
                self.held = True
                return True
            except FileExistsError:
                pass
            finally:
                tmp.unlink(missing_ok=True)
            try:
                holder = int(self.file.read_text().strip() or "0")
            except FileNotFoundError:
                continue  # released between our attempt and the read
            except ValueError:
                holder = 0
            if not pid_alive(holder):
                try:
                    again = int(self.file.read_text().strip() or "0")
                except (FileNotFoundError, ValueError):
                    again = None
                if again == holder:
                    self.file.unlink(missing_ok=True)
                    log(f"took over a stale Sentinel deploy lock (pid {holder})")
                continue
            if time.monotonic() >= deadline:
                return False
            time.sleep(poll_s)

    def release(self) -> None:
        if not self.held:
            return
        try:
            if self.file.read_text().strip() == str(os.getpid()):
                self.file.unlink()
        except FileNotFoundError:
            pass
        self.held = False


# --------------------------------------------------------------------------
# The builder (the app container) and the test gate
# --------------------------------------------------------------------------


class Builder(Protocol):
    def version(self) -> str: ...
    def catalog_sha256(self) -> str: ...
    def add(self, title: str, author: str) -> None: ...
    def build(self, title: str, job_id: str, cap_usd: float) -> None: ...
    def render(self) -> None: ...


Checks = Callable[[Path], "tuple[bool, str]"]

_CATALOG_SHA = (
    "import hashlib;print(hashlib.sha256(open('/app/books.yaml','rb').read()).hexdigest())"
)


class ContainerBuilder:
    """Runs gttp inside the running `app` compose service."""

    def __init__(self, repo: Path, build_timeout_s: int):
        self.repo = repo
        self.build_timeout_s = build_timeout_s

    def _exec(self, label: str, args: list[str], timeout: float, echo: bool = True) -> str:
        cmd = ["docker", "compose", "exec", "-T", *args]
        try:
            r = subprocess.run(cmd, cwd=self.repo, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            raise JobFailed(f"{label} timed out after {int(timeout)}s") from None
        if echo:
            for stream in (r.stdout, r.stderr):
                if stream.strip():
                    log(stream.rstrip())
        if r.returncode != 0:
            raise JobFailed(f"{label} failed: {_last_line(r.stderr or r.stdout)}")
        return r.stdout

    def version(self) -> str:
        return self._exec("gttp --version", ["app", "gttp", "--version"], 60, echo=False).strip()

    def catalog_sha256(self) -> str:
        return self._exec("catalog check", ["app", "python", "-c", _CATALOG_SHA], 60, echo=False).strip()

    def add(self, title: str, author: str) -> None:
        self._exec("gttp add", ["app", "gttp", "add", f"--author={author}", "--", title], 120)

    def build(self, title: str, job_id: str, cap_usd: float) -> None:
        self._exec(
            "gttp build",
            [
                "-e", f"GTTP_MAX_BUDGET_USD={cap_usd}",
                "-e", f"GTTP_JOB_ID={job_id}",
                "app", "timeout", "--kill-after=30", str(self.build_timeout_s),
                "gttp", "build", "--exact", f"--only={title}",
            ],
            self.build_timeout_s + 60,
        )

    def render(self) -> None:
        self._exec("gttp render", ["app", "gttp", "render"], 300)


def run_checks(repo: Path) -> tuple[bool, str]:
    """The repo's own gate: pytest, then ruff. Never with a real API key."""
    env = dict(os.environ)
    env["ANTHROPIC_API_KEY"] = ""
    for name in ("GTTP_MAX_BUDGET_USD", "GTTP_JOB_ID", "GTTP_QUEUE_DIR"):
        env.pop(name, None)
    venv = repo / ".venv" / "bin"
    for tool, cmd in (
        ("pytest", [str(venv / "python"), "-m", "pytest", "-q"]),
        ("ruff", [str(venv / "ruff"), "check", "."]),
    ):
        try:
            r = subprocess.run(cmd, cwd=repo, env=env, capture_output=True, text=True, timeout=600)
        except subprocess.TimeoutExpired:
            return False, f"{tool} failed: timed out"
        except OSError as exc:
            return False, f"{tool} failed: {exc}"
        if r.returncode != 0:
            return False, f"{tool} failed: {_last_line(r.stdout + chr(10) + r.stderr)}"
    return True, ""


# --------------------------------------------------------------------------
# Page validation and budgets
# --------------------------------------------------------------------------


def _is_str(v, max_len: int, allow_empty: bool = False) -> bool:
    return isinstance(v, str) and len(v) <= max_len and (allow_empty or bool(v.strip()))


def validate_page(path: Path, title: str, author: str) -> tuple[bool, str]:
    """Is the built page publishable? First failure wins (see the plan's
    message prefixes: insufficient evidence / build error / page failed
    validation)."""
    if not path.is_file():
        return False, "insufficient evidence: build wrote no page"
    try:
        if path.stat().st_size > MAX_PAGE_BYTES:
            raise ValueError
        d = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(d, dict):
            raise ValueError
    except (OSError, ValueError):
        return False, "page failed validation: not a JSON object"
    try:
        page = BookPage(**d)
    except TypeError:
        return False, "page failed validation: does not match the BookPage schema"
    if page.generated_by == "error":
        return False, f"build error: {str(page.honest_take)[:150]}"
    if not page.bullets and not page.sources:
        return False, "insufficient evidence: no qualifying Reddit summaries found"
    if page.generated_by != "claude":
        return False, (
            "insufficient evidence: page was not synthesized by Claude "
            f"(generated_by={page.generated_by})"
        )
    if page_quality(page) != 2:
        return False, "insufficient evidence: quality guard did not pass"

    def bad(what: str) -> tuple[bool, str]:
        return False, f"page failed validation: {what}"

    if page.title != title or page.author != author:
        return bad("title or author does not match the submission")
    if not _is_str(page.core_idea, 1000):
        return bad("core_idea must be non-empty text of at most 1000 characters")
    if not isinstance(page.bullets, list) or not 1 <= len(page.bullets) <= 5:
        return bad("needs 1-5 bullets")
    if not all(_is_str(b, 600) for b in page.bullets):
        return bad("each bullet must be non-empty text of at most 600 characters")
    if not _is_str(page.honest_take, 4000, allow_empty=True):
        return bad("honest_take must be text of at most 4000 characters")
    if not isinstance(page.quotes, list) or len(page.quotes) > 3:
        return bad("at most 3 quotes")
    for q in page.quotes:
        if not (isinstance(q, dict) and all(isinstance(q.get(k), str) for k in ("text", "author", "url"))):
            return bad("each quote needs text, author and url")
    if not isinstance(page.sources, list) or not page.sources:
        return bad("needs at least one source")
    for s in page.sources:
        if not (isinstance(s, dict) and all(isinstance(s.get(k), str) for k in ("url", "title"))):
            return bad("each source needs a url and a title")
    for s in page.sources:
        if not REDDIT_URL_RE.match(s["url"]):
            return bad("source url is not a reddit link")
    for q in page.quotes:
        if not REDDIT_URL_RE.match(q["url"]):
            return bad("quote url is not a reddit link")
    return True, ""


def _usage_lines(usage_log: Path):
    try:
        text = usage_log.read_text()
    except OSError:
        return
    for line in text.splitlines():
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if isinstance(d, dict):
            yield d


def _usd(d: dict) -> float:
    v = d.get("usd")
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else 0.0


def month_spend(usage_log: Path, now: datetime) -> float:
    """Claude spend in the current America/New_York calendar month, from
    every usage line (manual builds count too)."""
    local = now.astimezone(jobs.TZ)
    total = 0.0
    for d in _usage_lines(usage_log):
        t = d.get("t")
        if not isinstance(t, (int, float)):
            continue
        when = datetime.fromtimestamp(t, jobs.TZ)
        if (when.year, when.month) == (local.year, local.month):
            total += _usd(d)
    return total


def job_spend(usage_log: Path, job_id: str) -> float:
    return round(sum(_usd(d) for d in _usage_lines(usage_log) if d.get("job") == job_id), 4)


def budget_refusal(usage_log: Path, job_id: str) -> dict | None:
    for d in _usage_lines(usage_log):
        if d.get("job") == job_id and d.get("step") == "budget_refused":
            return d
    return None


# --------------------------------------------------------------------------
# Preflight
# --------------------------------------------------------------------------


def _preflight_checks(cfg: WorkerConfig, builder: Builder):
    """(name, check) pairs in order; each check returns an error or None."""
    repo = cfg.repo

    def on_branch():
        b = git(repo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
        return None if b == cfg.branch else f"checkout is on {b!r}, not {cfg.branch!r}"

    def clean():
        lines = [f"{s} {p}" for p, s in porcelain_paths(repo).items()]
        if not lines:
            return None
        more = f" (+{len(lines) - 5} more)" if len(lines) > 5 else ""
        return "working tree is not clean: " + ", ".join(lines[:5]) + more

    def in_sync():
        git(repo, "fetch", "-q", cfg.remote, cfg.branch, timeout=120)
        local, remote = head(repo), head(repo, f"{cfg.remote}/{cfg.branch}")
        return None if local == remote else (
            f"HEAD {local[:7]} is not {cfg.remote}/{cfg.branch} {remote[:7]}"
        )

    def version():
        want, got = f"gttp {__version__}", builder.version()
        return None if got == want else (
            f"app container runs {got!r}, worker is {want!r}; deploy first"
        )

    def same_catalog():
        if builder.catalog_sha256() == sha256_file(repo / "books.yaml"):
            return None
        return "the app container sees a different books.yaml; restart the app container"

    def pages_exist():
        missing = [
            b.title for b in load_catalog(repo / "books.yaml")
            if not (repo / ".cache" / "pages" / f"{b.slug}.json").is_file()
        ]
        if not missing:
            return None
        return "catalog books without a page (a build would build them too): " + ", ".join(missing[:5])

    return [
        ("on branch", on_branch),
        ("clean tree", clean),
        ("in sync with origin", in_sync),
        ("container version", version),
        ("container sees the same books.yaml", same_catalog),
        ("every catalog book has a page", pages_exist),
    ]


def _run_check(fn) -> str | None:
    try:
        return fn()
    except Exception as exc:  # docker down, git fetch offline, ...
        return f"{type(exc).__name__}: {exc}"


def preflight(cfg: WorkerConfig, builder: Builder) -> str | None:
    """The first reason the worker can't start a job now, or None."""
    for _name, fn in _preflight_checks(cfg, builder):
        reason = _run_check(fn)
        if reason:
            return reason
    return None


# --------------------------------------------------------------------------
# Restore
# --------------------------------------------------------------------------


def _write_in_place(path: Path, data: bytes) -> None:
    """Rewrite a file without replacing its inode (single-file bind mount)."""
    with open(path, "r+b") as f:
        f.seek(0)
        f.write(data)
        f.truncate()
        f.flush()
        os.fsync(f.fileno())


def restore(
    cfg: WorkerConfig, builder: Builder, slug: str, pre_head: str, pre_books: bytes,
    pre_ino: int, pushed: bool = False,
) -> bool:
    """Undo everything a failed job did. Each step is best-effort and logged.
    Returns whether the tree is clean afterwards."""
    repo = cfg.repo
    ok = True
    # 1. An unpushed commit: undo it without touching the working tree.
    try:
        if not pushed and head(repo) != pre_head:
            parent = head(repo, "HEAD~1")
            on_remote = git(
                repo, "merge-base", "--is-ancestor", "HEAD", f"{cfg.remote}/{cfg.branch}",
                check=False,
            ).returncode == 0
            if parent == pre_head and not on_remote:
                git(repo, "reset", "-q", "--soft", pre_head)
                git(repo, "reset", "-q")
                log(f"restore: undid the job's local commit (back to {pre_head[:7]})")
            else:
                log("RESTORE: HEAD moved in an unexpected way; leaving git history alone")
                ok = False
    except Exception as exc:
        log(f"RESTORE: could not check/undo the commit: {exc}")
        ok = False

    # 2. books.yaml, in place.
    books = repo / "books.yaml"
    try:
        if books.read_bytes() != pre_books:
            _write_in_place(books, pre_books)
            log("restore: books.yaml rewritten in place")
        if books.stat().st_ino != pre_ino:
            log("RESTORE: books.yaml inode changed; run `docker compose restart app`")
            ok = False
    except Exception as exc:
        log(f"RESTORE: could not restore books.yaml: {exc}")
        ok = False

    # 3. Everything else the job changed.
    own = {f".cache/pages/{slug}.json", f"covers/{slug}.jpg"}
    try:
        for path, status in sorted(porcelain_paths(repo).items()):
            if path == "books.yaml":
                continue
            if status == "??":
                if path in own:
                    (repo / path).unlink(missing_ok=True)
                    log(f"restore: removed {path}")
                else:
                    log(f"RESTORE: left untracked file: {path}")
            else:
                git(repo, "checkout", "HEAD", "--", path)
                log(f"restore: checked out {path}")
    except Exception as exc:
        log(f"RESTORE: could not restore changed files: {exc}")
        ok = False

    # 4. The job's derived site output (site/ is gitignored).
    site = (repo / "site").resolve()
    for rel in (f"books/{slug}.html", f"books/{slug}.md", f"covers/{slug}.jpg"):
        p = (site / rel).resolve()
        if p.parent != site and site not in p.parents:
            continue
        try:
            if p.is_file():
                p.unlink()
                log(f"restore: removed site/{rel}")
        except OSError as exc:
            log(f"RESTORE: could not remove site/{rel}: {exc}")

    # 5. Re-render the live site from the (restored) cache.
    try:
        builder.render()
    except Exception as exc:
        log(f"restore: re-render failed ({exc}); the next deploy re-renders")

    try:
        clean = not porcelain_paths(repo)
    except Exception:
        clean = False
    return ok and clean


# --------------------------------------------------------------------------
# The job
# --------------------------------------------------------------------------


def _save(cfg: WorkerConfig, entry: dict) -> None:
    jobs.write_entry(cfg.queue_dir, entry)


def _fail(cfg: WorkerConfig, entry: dict, reason: str, now=None) -> dict:
    out = jobs.transition(entry, "failed", reason, now=now)
    _save(cfg, out)
    log(f"JOB FAILED: {reason}")
    return out


def _wait(cfg: WorkerConfig, entry: dict, reason: str) -> int:
    msg = f"waiting: {reason}"
    if entry.get("message") != msg:
        _save(cfg, jobs.note(entry, msg))
    log(msg)
    return EXIT_WAITING


def commit_message(title: str, author: str, job_id: str) -> str:
    return (
        f"feat: add {title}\n\n"
        f"Author: {author}\n"
        f"Submitted via the gttp submit form (job {job_id}).\n"
    )


def run(cfg: WorkerConfig, builder: Builder, checks: Checks, now: datetime | None = None) -> int:
    lock = WorkerLock(cfg.lock_path)
    if not lock.acquire():
        log("another worker is running")
        return EXIT_OK
    try:
        return _run_locked(cfg, builder, checks, now)
    finally:
        lock.release()


def _run_locked(cfg, builder, checks, now) -> int:
    entries = jobs.read_entries(cfg.queue_dir)
    # The flock proves no live worker owns a `running` entry.
    for e in entries:
        if e["state"] == "running":
            _fail(cfg, e, INTERRUPTED, now)
    queued = [e for e in entries if e["state"] == "queued"]
    if not queued:
        return EXIT_OK
    entry = queued[0]
    title, author, job_id = entry["title"], entry["author"], entry["id"]
    log(f"job {job_id}: {title!r} by {author!r}")

    err, slug = jobs.validate_submission(title, author)
    if err:
        _fail(cfg, entry, err, now)
        return EXIT_FAILED

    reason = preflight(cfg, builder)
    if reason:
        return _wait(cfg, entry, reason)

    spent = month_spend(cfg.usage_log, now or datetime.now(jobs.TZ))
    if spent + cfg.job_cap_usd > cfg.monthly_cap_usd:
        _fail(cfg, entry, f"monthly budget reached (${spent:.2f} of ${cfg.monthly_cap_usd:.2f})", now)
        return EXIT_FAILED

    repo = cfg.repo
    page_rel = f".cache/pages/{slug}.json"
    cover_rel = f"covers/{slug}.jpg"
    host_slugs = {b.slug for b in load_catalog(repo / "books.yaml")}
    if slug in host_slugs or (repo / page_rel).exists() or (repo / cover_rel).exists():
        _fail(cfg, entry, f"'{title}' is already in the catalog", now)
        return EXIT_FAILED

    sentinel = SentinelLock(cfg.sentinel_lock_dir, repo)
    if not sentinel.acquire(cfg.sentinel_wait_s, cfg.sentinel_poll_s):
        return _wait(cfg, entry, "a Sentinel deploy is in progress")
    try:
        # A deploy may have just pulled: re-check under the lock.
        recheck = _run_check(lambda: None if not porcelain_paths(repo) else "working tree is not clean")
        if not recheck:
            recheck = _run_check(
                lambda: None if head(repo) == head(repo, f"{cfg.remote}/{cfg.branch}")
                else "HEAD moved; retrying next run"
            )
        if recheck:
            return _wait(cfg, entry, recheck)
        return _job(cfg, builder, checks, entry, slug, now)
    finally:
        sentinel.release()


def _job(cfg, builder, checks, entry, slug, now) -> int:
    repo = cfg.repo
    title, author, job_id = entry["title"], entry["author"], entry["id"]
    entry = jobs.transition(entry, "running", "building", now=now)
    _save(cfg, entry)
    books = repo / "books.yaml"
    pre_head = head(repo)
    pre_books = books.read_bytes()
    pre_ino = books.stat().st_ino
    page_rel = f".cache/pages/{slug}.json"
    try:
        builder.add(title, author)
        builder.build(title, job_id, cfg.job_cap_usd)

        expected = {"books.yaml", page_rel}
        if (repo / f"covers/{slug}.jpg").is_file():
            expected.add(f"covers/{slug}.jpg")
        changed = set(porcelain_paths(repo))
        extra = sorted(changed - expected)
        if extra:
            raise JobFailed("unexpected changes outside this book: " + ", ".join(extra[:5]))
        if page_rel not in changed:
            raise JobFailed("insufficient evidence: build wrote no page")

        before = yaml.safe_load(pre_books) or {}
        after = yaml.safe_load(books.read_bytes()) or {}
        same_rest = {k: v for k, v in after.items() if k != "books"} == {
            k: v for k, v in before.items() if k != "books"
        }
        want = list(before.get("books") or []) + [{"title": title, "author": author}]
        if not same_rest or after.get("books") != want:
            raise JobFailed("books.yaml changed beyond the new entry")

        refused = budget_refusal(cfg.usage_log, job_id)
        if refused:
            raise JobFailed(
                f"over budget: refused {refused.get('refused_step')} "
                f"(spent ${float(refused.get('spent') or 0):.3f} of ${cfg.job_cap_usd:.2f} cap)"
            )

        ok, reason = validate_page(repo / page_rel, title, author)
        if not ok:
            raise JobFailed(reason)

        ok, reason = checks(repo)
        if not ok:
            raise JobFailed(reason)
        if set(porcelain_paths(repo)) != changed:
            raise JobFailed("the checks changed files in the working tree")

        files = sorted(expected)
        git(repo, "add", "--", *files)
        staged = set(git(repo, "diff", "--cached", "--name-only", "-z").stdout.split("\0")) - {""}
        if staged != expected:
            raise JobFailed("staging mismatch")
        tmpdir = Path(tempfile.mkdtemp(prefix="gttp-worker-"))
        msg_file = tmpdir / "COMMIT_MSG"
        try:
            msg_file.write_text(commit_message(title, author, job_id))
            git(repo, "commit", "-q", "-F", str(msg_file))
        finally:
            msg_file.unlink(missing_ok=True)
            tmpdir.rmdir()
        r = git(repo, "push", "-q", cfg.remote, f"HEAD:{cfg.branch}", check=False, timeout=120)
        if r.returncode != 0:
            raise JobFailed(f"push failed: {_last_line(r.stderr or r.stdout)}")
    except Exception as exc:
        reason = str(exc) if isinstance(exc, JobFailed) else f"worker error: {type(exc).__name__}: {exc}"
        clean = restore(cfg, builder, slug, pre_head, pre_books, pre_ino, pushed=False)
        if not clean:
            reason += " (tree NOT clean after restore: Sentinel deploys are blocked)"
        _fail(cfg, entry, reason, now)
        return EXIT_FAILED if clean else EXIT_DIRTY

    sha = git(repo, "rev-parse", "--short", "HEAD").stdout.strip()
    done = jobs.transition(
        entry, "done", "published; deploy runs from the push", now=now,
        commit=sha, usd=job_spend(cfg.usage_log, job_id),
    )
    _save(cfg, done)
    log(f"job {job_id}: published {title!r} as {sha}")
    return EXIT_OK


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def _cmd_preflight(cfg: WorkerConfig, builder: Builder) -> int:
    failed = False
    for name, fn in _preflight_checks(cfg, builder):
        reason = _run_check(fn)
        failed = failed or bool(reason)
        log(f"{'✗' if reason else '✓'} {name}{': ' + reason if reason else ''}")
    return EXIT_WAITING if failed else EXIT_OK


def _cmd_cancel(cfg: WorkerConfig, job_id: str) -> int:
    if not jobs.ID_RE.match(job_id):
        log("not a job id")
        return EXIT_FAILED
    lock = WorkerLock(cfg.lock_path)
    if not lock.acquire():
        log("a worker is running; try again in a minute")
        return EXIT_WAITING
    try:
        for e in jobs.read_entries(cfg.queue_dir):
            if e["id"] == job_id:
                if e["state"] != "queued":
                    log(f"job {job_id} is {e['state']}, not queued")
                    return EXIT_FAILED
                _save(cfg, jobs.transition(e, "failed", "cancelled by operator"))
                log(f"cancelled {job_id}")
                return EXIT_OK
        log(f"no job {job_id}")
        return EXIT_FAILED
    finally:
        lock.release()


def _cmd_status(cfg: WorkerConfig) -> int:
    entries = jobs.read_entries(cfg.queue_dir)
    if not entries:
        log("queue is empty")
    for e in entries:
        extra = f" [{e['commit']}]" if e.get("commit") else ""
        log(f"{e['id']}  {e['state']:<7}  {e['title']} — {e['author']}{extra}")
        if e.get("message"):
            log(f"    {e['message']}")
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m gttp.worker", description="gttp submit worker")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("run", help="build the oldest queued book (default)")
    sub.add_parser("preflight", help="show whether a job could start now")
    p_cancel = sub.add_parser("cancel", help="fail a queued job without deleting it")
    p_cancel.add_argument("job_id")
    sub.add_parser("status", help="print the queue")
    args = parser.parse_args(argv)

    if Path("/.dockerenv").exists():
        log("the worker runs on the host, never inside a container")
        return EXIT_WAITING
    try:
        cfg = WorkerConfig.from_env()
    except ValueError as exc:
        log(f"bad worker config: {exc}")
        return EXIT_WAITING
    builder = ContainerBuilder(cfg.repo, cfg.build_timeout_s)
    command = args.command or "run"
    if command == "preflight":
        return _cmd_preflight(cfg, builder)
    if command == "cancel":
        return _cmd_cancel(cfg, args.job_id)
    if command == "status":
        return _cmd_status(cfg)
    return run(cfg, builder, run_checks)


if __name__ == "__main__":
    sys.exit(main())
