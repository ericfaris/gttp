"""The book-submission queue: validation, entry schema, state machine and I/O.

Shared by the submit service (which only creates `queued` entries) and the
host-side worker (which moves them through `running` to `done`/`failed`).
Each entry is one JSON file, `.cache/queue/<id>.json`.

The submit container can write anything into the queue directory, so the
worker treats every entry as untrusted: `read_entries` skips anything that
doesn't look like an entry, and the worker re-validates title and author
and recomputes the slug itself.

Deliberately imports nothing heavier than config (no cache, pipeline or
reddit client): the submit service imports this module and must stay small.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import unicodedata
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .config import BOOKS_FILE, ROOT, load_catalog, slugify

TITLE_MAX = 120
AUTHOR_MAX = 80
SLUG_MAX = 100
MAX_ENTRY_BYTES = 16 * 1024
MESSAGE_MAX = 300
TZ = ZoneInfo("America/New_York")
QUEUE_DIR = ROOT / ".cache" / "queue"
ID_RE = re.compile(r"^\d{13}-[0-9a-f]{8}$")
SLUG_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
STATES = ("queued", "running", "done", "failed")
TRANSITIONS = {
    "queued": {"running", "failed"},
    "running": {"done", "failed"},
    "done": set(),
    "failed": set(),
}
# Fields `transition` may set besides state/message.
_RESULT_FIELDS = {"commit", "usd"}


def _is_invisible(c: str) -> bool:
    # Category C: control (\n, \t, \x00), format (zero-width, bidi such as
    # U+200B / U+202E), surrogate, private use, unassigned. Zl/Zp are the
    # Unicode line and paragraph separators, newlines by another name.
    cat = unicodedata.category(c)
    return cat[0] == "C" or cat in ("Zl", "Zp")


def validate_field(name: str, value, max_len: int) -> str | None:
    """The error message for one submitted field, or None if it's fine.
    Rejects rather than sanitizes: a bad value is never silently fixed."""
    if not isinstance(value, str):
        return f"{name} must be text"
    if not value:
        return f"{name} is required"
    if value != value.strip():
        return f"{name} has leading or trailing spaces"
    if len(value) > max_len:
        return f"{name} must be at most {max_len} characters"
    if any(_is_invisible(c) for c in value):
        return f"{name} contains control or invisible characters"
    if value.startswith("-"):
        return f"{name} must not start with '-'"
    return None


def validate_submission(title, author) -> tuple[str | None, str | None]:
    """(error, slug): exactly one of them is None."""
    err = validate_field("title", title, TITLE_MAX) or validate_field(
        "author", author, AUTHOR_MAX
    )
    if err:
        return err, None
    slug = slugify(title)
    if not SLUG_RE.match(slug):
        return "title must contain letters or digits (A-Z, 0-9) for its page address", None
    if len(slug) > SLUG_MAX:
        return f"title is too long for its page address (at most {SLUG_MAX} letters and digits)", None
    return None, slug


URL_MAX = 2048
CHECK_FIELDS = frozenset({"url", "title", "author"})


def validate_check(data: dict) -> tuple[str | None, str, str, str]:
    """(error, url, title, author) for a link-check request ({url?, title?,
    author?}). Shared by the submit service and the resolver, which each
    validate it. "" counts as absent."""
    url, title, author = data.get("url", ""), data.get("title", ""), data.get("author", "")
    if url != "":
        if not isinstance(url, str):
            return "link must be text", "", "", ""
        if len(url) > URL_MAX:
            return f"link must be at most {URL_MAX} characters", "", "", ""
        if any(_is_invisible(c) for c in url):
            return "link contains control or invisible characters", "", "", ""
        if not url.lower().startswith(("http://", "https://")):
            return "link must start with http:// or https://", "", "", ""
    for name, value, max_len in (("title", title, TITLE_MAX), ("author", author, AUTHOR_MAX)):
        if value != "":
            err = validate_field(name, value, max_len)
            if err:
                return err, "", "", ""
    if not url and not title:
        return "give a link or a title", "", "", ""
    return None, url, title, author


def _iso(now: datetime) -> str:
    return now.astimezone(TZ).isoformat(timespec="seconds")


def _now(now: datetime | None) -> datetime:
    return now if now is not None else datetime.now(TZ)


def new_entry(title: str, author: str, slug: str, now: datetime | None = None) -> dict:
    now = _now(now)
    stamp = _iso(now)
    return {
        "v": 1,
        "id": f"{int(now.timestamp() * 1000):013d}-{secrets.token_hex(4)}",
        "title": title,
        "author": author,
        "slug": slug,
        "state": "queued",
        "message": "",
        "created_at": stamp,
        "updated_at": stamp,
        "started_at": None,
        "finished_at": None,
        "commit": None,
        "usd": None,
    }


def transition(
    entry: dict, new_state: str, message: str = "", now: datetime | None = None, **fields
) -> dict:
    """A copy of `entry` moved to `new_state`. Raises ValueError on a move
    the state machine doesn't allow (done and failed are terminal)."""
    old = entry.get("state")
    if new_state not in TRANSITIONS.get(old, set()):
        raise ValueError(f"cannot move a job from {old!r} to {new_state!r}")
    unknown = set(fields) - _RESULT_FIELDS
    if unknown:
        raise ValueError(f"unknown entry fields: {sorted(unknown)}")
    stamp = _iso(_now(now))
    out = {**entry, **fields, "state": new_state, "message": message[:MESSAGE_MAX]}
    out["updated_at"] = stamp
    if new_state == "running":
        out["started_at"] = stamp
    if new_state in ("done", "failed"):
        out["finished_at"] = stamp
    return out


