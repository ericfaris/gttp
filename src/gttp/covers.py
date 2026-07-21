"""Book cover images from Open Library.

Covers are fetched once (at `gttp add` time, or in bulk via `gttp covers`) and
stored as committed repo assets under `covers/<slug>.jpg`. The build path never
touches the network: `write_site` copies whatever exists in `covers/` into
`site/covers/`, and books without a stored cover render a deterministic inline
SVG placeholder so `gttp build --offline` stays zero-network.

Only the Large ("L") Open Library size is stored; the index thumbnail and the
larger detail-page cover both render from that single file via CSS.
"""

from __future__ import annotations

import hashlib
import html
from pathlib import Path

import requests

from .config import ROOT

COVERS_DIR = ROOT / "covers"

_SEARCH_URL = "https://openlibrary.org/search.json"
_COVER_URL = "https://covers.openlibrary.org/b/id/{cover_i}-L.jpg"
_USER_AGENT = "gttp/0.1 (+https://github.com/ericfaris/gttp)"
_MIN_IMAGE_BYTES = 1024


def cover_file(slug: str, covers_dir: Path = COVERS_DIR) -> Path | None:
    """Return the stored cover path for a slug, or None if it doesn't exist."""
    path = covers_dir / f"{slug}.jpg"
    return path if path.exists() else None


def _search_cover_id(
    title: str, author: str | None, timeout: float
) -> int | None:
    """Resolve a title/author to an Open Library cover id, or None on any miss."""
    params = {
        "title": title,
        "limit": 5,
        "fields": "key,title,author_name,cover_i",
    }
    if author:
        params["author"] = author
    try:
        resp = requests.get(
            _SEARCH_URL,
            params=params,
            headers={"User-Agent": _USER_AGENT},
            timeout=timeout,
        )
        if resp.status_code != 200:
            return None
        docs = resp.json().get("docs", [])
    except (requests.RequestException, ValueError):
        return None
    for doc in docs:
        cover_i = doc.get("cover_i")
        if cover_i:
            return int(cover_i)
    return None


def fetch_cover(
    title: str,
    author: str | None,
    slug: str,
    covers_dir: Path = COVERS_DIR,
    timeout: float = 15.0,
) -> Path | None:
    """Fetch and store the Large cover for a book. Best-effort: returns the
    written path on success, None on any miss/failure (never raises)."""
    cover_i = _search_cover_id(title, author, timeout)
    if cover_i is None:
        return None
    try:
        resp = requests.get(
            _COVER_URL.format(cover_i=cover_i),
            params={"default": "false"},
            headers={"User-Agent": _USER_AGENT},
            timeout=timeout,
        )
    except requests.RequestException:
        return None
    if resp.status_code != 200 or len(resp.content) <= _MIN_IMAGE_BYTES:
        return None
    covers_dir.mkdir(parents=True, exist_ok=True)
    path = covers_dir / f"{slug}.jpg"
    path.write_bytes(resp.content)
    return path


def placeholder_svg(title: str, css_class: str) -> str:
    """Deterministic inline SVG cover placeholder: the title's first
    alphanumeric character over a title-derived hue. Uses md5 (never the
    process-randomized built-in hash()) so offline builds stay reproducible."""
    letter = next((c for c in title if c.isalnum()), "?").upper()
    digest = hashlib.md5(title.encode("utf-8")).hexdigest()
    hue = int(digest[:6], 16) % 360
    fill = f"hsl({hue} 40% 40%)"
    letter_esc = html.escape(letter)
    label = html.escape(f"Cover of {title}")
    return (
        f'<svg class="{css_class}" viewBox="0 0 60 90" role="img" '
        f'aria-label="{label}" preserveAspectRatio="xMidYMid slice">'
        f'<rect width="60" height="90" fill="{fill}"/>'
        f'<text x="30" y="45" fill="#fff" font-size="34" font-weight="700" '
        f'text-anchor="middle" dominant-baseline="central" '
        f'font-family="Georgia, \'Times New Roman\', serif">{letter_esc}</text>'
        f'</svg>'
    )
