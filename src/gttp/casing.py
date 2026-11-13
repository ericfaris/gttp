"""Deterministic title and author casing, and cleanup of untrusted candidate
strings (page metadata, Open Library results).

Pure functions, stdlib only, no I/O. Deliberately imports nothing from the
package (not even config), so the submit service can use it for its
casing-only fallback without pulling anything else in.

The rules keep any word that already carries an inner capital ("McKeown",
"iPhone", "NASA", "Le Guin"), so applying them to an already-correct value
is a no-op: every title and author in books.yaml is a fixed point (a test
checks this).
"""

from __future__ import annotations

import html
import re
import unicodedata

SMALL_WORDS = frozenset(
    "a an the and but or for nor on at to by of in vs vs. as".split()
)
# Lowercase words that become uppercase roman numerals. Never i, v, x (too
# ambiguous) nor anything that merely looks like one ("mix", "civil").
ROMAN = frozenset("ii iii iv vi vii viii ix xi xii xiii".split())
# A word ending in one of these makes the next word start a new phrase.
_PHRASE_END = (":", "—", "–", "?", "!")
PARTICLES = frozenset(
    "de da das del della der den di do dos du la le ten ter van von y bin ibn al".split()
)
_LEADING_PUNCT = "(\"'“‘[«"
_TRAILING_PUNCT = ".,;:!?)\"'”’]»"
_INITIALS_RE = re.compile(r"^([A-Za-z]\.)+$")
_QUOTE_PAIRS = (('"', '"'), ("“", "”"), ("'", "'"), ("‘", "’"))


def _is_invisible(c: str) -> bool:
    # The same rule as gttp.jobs._is_invisible (copied, not imported, so this
    # module stays dependency-free): category C (control, format, surrogate,
    # private use, unassigned) plus the Unicode line/paragraph separators.
    cat = unicodedata.category(c)
    return cat[0] == "C" or cat in ("Zl", "Zp")


def clean_candidate(s: str, max_len: int = 300) -> str:
    """Untrusted text -> one clean line, or "" if it is too long."""
    if not isinstance(s, str):
        return ""
    s = html.unescape(s)
    s = unicodedata.normalize("NFC", s)
    # Whitespace (including \n and \t) becomes a space before invisible
    # characters are dropped, so "a\nb" stays two words.
    s = "".join(" " if c.isspace() else c for c in s)
    s = "".join(c for c in s if not _is_invisible(c))
    s = " ".join(s.split())
    for open_q, close_q in _QUOTE_PAIRS:
        if len(s) >= 2 and s.startswith(open_q) and s.endswith(close_q):
            s = s[1:-1].strip()
            break
    if len(s) > max_len:
        return ""
    return s


# -- titles -------------------------------------------------------------------

def _has_inner_upper(word: str) -> bool:
    """An uppercase letter anywhere after the first letter."""
    seen_letter = False
    for c in word:
        if c.isalpha():
            if seen_letter and c.isupper():
                return True
            seen_letter = True
    return False


def _upper_first_letter(word: str) -> str:
    for i, c in enumerate(word):
        if c.isalpha():
            return word[:i] + c.upper() + word[i + 1:]
        if c.isdigit():
            return word
    return word


def _core(word: str) -> str:
    return word.lstrip(_LEADING_PUNCT).rstrip(_TRAILING_PUNCT.replace(".", ""))


def _title_part(part: str, force_cap: bool) -> str:
    if not part:
        return part
    stripped = part.lstrip(_LEADING_PUNCT)
    if stripped[:1].isdigit():
        return part
    if _has_inner_upper(part):
        return part
    core = _core(part).lower()
    if not force_cap and core in SMALL_WORDS:
        return part.lower()
    if part == part.lower() and core.rstrip(".") in ROMAN:
        return part.upper()
    return _upper_first_letter(part)


