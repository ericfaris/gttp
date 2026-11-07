"""Book title/author candidates from a fetched page.

Everything here is untrusted text in, untrusted candidate strings out: the
parsers never raise on bad HTML, every string goes through
`casing.clean_candidate`, and the caller (gttp.resolver) still validates
the result before it leaves the service. Per-site parsers for Goodreads
and Amazon, and a generic one (JSON-LD Book/Product, og: tags, <title>).
Amazon often answers bots with a captcha page; the `dp` URL slug is then
the fallback title guess.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.parse import unquote, urlsplit

from .casing import clean_candidate, join_authors
from .safefetch import FetchError

META_KEYS = ("og:title", "og:type", "book:author", "author", "twitter:title")
MAX_LD_SCRIPTS = 20
MAX_LD_BYTES = 200 * 1024
MAX_LD_NODES = 50
MAX_LD_DEPTH = 4
MAX_AUTHORS = 3

PAGE_ACCEPT = ("text/html", "application/xhtml+xml")
PAGE_MAX_BYTES = 1_048_576
PAGE_TIMEOUT = 8.0

_GOODREADS_RE = re.compile(r"(^|\.)goodreads\.com$")
_AMAZON_RE = re.compile(r"(^|\.)amazon\.(com|ca|co\.uk|com\.au|de|fr|in|it|es|co\.jp)$")
_SLUG_RE = re.compile(r"^/(?:(?P<slug>[^/]+)/)?(?:dp|gp/product)/[A-Z0-9]{10}(?:[/?]|$)")
_EDITION_RE = re.compile(
    r"\s*\((?=[^)]*(?:edition|paperback|hardcover|kindle|audiobook|book \d+|volume \d+"
    r"|series|unabridged))[^)]*\)\s*$",
    re.IGNORECASE,
)
_AUTHOR_ROLE_RE = re.compile(
    r"\s*\((?:goodreads author|editor|editors|translator|illustrator|narrator|contributor"
    r"|foreword|introduction|author)[^)]*\)",
    re.IGNORECASE,
)
_SITES = (
    "goodreads", "amazon", "amazon.com", "barnes & noble", "bookshop", "google books",
    "open library", "audible", "penguin random house", "simon & schuster",
    "harpercollins", "wikipedia",
)
_SEPARATORS = (" | ", " - ", " – ", " — ", " · ", " : ")
_BY_RE = re.compile(r"\s+by\s+", re.IGNORECASE)


@dataclass
class Candidate:
    title_raw: str = ""
    author_raw: str = ""
    kind: str = "generic"
    notes: list[str] = field(default_factory=list)


class MetaExtractor(HTMLParser):
    """Collects meta tags, <title>, JSON-LD scripts and two Amazon elements.
    Never raises (see `extract`)."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.meta: dict[str, str] = {}
        self.title: str | None = None
        self.ld: list[str] = []
        self.product_title: str | None = None
        self.byline: str | None = None
        self._title_buf: list[str] | None = None
        self._ld_buf: list[str] | None = None
        self._ld_len = 0
        self._cap: list | None = None  # [name, tag, depth, parts]
        self._byline_tag: str | None = None
        self._byline_depth = 0

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "meta":
            key = (a.get("property") or a.get("name") or "").strip().lower()
            if key in META_KEYS and key not in self.meta and "content" in a:
                self.meta[key] = a["content"]
        elif tag == "title" and self.title is None and self._title_buf is None:
            self._title_buf = []
        elif (tag == "script" and a.get("type", "").strip().lower() == "application/ld+json"
              and len(self.ld) < MAX_LD_SCRIPTS):
            self._ld_buf, self._ld_len = [], 0

        if self._cap is not None and tag == self._cap[1]:
            self._cap[2] += 1
        if a.get("id") == "productTitle" and self.product_title is None and self._cap is None:
            self._cap = ["product", tag, 1, []]

        if self._byline_tag is not None and tag == self._byline_tag:
            self._byline_depth += 1
        elif self._byline_tag is None and self.byline is None and (
            (tag == "span" and "author" in a.get("class", "").split())
            or a.get("id") == "bylineInfo"
        ):
            self._byline_tag, self._byline_depth = tag, 1
        if (self._byline_tag is not None and tag == "a" and self.byline is None
                and self._cap is None):
            self._cap = ["byline", "a", 1, []]

    def handle_endtag(self, tag):
        if tag == "title" and self._title_buf is not None:
            self.title = "".join(self._title_buf)
            self._title_buf = None
        if tag == "script" and self._ld_buf is not None:
            if self._ld_len <= MAX_LD_BYTES:
                self.ld.append("".join(self._ld_buf))
            self._ld_buf = None
        if self._cap is not None and tag == self._cap[1]:
            self._cap[2] -= 1
            if self._cap[2] <= 0:
                name, _, _, parts = self._cap
                text = "".join(parts)
                if name == "product":
                    self.product_title = text
                else:
                    self.byline = text
                self._cap = None
        if self._byline_tag is not None and tag == self._byline_tag:
            self._byline_depth -= 1
            if self._byline_depth <= 0:
                self._byline_tag = None

    def handle_data(self, data):
        if self._title_buf is not None:
            self._title_buf.append(data)
        if self._ld_buf is not None:
            self._ld_len += len(data)
            if self._ld_len <= MAX_LD_BYTES:
                self._ld_buf.append(data)
        if self._cap is not None:
            self._cap[3].append(data)


