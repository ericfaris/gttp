"""Check a title/author against Open Library's search API.

Only the resolver calls this, through its guarded fetcher. The response is
untrusted: `parse_docs` keeps only well-typed, cleaned, length-capped
fields, and every value that leaves goes through the casing functions (OL
titles are often sentence case from MARC records, "The charisma myth", so
they are re-cased rather than taken raw).

`lookup` never raises: network trouble, a bad response, the fetch budget
or the deadline all come back as `unverified`.
"""

from __future__ import annotations

import json
import threading
import time
import unicodedata
from collections import OrderedDict
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from urllib.parse import urlencode

from .casing import author_case, clean_candidate, join_authors, split_authors, title_case
from .linkparse import strip_edition
from .safefetch import FetchError

SEARCH = "https://openlibrary.org/search.json"
FIELDS = "title,author_name,first_publish_year"
ACCEPT = ("application/json",)
MAX_BYTES = 262_144
QUERY_TIMEOUT = 5.0
CHECK_BUDGET = 6.0
MAX_DOCS = 50
CACHE_TTL = 24 * 3600
CACHE_MAX = 256

TITLE_WITH_AUTHOR = 0.85
AUTHOR_MIN = 0.80
TITLE_ALONE = 0.92

DECOY_TERMS = (
    "summary", "summaries", "study guide", "workbook", "analysis", "sparknotes",
    "cliffsnotes", "cliff notes", "key takeaways", "companion", "box set", "boxed set",
    "boxset", "trivia", "quiz", "conversation starters", "collection",
)
DECOY_AUTHORS = (
    "instaread", "supersummary", "bookrags", "quickread", "readtrepreneur",
    "sapiens editorial", "summary station",
)


class OLError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class Doc:
    title: str
    authors: tuple[str, ...]
    year: int | None = None


@dataclass(frozen=True)
class Match:
    doc: Doc
    title_score: float
    author_score: float
    author_ok: bool | None  # None: no author was typed


@dataclass
class Outcome:
    kind: str  # matched | author_mismatch | not_found | unverified
    title: str = ""
    author: str = ""
    ol_title: str = ""
    ol_authors: list[str] = field(default_factory=list)
    code: str = ""


# -- normalizing and scoring ----------------------------------------------------

def normalize(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c)).casefold()
    s = s.replace("&", " and ")
    s = "".join(c if c.isalnum() else " " for c in s)
    words = s.split()
    if len(words) > 1 and words[0] in ("the", "a", "an"):
        words = words[1:]
    return " ".join(words)


def _ratio(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b, autojunk=False).ratio()


def main_title(t: str) -> str:
    return t.partition(": ")[0].strip()


def title_score(user: str, ol: str) -> float:
    u = normalize(user)
    return max(_ratio(u, normalize(ol)), _ratio(u, normalize(main_title(ol))))


def name_score(a: str, b: str) -> float:
    na, nb = normalize(a), normalize(b)
    sa, sb = " ".join(sorted(na.split())), " ".join(sorted(nb.split()))
    return max(_ratio(na, nb), _ratio(sa, sb))


def author_score(user_author: str, ol_authors) -> float:
    """Every typed name must be found among OL's authors: the worst of the
    per-name best scores."""
    names = split_authors(user_author)
    if not names or not ol_authors:
        return 0.0
    return min(max(name_score(n, o) for o in ol_authors) for n in names)


def is_decoy(doc: Doc, user_title: str) -> bool:
    t = f" {normalize(doc.title)} "
    u = f" {normalize(user_title)} "
    for term in DECOY_TERMS:
        k = f" {normalize(term)} "
        if k in t and k not in u:
            return True
    return any(normalize(a) in DECOY_AUTHORS for a in doc.authors)


# -- response parsing -------------------------------------------------------------

def parse_docs(body: bytes) -> list[Doc]:
    try:
        data = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise OLError("malformed") from None
    if not isinstance(data, dict) or not isinstance(data.get("docs"), list):
        raise OLError("malformed")
    docs = []
    for d in data["docs"][:MAX_DOCS]:
        if not isinstance(d, dict) or not isinstance(d.get("title"), str):
            continue
        title = clean_candidate(d["title"], 300)
        if not title:
            continue
        names = d.get("author_name")
        authors = []
        if isinstance(names, list):
            for n in names[:10]:
                if isinstance(n, str):
                    c = clean_candidate(n, 200)
                    if c:
                        authors.append(c)
        year = d.get("first_publish_year")
        if not isinstance(year, int) or isinstance(year, bool):
            year = None
        docs.append(Doc(title, tuple(authors), year))
    return docs


# -- matching --------------------------------------------------------------------