def note(entry: dict, message: str, now: datetime | None = None) -> dict:
    """A copy of `entry` with a new message and no state change (the
    worker's "waiting: …" updates on a queued entry)."""
    return {**entry, "message": message[:MESSAGE_MAX], "updated_at": _iso(_now(now))}


def write_entry(queue_dir: Path, entry: dict) -> Path:
    """Atomically write an entry: temp file in the same directory, fsync,
    rename. (A rename is safe here: the queue is a directory mount.)"""
    if not ID_RE.match(str(entry.get("id", ""))):
        raise ValueError("bad entry id")
    final = queue_dir / f"{entry['id']}.json"
    tmp = queue_dir / f".{entry['id']}.tmp"
    data = json.dumps(entry, indent=2, ensure_ascii=False).encode("utf-8")
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, final)
    return final


def _load_entry(path: Path) -> dict | None:
    if not ID_RE.match(path.stem):
        return None
    try:
        if path.stat().st_size > MAX_ENTRY_BYTES:
            return None
        d = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(d, dict) or d.get("state") not in STATES:
        return None
    if not all(isinstance(d.get(k), str) for k in ("id", "title", "author")):
        return None
    if d["id"] != path.stem:
        return None
    if not isinstance(d.get("created_at"), str):
        return None
    return d


def read_entries(queue_dir: Path) -> list[dict]:
    """Every well-formed entry, oldest first. Anything else in the directory
    is skipped with one warning line (hidden temp files silently)."""
    if not queue_dir.is_dir():
        return []
    entries = []
    for path in queue_dir.iterdir():
        if path.name.startswith("."):
            continue  # in-flight atomic writes, probes
        entry = _load_entry(path) if path.suffix == ".json" and path.is_file() else None
        if entry is None:
            print(f"[queue] skipping {path.name!r}: not a valid queue entry")
            continue
        entries.append(entry)
    entries.sort(key=lambda e: (e["created_at"], e["id"]))
    return entries


def _local_date(stamp: str):
    try:
        return datetime.fromisoformat(stamp).astimezone(TZ).date()
    except (TypeError, ValueError):
        return None


def count_today(entries: list[dict], now: datetime | None = None) -> int:
    """Entries created on today's America/New_York date, whatever their state."""
    today = _now(now).astimezone(TZ).date()
    return sum(1 for e in entries if _local_date(e.get("created_at")) == today)


def active_count(entries: list[dict]) -> int:
    return sum(1 for e in entries if e["state"] in ("queued", "running"))


def catalog_slugs(path: Path = BOOKS_FILE) -> set[str]:
    return {b.slug for b in load_catalog(path)}


def find_duplicate(
    slug: str, entries: list[dict], catalog: set[str], title: str | None = None
) -> str | None:
    """Why `slug` can't be submitted, or None. A failed entry doesn't block
    a resubmission."""
    shown = title or slug
    if slug in catalog:
        return f"'{shown}' is already in the catalog"
    for e in entries:
        if slugify(e["title"]) != slug:
            continue
        if e["state"] in ("queued", "running"):
            return f"'{shown}' is already queued"
        if e["state"] == "done":
            return f"'{shown}' was already published"
    return None