def extract(html: str) -> MetaExtractor:
    p = MetaExtractor()
    try:
        p.feed(html)
        p.close()
    except Exception:
        pass
    if p.title is None and p._title_buf:
        p.title = "".join(p._title_buf)
    return p


# -- JSON-LD ----------------------------------------------------------------------

def _types(node: dict) -> set[str]:
    t = node.get("@type")
    if isinstance(t, str):
        return {t}
    if isinstance(t, list):
        return {x for x in t if isinstance(x, str)}
    return set()


def _ld_nodes(blobs: list[str]) -> list[dict]:
    nodes: list[dict] = []

    def walk(v, depth):
        if len(nodes) >= MAX_LD_NODES or depth > MAX_LD_DEPTH:
            return
        if isinstance(v, dict):
            nodes.append(v)
            for child in v.values():
                if isinstance(child, (dict, list)):
                    walk(child, depth + 1)
        elif isinstance(v, list):
            for child in v[:MAX_LD_NODES]:
                walk(child, depth + 1)

    for blob in blobs:
        try:
            walk(json.loads(blob), 0)
        except (ValueError, RecursionError):
            continue
    return nodes


def _ld_item(nodes: list[dict], allow_product: bool) -> dict | None:
    for want in ("Book", "Product") if allow_product else ("Book",):
        for n in nodes:
            if want in _types(n):
                return n
    return None


def _first_str(v) -> str:
    if isinstance(v, str):
        return v
    if isinstance(v, list):
        for x in v:
            if isinstance(x, str):
                return x
    return ""


def _ld_authors(v) -> list[str]:
    items = v if isinstance(v, list) else [v]
    names = []
    for it in items[:10]:
        if isinstance(it, str):
            names.append(it)
        elif isinstance(it, dict):
            names.append(_first_str(it.get("name")))
        if len([n for n in names if n]) >= MAX_AUTHORS:
            break
    return [n for n in names if n]


# -- string cleanup -------------------------------------------------------------

def clean_author(s: str) -> str:
    s = clean_candidate(s)
    if s.lower().startswith("http"):
        return ""
    return " ".join(_AUTHOR_ROLE_RE.sub("", s).split()).strip(" ,")


def _authors(names: list[str]) -> str:
    cleaned = []
    for n in names:
        c = clean_author(n)
        if c and c not in cleaned:
            cleaned.append(c)
    return join_authors(cleaned[:MAX_AUTHORS])


def shorten_title(t: str) -> str:
    head, sep, _ = t.partition(": ")
    if sep and any(c.isalnum() for c in head):
        t = head
    prev = None
    while prev != t:
        prev = t
        t = _EDITION_RE.sub("", t).strip()
    return t


def strip_site_suffix(t: str) -> str:
    for _ in range(2):
        best = -1
        for sep in _SEPARATORS:
            i = t.rfind(sep)
            if i > best:
                best, cut = i, sep
        if best <= 0:
            break
        rest, seg = t[:best].strip(), t[best + len(cut):].strip()
        low = seg.lower()
        if any(site in low for site in _SITES) or (
            len(seg.split()) <= 3 and len(rest) > len(seg)
        ):
            t = rest
        else:
            break
    return t


def split_by_author(t: str) -> tuple[str, str | None]:
    matches = list(_BY_RE.finditer(t))
    if not matches:
        return t, None
    m = matches[-1]
    left, right = t[: m.start()].strip(), t[m.end():].strip()
    words = right.split()
    if left and 1 <= len(words) <= 6 and right[:1].isupper():
        return left, right
    return t, None


def _title_and_author_from_text(text: str) -> tuple[str, str]:
    t = strip_site_suffix(clean_candidate(text))
    title, author = split_by_author(t)
    return title, author or ""


# -- parsers --------------------------------------------------------------------

def parse_goodreads(html: str, url: str) -> Candidate:
    p = extract(html)
    c = Candidate(kind="goodreads")
    book = _ld_item(_ld_nodes(p.ld), allow_product=False)
    if book is not None:
        c.title_raw = clean_candidate(_first_str(book.get("name")))
        c.author_raw = _authors(_ld_authors(book.get("author")))
    if not c.title_raw:
        text = p.meta.get("og:title") or p.title or ""
        c.title_raw, by = _title_and_author_from_text(text)
        if not c.author_raw:
            c.author_raw = clean_author(by)
    return c