def title_case(s: str) -> str:
    words = s.split()
    if not words:
        return ""
    letters = [c for c in s if c.isalpha() and (c.isupper() or c.islower())]
    worded = sum(1 for w in words if any(c.isalpha() for c in w))
    if letters and all(c.isupper() for c in letters) and worded >= 2:
        words = [w.lower() for w in words]
    out = []
    last = len(words) - 1
    for i, word in enumerate(words):
        force = i == 0 or i == last or words[i - 1].endswith(_PHRASE_END)
        parts = word.split("-")
        cased = [_title_part(parts[0], force)]
        for p in parts[1:]:
            # Parts after a hyphen follow the small-word rule ("Out-of-Body").
            cased.append(_title_part(p, False))
        out.append("-".join(cased))
    return " ".join(out)


# -- authors ------------------------------------------------------------------

_AND_RE = re.compile(r"\s+and\s+|\s*&\s*", re.IGNORECASE)


def split_authors(s: str) -> list[str]:
    """"A and B", "A & B", "A, B and C" -> names. Commas split only when every
    comma-separated part has at least two words, so "Fox Cabane, Olivia"
    stays one name."""
    names: list[str] = []
    for chunk in _AND_RE.split(s.strip()):
        chunk = chunk.strip().strip(",").strip()
        if not chunk:
            continue
        parts = [p.strip() for p in chunk.split(",")]
        if len(parts) > 1 and all(len(p.split()) >= 2 for p in parts):
            names.extend(parts)
        else:
            names.append(" ".join(chunk.split()))
    return names


def join_authors(names: list[str]) -> str:
    names = [n for n in names if n]
    if len(names) <= 1:
        return names[0] if names else ""
    if len(names) == 2:
        return f"{names[0]} and {names[1]}"
    return ", ".join(names[:-1]) + " and " + names[-1]


def _cap_name_part(part: str) -> str:
    low = part
    i = 0
    while i < len(low) and not low[i].isalpha():
        i += 1
    head, rest = low[:i], low[i:]
    if not rest:
        return part
    if len(rest) > 2 and rest.startswith("mc") and rest[2].isalpha():
        return head + "Mc" + rest[2].upper() + rest[3:]
    if len(rest) > 2 and rest[:2] in ("o'", "d'", "o’", "d’") and rest[2].isalpha():
        return head + rest[0].upper() + rest[1] + rest[2].upper() + rest[3:]
    return head + rest[0].upper() + rest[1:]


def _cap_name_token(token: str) -> str:
    return "-".join(_cap_name_part(p) for p in token.split("-"))


def _case_name(name: str) -> str:
    tokens = name.split()
    has_lower = any(c.islower() for c in name)
    has_upper = any(c.isupper() for c in name)
    all_one_case = not has_lower or not has_upper
    out: list[str] = []
    last = len(tokens) - 1
    for i, tok in enumerate(tokens):
        if _INITIALS_RE.match(tok):
            out.append(" ".join(f"{c.upper()}." for c in tok.split(".") if c))
            continue
        letters = [c for c in tok if c.isalpha()]
        if _has_inner_upper(tok):
            if all_one_case and len(letters) >= 3 and all(c.isupper() for c in letters):
                out.append(_cap_name_token(tok.lower()))
            else:
                out.append(tok)
            continue
        if 0 < i < last and tok.lower() in PARTICLES:
            if all_one_case:
                low = tok.lower()
                out.append(low.capitalize() if low in ("le", "la") else low)
            else:
                out.append(tok)
            continue
        if tok == tok.lower():
            out.append(_cap_name_token(tok))
            continue
        out.append(tok)
    return " ".join(out)


def author_case(s: str) -> str:
    names = split_authors(s)
    return join_authors([_case_name(n) for n in names])


# -- one call per field -------------------------------------------------------

def apply_casing(field: str, value: str) -> tuple[str, dict | None]:
    """(new value, change or None) for field "title" or "author"."""
    new = title_case(value) if field == "title" else author_case(value)
    if new == value:
        return value, None
    return new, {"field": field, "from": value, "to": new, "reason": "capitalization"}
