"""Open Library matcher (gttp.openlibrary) with a stub HTTP layer."""

from pathlib import Path

import pytest

from gttp import openlibrary as ol
from gttp.safefetch import FetchError

FIX = Path(__file__).parent / "fixtures" / "openlibrary"


def body(name):
    return (FIX / name).read_bytes()


class Stub:
    """fetch(url, timeout): the same fixture for every URL, or per-URL."""

    def __init__(self, default=None, by_url=None, raises=None):
        self.default, self.by_url, self.raises = default, by_url or {}, raises
        self.calls = []

    def __call__(self, url, timeout):
        self.calls.append(url)
        assert timeout <= ol.QUERY_TIMEOUT
        if self.raises:
            raise self.raises
        if url in self.by_url:
            return body(self.by_url[url])
        return body(self.default or "empty.json")


def test_exact_hit():
    o = ol.lookup("The Charisma Myth", "Olivia Fox Cabane", Stub("charisma.json"))
    assert (o.kind, o.title, o.author) == ("matched", "The Charisma Myth", "Olivia Fox Cabane")


def test_close_hit_fixes_a_typo():
    o = ol.lookup("The Charsima Myth", "Olivia Fox Cabane", Stub("charisma.json"))
    assert (o.kind, o.title) == ("matched", "The Charisma Myth")


def test_sentence_case_ol_title_is_recased():
    o = ol.lookup("The Charisma Myth", "Olivia Fox Cabane", Stub("charisma-sentence-case.json"))
    assert o.title == "The Charisma Myth"


def test_decoys_are_skipped():
    o = ol.lookup("The Charisma Myth", "Olivia Fox Cabane", Stub("charisma-decoys.json"))
    assert (o.kind, o.title, o.author) == ("matched", "The Charisma Myth", "Olivia Fox Cabane")
    ol.clear_cache()
    assert ol.lookup("The Charisma Myth", "Olivia Fox Cabane",
                     Stub("decoys-only.json")).kind == "not_found"


def test_decoy_term_in_the_users_own_title_is_fine():
    doc = ol.Doc("The Artist's Way Workbook", ("Julia Cameron",))
    assert not ol.is_decoy(doc, "The Artist's Way Workbook")
    assert ol.is_decoy(doc, "The Artist's Way")


def test_no_hit():
    stub = Stub("empty.json")
    assert ol.lookup("Grit", "Angela Duckworth", stub).kind == "not_found"
    assert len(stub.calls) == 2  # title/author query, then the q= fallback


def test_author_mismatch_keeps_the_typed_author():
    o = ol.lookup("The Charisma Myth", "Olivia Fox Cabane", Stub("wrong-author.json"))
    assert o.kind == "author_mismatch"
    assert (o.ol_title, o.ol_authors) == ("The Charisma Myth", ["Someone Else"])


def test_title_only():
    o = ol.lookup("Essentialism", "", Stub("mckeown.json"))
    assert (o.kind, o.title, o.author) == ("matched", "Essentialism", "Greg McKeown")


def test_title_only_near_miss_is_not_accepted():
    assert ol.lookup("Influencer", "", Stub("influence.json")).kind == "not_found"
    assert ol.lookup("Influence", "", Stub("influence.json")).kind == "matched"


def test_title_only_typo_in_a_longer_title():
    o = ol.lookup("the charsima myth", "", Stub("charisma.json"))
    assert (o.kind, o.title, o.author) == ("matched", "The Charisma Myth", "Olivia Fox Cabane")


def test_multi_author():
    o = ol.lookup("Boundaries", "henry cloud and john townsend", Stub("boundaries.json"))
    assert (o.kind, o.author) == ("matched", "Henry Cloud and John Townsend")


def test_slug_mode():
    stub = Stub("charisma.json")
    o = ol.lookup("Charisma Myth Science Personal Magnetism", "", stub, slug_mode=True)
    assert (o.kind, o.title, o.author) == ("matched", "The Charisma Myth", "Olivia Fox Cabane")
    assert len(stub.calls) == 1 and "q=Charisma+Myth+Science" in stub.calls[0]


@pytest.mark.parametrize("stub", [
    Stub(raises=FetchError("timeout")),
    Stub(raises=FetchError("http-503")),
    Stub(raises=FetchError("too-large")),
    Stub(raises=FetchError("limit")),
    Stub(raises=RuntimeError("boom")),
    Stub("malformed.txt"),
])
def test_failures_are_unverified(stub):
    o = ol.lookup("The Charisma Myth", "Olivia Fox Cabane", stub)
    assert o.kind == "unverified" and o.title == "" and o.code


def test_weird_types_are_dropped():
    assert ol.parse_docs(body("weird-types.json")) == []
    assert ol.lookup("The Charisma Myth", "", Stub("weird-types.json")).kind == "not_found"


@pytest.mark.parametrize("raw", [b"[]", b'{"docs": 5}', b"\xff\xfe", b"", b"null"])
def test_parse_docs_rejects_bad_shapes(raw):
    with pytest.raises(ol.OLError):
        ol.parse_docs(raw)


def test_parse_docs_cleans_and_caps():
    raw = b'{"docs": [{"title": "The\\u202e Charisma\\nMyth", "author_name": ["A\\u200bB", 5],' \
          b' "first_publish_year": true}]}'
    (doc,) = ol.parse_docs(raw)
    assert doc == ol.Doc("The Charisma Myth", ("AB",), None)


def test_cache_hit_makes_no_fetch():
    stub = Stub("charisma.json")
    ol.lookup("The Charisma Myth", "Olivia Fox Cabane", stub)
    n = len(stub.calls)
    ol.lookup("The Charisma Myth", "Olivia Fox Cabane", stub)
    assert len(stub.calls) == n


def test_failures_are_not_cached():
    ol.lookup("Grit", "", Stub(raises=FetchError("timeout")))
    stub = Stub("empty.json")
    ol.lookup("Grit", "", stub)
    assert len(stub.calls) == 1


def test_query_encoding():
    want = ("https://openlibrary.org/search.json?title=Tom+%26+Jerry+%23+1%3F"
            "&limit=5&fields=title%2Cauthor_name%2Cfirst_publish_year")
    stub = Stub(by_url={want: "empty.json"})
    ol.lookup("Tom & Jerry # 1?", "", stub)
    assert stub.calls == [want]