def is_blocked_amazon(html: str) -> bool:
    if any(s in html for s in ("/errors/validateCaptcha", "Robot Check",
                               "api-services-support@amazon.com")):
        return True
    title = extract(html).title
    return clean_candidate(title or "").lower() == "amazon.com"


_AMAZON_STRIP = (
    (re.compile(r"^amazon\.[a-z.]+\s*:\s*", re.IGNORECASE), ""),
    (re.compile(r"\s*:\s*amazon\.[a-z.]+\s*:\s*[^:]*$", re.IGNORECASE), ""),
    (re.compile(r"\s*:\s*books\s*$", re.IGNORECASE), ""),
    (re.compile(r"\s*:\s*[\dX]{10,13}\s*$"), ""),
)


def _strip_amazon(t: str) -> str:
    t = clean_candidate(t)
    for rx, rep in _AMAZON_STRIP:
        t = rx.sub(rep, t).strip()
    return t


def parse_amazon(html: str, url: str) -> Candidate:
    p = extract(html)
    c = Candidate(kind="amazon")
    if p.product_title:
        c.title_raw = clean_candidate(p.product_title)
    if not c.title_raw and p.meta.get("og:title"):
        c.title_raw = _strip_amazon(p.meta["og:title"])
    page_title = _strip_amazon(p.title or "")
    if p.byline:
        c.author_raw = clean_author(p.byline)
    if not c.title_raw and page_title:
        c.title_raw = page_title
    if not c.author_raw and page_title:
        # "Title: Last, First" (the books page <title> shape).
        head, sep, last = page_title.rpartition(": ")
        if sep and last.count(",") == 1:
            surname, first = (x.strip() for x in last.split(","))
            if surname and first and len(last.split()) <= 6:
                c.author_raw = clean_author(f"{first} {surname}")
                if c.title_raw == page_title:
                    c.title_raw = head
    return c


def amazon_slug_title(url: str) -> str | None:
    try:
        path = urlsplit(url).path
    except ValueError:
        return None
    m = _SLUG_RE.match(path)
    if not m or not m.group("slug"):
        return None
    slug = clean_candidate(unquote(m.group("slug")).replace("-", " "))
    return slug if any(ch.isalpha() for ch in slug) else None


def parse_generic(html: str, url: str) -> Candidate:
    p = extract(html)
    c = Candidate(kind="generic")
    item = _ld_item(_ld_nodes(p.ld), allow_product=True)
    if item is not None:
        c.title_raw = clean_candidate(_first_str(item.get("name")))
        c.author_raw = _authors(_ld_authors(item.get("author")))
    by = ""
    if not c.title_raw and p.meta.get("og:title"):
        c.title_raw = strip_site_suffix(clean_candidate(p.meta["og:title"]))
    if not c.title_raw and p.title:
        c.title_raw, by = _title_and_author_from_text(p.title)
    if not c.author_raw and (p.meta.get("og:type") or "").strip().lower() == "book":
        c.author_raw = clean_author(p.meta.get("book:author", ""))
    if not c.author_raw and by:
        c.author_raw = clean_author(by)
    if not c.author_raw and p.meta.get("author"):
        c.author_raw = clean_author(p.meta["author"])
        if c.author_raw:
            c.notes.append("author-guessed")
    return c


def _host(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").lower().rstrip(".")
    except ValueError:
        return ""


def kind_for(url: str) -> str:
    host = _host(url)
    if _GOODREADS_RE.search(host):
        return "goodreads"
    if _AMAZON_RE.search(host):
        return "amazon"
    return "generic"


_PARSERS = {"goodreads": parse_goodreads, "amazon": parse_amazon, "generic": parse_generic}


def _slug_fallback(*urls: str) -> Candidate | None:
    for u in urls:
        if kind_for(u) == "amazon":
            slug = amazon_slug_title(u)
            if slug:
                return Candidate(title_raw=slug, kind="amazon", notes=["amazon-slug"])
    return None


def resolve_link(url: str, fetch) -> Candidate:
    """Fetch `url` (fetch is safefetch.fetch with the page settings bound)
    and parse it. Never raises; failures come back as notes."""
    try:
        res = fetch(url)
    except FetchError as e:
        fb = _slug_fallback(url)
        if fb:
            return fb
        note = "blocked" if e.code.startswith("blocked") else f"fetch-failed:{e.code}"
        return Candidate(kind=kind_for(url), notes=[note])
    try:
        text = res.body.decode(res.charset or "utf-8", errors="replace")
    except LookupError:
        text = res.body.decode("utf-8", errors="replace")
    kind = kind_for(res.url)
    if kind == "amazon" and is_blocked_amazon(text):
        fb = _slug_fallback(res.url, url)
        if fb:
            return fb
        return Candidate(kind="amazon", notes=["fetch-failed:amazon-blocked"])
    try:
        return _PARSERS[kind](text, res.url)
    except Exception:
        return Candidate(kind=kind, notes=["fetch-failed:parse"])