def match(user_title: str, user_author: str, docs: list[Doc], slug_mode: bool = False):
    real = [d for d in docs if not is_decoy(d, user_title)]
    if slug_mode:
        slug = normalize(user_title).split()
        for d in real:
            ol = normalize(main_title(d.title)).split()
            if not ol:
                continue
            if (len(ol) >= 2 and slug[: len(ol)] == ol) or (len(ol) == 1 and slug[:1] == ol):
                return Match(d, 1.0, 0.0, None)
        return None

    nu = normalize(user_title)
    scored = []
    for i, d in enumerate(real):
        ts = title_score(user_title, d.title)
        exact = nu == normalize(d.title) or nu == normalize(main_title(d.title))
        aus = author_score(user_author, d.authors) if user_author else 0.0
        scored.append((exact, ts, aus, -i, d))
    scored.sort(key=lambda x: (x[0], x[1] + x[2], x[3]), reverse=True)
    if user_author:
        for exact, ts, aus, _, d in scored:
            if ts >= TITLE_WITH_AUTHOR and aus >= AUTHOR_MIN:
                return Match(d, ts, aus, True)
        for exact, ts, aus, _, d in scored:
            if ts >= TITLE_ALONE:
                return Match(d, ts, aus, False)
        return None
    # With no author to corroborate, a one-word title must match exactly:
    # near-miss single words score high ("influence"/"influencer" 0.95,
    # "mindset"/"mindsets" 0.93) but are different books.
    multi_word = len(nu.split()) >= 2
    for exact, ts, aus, _, d in scored:
        if exact or (multi_word and ts >= TITLE_ALONE):
            return Match(d, ts, 0.0, None)
    return None


def canonical(user_title: str, user_author: str, m: Match) -> tuple[str, str]:
    t = m.doc.title if ": " in user_title else main_title(m.doc.title)
    title = title_case(strip_edition(t))
    names = split_authors(user_author) if user_author else []
    if names:
        out = []
        for n in names:
            best = max(m.doc.authors, key=lambda o: name_score(n, o), default=None)
            if best is not None and name_score(n, best) >= AUTHOR_MIN:
                out.append(author_case(best))
            else:
                out.append(author_case(n))
        author = join_authors(out)
    else:
        author = author_case(m.doc.authors[0]) if m.doc.authors else ""
    return title, author


# -- cache -----------------------------------------------------------------------

class _Cache:
    def __init__(self):
        self._d: OrderedDict[str, tuple[float, list[Doc]]] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key):
        with self._lock:
            hit = self._d.get(key)
            if hit is None:
                return None
            if time.monotonic() - hit[0] > CACHE_TTL:
                del self._d[key]
                return None
            return hit[1]

    def put(self, key, docs):
        with self._lock:
            self._d[key] = (time.monotonic(), docs)
            self._d.move_to_end(key)
            while len(self._d) > CACHE_MAX:
                self._d.popitem(last=False)

    def clear(self):
        with self._lock:
            self._d.clear()


_CACHE = _Cache()


def clear_cache():
    _CACHE.clear()


def query_url(params: dict) -> str:
    return SEARCH + "?" + urlencode(params)


def _search(params: dict, fetch, deadline: float) -> list[Doc]:
    url = query_url(params)
    cached = _CACHE.get(url)
    if cached is not None:
        return cached
    left = deadline - time.monotonic()
    if left <= 0.2:
        raise FetchError("timeout")
    res = fetch(url, min(QUERY_TIMEOUT, left))
    body = res if isinstance(res, (bytes, bytearray)) else res.body
    docs = parse_docs(bytes(body))
    _CACHE.put(url, docs)
    return docs


def _params(title: str, author: str, slug_mode: bool) -> dict:
    if slug_mode:
        return {"q": " ".join(title.split()[:8]), "limit": 5, "fields": FIELDS}
    p = {"title": title}
    if author:
        p["author"] = author
    p.update({"limit": 5, "fields": FIELDS})
    return p


def lookup(title: str, author: str, fetch, slug_mode: bool = False) -> Outcome:
    """`fetch(url, timeout)` returns a FetchResult (or bytes) or raises."""
    deadline = time.monotonic() + CHECK_BUDGET
    queries = [_params(title, author, slug_mode)]
    if author and not slug_mode:
        queries.append({"q": f"{title} {author}", "limit": 5, "fields": FIELDS})
    mismatch: Match | None = None
    try:
        for params in queries:
            docs = _search(params, fetch, deadline)
            m = match(title, author, docs, slug_mode)
            if m is None:
                continue
            if m.author_ok is False:
                mismatch = mismatch or m
                continue
            t, a = canonical(title, author, m)
            return Outcome("matched", title=t, author=a)
    except FetchError as e:
        if mismatch is None:
            return Outcome("unverified", code=e.code)
    except OLError as e:
        if mismatch is None:
            return Outcome("unverified", code=e.code)
    except Exception as e:  # never raise out of a lookup
        if mismatch is None:
            return Outcome("unverified", code=type(e).__name__)
    if mismatch is not None:
        return Outcome(
            "author_mismatch",
            ol_title=title_case(strip_edition(main_title(mismatch.doc.title))),
            ol_authors=[author_case(a) for a in mismatch.doc.authors[:2]],
        )
    return Outcome("not_found")
