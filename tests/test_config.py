"""`add_book` appends in place and keeps books.yaml's comments."""

import pytest

from gttp.config import add_book, load_catalog

CATALOG = """\
defaults:
  min_score: 10  # keep low-signal threads out
books:
- title: Atomic Habits
  author: James Clear
- title: Boundaries
  author: Henry Cloud and John Townsend
  require_author: true  # "boundaries" alone matches generic advice posts
"""


@pytest.fixture
def books(tmp_path):
    p = tmp_path / "books.yaml"
    p.write_text(CATALOG)
    return p


def test_add_book_keeps_comments_and_appends(books):
    assert add_book("Deep Work", "Cal Newport", path=books) is True
    text = books.read_text()
    assert text.startswith(CATALOG)  # everything before is byte-identical
    assert "# keep low-signal threads out" in text
    assert '# "boundaries" alone matches generic advice posts' in text
    assert [b.title for b in load_catalog(books)] == ["Atomic Habits", "Boundaries", "Deep Work"]
    assert load_catalog(books)[-1].author == "Cal Newport"


def test_add_book_without_author(books):
    assert add_book("Grit", path=books) is True
    assert load_catalog(books)[-1].author is None


def test_add_book_round_trips_awkward_titles(books):
    title = 'Why: Not #1? "Quoted" and \'single\''
    assert add_book(title, "A. Person: Jr.", path=books) is True
    last = load_catalog(books)[-1]
    assert last.title == title and last.author == "A. Person: Jr."


def test_add_book_duplicate_leaves_file_byte_identical(books):
    before = books.read_bytes()
    assert add_book("atomic habits", "Someone", path=books) is False
    assert books.read_bytes() == before


def test_add_book_keeps_the_inode(books):
    ino = books.stat().st_ino
    add_book("Deep Work", "Cal Newport", path=books)
    assert books.stat().st_ino == ino


def test_add_book_handles_missing_trailing_newline(books):
    books.write_text(CATALOG.rstrip("\n"))
    assert add_book("Deep Work", path=books) is True
    assert [b.title for b in load_catalog(books)][-1] == "Deep Work"


def test_add_book_requires_books_to_be_last(books):
    books.write_text("books:\n- title: Grit\ndefaults:\n  min_score: 5\n")
    before = books.read_bytes()
    with pytest.raises(ValueError, match="books: must be the last key"):
        add_book("Deep Work", path=books)
    assert books.read_bytes() == before


def test_add_book_to_empty_file(tmp_path):
    p = tmp_path / "books.yaml"
    p.write_text("")
    assert add_book("Grit", path=p) is True
    assert [b.title for b in load_catalog(p)] == ["Grit"]
