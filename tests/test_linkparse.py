"""Page parsers (gttp.linkparse) against hand-written synthetic fixtures."""

from pathlib import Path

import pytest

from gttp import linkparse
from gttp.linkparse import (
    Candidate,
    amazon_slug_title,
    is_blocked_amazon,
    resolve_link,
    shorten_title,
    split_by_author,
    strip_site_suffix,
)
from gttp.safefetch import FetchError, FetchResult

PAGES = Path(__file__).parent / "fixtures" / "pages"
CHARISMA_LONG = ("The Charisma Myth: How Anyone Can Master the Art and Science of "
                 "Personal Magnetism")
AMAZON_URL = "https://www.amazon.com/Charisma-Myth-Science-Personal-Magnetism/dp/1591845947"


def page(name):
    return (PAGES / name).read_text(encoding="utf-8")


def serve(name=None, body=None, final_url=None, charset="utf-8"):
    def fetch(url):
        data = body if body is not None else page(name).encode("utf-8")
        return FetchResult(final_url or url, 200, "text/html", charset, data, False)
    return fetch


def failing(code):
    def fetch(url):
        raise FetchError(code)
    return fetch


@pytest.mark.parametrize("name, url, want", [
    ("goodreads-charisma.html", "https://www.goodreads.com/book/show/1",
     (CHARISMA_LONG, "Olivia Fox Cabane", "goodreads", [])),
    ("goodreads-og-only.html", "https://www.goodreads.com/book/show/2",
     ("Essentialism: The Disciplined Pursuit of Less", "Greg McKeown", "goodreads", [])),
    ("amazon-product.html", AMAZON_URL,
     (CHARISMA_LONG, "Olivia Fox Cabane", "amazon", [])),
    ("amazon-title-only.html", "https://www.amazon.co.uk/dp/1591845947",
     (CHARISMA_LONG, "Olivia Fox Cabane", "amazon", [])),
    ("generic-jsonld-graph.html", "https://publisher.example.org/deep-work",
     ("Deep Work: Rules for Focused Success in a Distracted World", "Cal Newport", "generic", [])),
    ("generic-og-book.html", "https://publisher.example.org/tiny",
     ("Tiny Experiments", "Anne-Laure Le Cunff", "generic", ["author-guessed"])),
    ("generic-title-only.html", "https://www.penguinrandomhouse.com/books/1",
     ("The Power of Habit", "", "generic", [])),
    ("generic-by-author.html", "https://bookshop.org/p/books/essentialism",
     ("Essentialism", "Greg McKeown", "generic", [])),
])
def test_fixtures(name, url, want):
    c = resolve_link(url, serve(name))
    assert (c.title_raw, c.author_raw, c.kind, c.notes) == want


def test_shorten_title():
    assert shorten_title(CHARISMA_LONG) == "The Charisma Myth"
    assert shorten_title("Grit (Paperback Edition)") == "Grit"
    assert shorten_title("Dune (Dune Book 1)") == "Dune"
    assert shorten_title("Why (Not)") == "Why (Not)"
    assert shorten_title(": Leading colon") == ": Leading colon"


def test_strip_site_suffix_and_split_by_author():
    assert strip_site_suffix("The Power of Habit | Penguin Random House") == "The Power of Habit"
    assert strip_site_suffix("Essentialism by Greg McKeown - Bookshop") == (
        "Essentialism by Greg McKeown")
    assert strip_site_suffix("Deep Work") == "Deep Work"
    assert split_by_author("Essentialism by Greg McKeown") == ("Essentialism", "Greg McKeown")
    assert split_by_author("Stand by me") == ("Stand by me", None)


def test_amazon_captcha_falls_back_to_the_slug():
    assert is_blocked_amazon(page("amazon-captcha.html"))
    assert not is_blocked_amazon(page("amazon-product.html"))
    c = resolve_link(AMAZON_URL, serve("amazon-captcha.html"))
    assert c.title_raw == "Charisma Myth Science Personal Magnetism"
    assert c.notes == ["amazon-slug"] and c.author_raw == ""


def test_fetch_errors():
    c = resolve_link(AMAZON_URL, failing("http-503"))
    assert c.notes == ["amazon-slug"] and c.title_raw.startswith("Charisma Myth")
    c = resolve_link("https://books.example.org/x", failing("http-503"))
    assert (c.title_raw, c.author_raw, c.notes) == ("", "", ["fetch-failed:http-503"])
    c = resolve_link("https://books.example.org/x", failing("blocked-address"))
    assert c.notes == ["blocked"]
    c = resolve_link("https://www.amazon.com/dp/1591845947", failing("timeout"))
    assert c.notes == ["fetch-failed:timeout"]


def test_redirect_to_amazon_uses_the_final_url():
    c = resolve_link("https://a.co/d/abc", serve("amazon-product.html", final_url=AMAZON_URL))
    assert c.kind == "amazon" and c.author_raw == "Olivia Fox Cabane"


def test_malformed_page_does_not_raise():
    c = resolve_link("https://books.example.org/x", serve("malformed.html"))
    assert isinstance(c, Candidate)
    assert c.title_raw == "Grit & Glory"


def test_truncated_big_page_still_parses():
    head = page("goodreads-charisma.html").split("</head>")[0]
    body = (head + "<p>" + "x" * 1_100_000).encode()[:1_048_576]
    c = resolve_link("https://www.goodreads.com/book/show/1", serve(body=body))
    assert c.title_raw == CHARISMA_LONG and c.author_raw == "Olivia Fox Cabane"


def test_unknown_charset_falls_back_to_utf8():
    c = resolve_link("https://www.goodreads.com/book/show/1",
                     serve("goodreads-charisma.html", charset="x-nonsense"))
    assert c.author_raw == "Olivia Fox Cabane"


def test_amazon_slug_title():
    assert amazon_slug_title(AMAZON_URL) == "Charisma Myth Science Personal Magnetism"
    assert amazon_slug_title("https://www.amazon.com/dp/1591845947") is None
    assert amazon_slug_title("https://www.amazon.com/gp/product/1591845947") is None
    assert amazon_slug_title("https://www.amazon.com/123-456/dp/1591845947") is None
    assert amazon_slug_title("https://www.amazon.com/search?q=x") is None


def test_dispatch():
    assert linkparse.kind_for("https://www.goodreads.com/x") == "goodreads"
    assert linkparse.kind_for("https://smile.amazon.co.uk/x") == "amazon"
    assert linkparse.kind_for("https://notamazon.com/x") == "generic"
    assert linkparse.kind_for("https://goodreads.com.evil.example/x") == "generic"


def test_author_cleanup():
    assert linkparse.clean_author("Olivia Fox Cabane (Goodreads Author)") == "Olivia Fox Cabane"
    assert linkparse.clean_author("https://example.com/author") == ""
