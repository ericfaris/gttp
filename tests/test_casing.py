"""Deterministic title/author casing (gttp.casing)."""

import pytest

from gttp.casing import (
    apply_casing,
    author_case,
    clean_candidate,
    join_authors,
    split_authors,
    title_case,
)
from gttp.config import load_catalog

TITLES = [
    ("the charisma myth", "The Charisma Myth"),
    ("The charisma myth", "The Charisma Myth"),
    ("THE CHARISMA MYTH", "The Charisma Myth"),
    ("the 7 habits of highly effective people", "The 7 Habits of Highly Effective People"),
    ("ego is the enemy", "Ego Is the Enemy"),
    ("the courage to be disliked", "The Courage to Be Disliked"),
    ("thinking, fast and slow", "Thinking, Fast and Slow"),
    ("no more mr. nice guy", "No More Mr. Nice Guy"),
    ("sapiens: a brief history of humankind", "Sapiens: A Brief History of Humankind"),
    ("in the end", "In the End"),
    ("what to look for", "What to Look For"),
    ("the 4-hour workweek", "The 4-Hour Workweek"),
    ("self-compassion", "Self-Compassion"),
    ("out-of-body travel", "Out-of-Body Travel"),
    ("who moved my cheese?", "Who Moved My Cheese?"),
    ("eat that frog!", "Eat That Frog!"),
    ("rocky iv", "Rocky IV"),
    ("the mix", "The Mix"),
    ("NASA and the moon", "NASA and the Moon"),
    ("iPhone for dummies", "iPhone for Dummies"),
    ("what got you here won't get you there", "What Got You Here Won't Get You There"),
    ("  the   charisma  myth ", "The Charisma Myth"),
    ("why: the answer", "Why: The Answer"),
]

AUTHORS = [
    ("olivia fox cabane", "Olivia Fox Cabane"),
    ("Olivia fox Cabane", "Olivia Fox Cabane"),
    ("greg mckeown", "Greg McKeown"),
    ("Greg McKeown", "Greg McKeown"),
    ("anne-laure le cunff", "Anne-Laure Le Cunff"),
    ("Anne-Laure Le Cunff", "Anne-Laure Le Cunff"),
    ("j.k. rowling", "J. K. Rowling"),
    ("j. k. rowling", "J. K. Rowling"),
    ("J.K. Rowling", "J. K. Rowling"),
    ("ursula k. le guin", "Ursula K. Le Guin"),
    ("ludwig van beethoven", "Ludwig van Beethoven"),
    ("Ludwig van Beethoven", "Ludwig van Beethoven"),
    ("henry cloud & john townsend", "Henry Cloud and John Townsend"),
    ("henry cloud, john townsend", "Henry Cloud and John Townsend"),
    ("a b, c d and e f", "A B, C D and E F"),
    ("MARCUS AURELIUS", "Marcus Aurelius"),
    ("flannery o'connor", "Flannery O'Connor"),
    ("stephen r. covey", "Stephen R. Covey"),
    ("brené brown", "Brené Brown"),
    ("Fox Cabane, Olivia", "Fox Cabane, Olivia"),
    ("JK Rowling", "JK Rowling"),
]


@pytest.mark.parametrize("raw, want", TITLES)
def test_title_case(raw, want):
    assert title_case(raw) == want


@pytest.mark.parametrize("raw, want", AUTHORS)
def test_author_case(raw, want):
    assert author_case(raw) == want


@pytest.mark.parametrize("raw, _", TITLES)
def test_title_case_is_idempotent(raw, _):
    once = title_case(raw)
    assert title_case(once) == once


@pytest.mark.parametrize("raw, _", AUTHORS)
def test_author_case_is_idempotent(raw, _):
    once = author_case(raw)
    assert author_case(once) == once


def test_catalog_is_a_fixed_point():
    """Regression guard: casing never "fixes" a title or author that is
    already in the catalog."""
    for book in load_catalog():
        assert title_case(book.title) == book.title
        if book.author:
            assert author_case(book.author) == book.author


def test_split_and_join_authors():
    assert split_authors("Henry Cloud and John Townsend") == ["Henry Cloud", "John Townsend"]
    assert split_authors("A B, C D, and E F") == ["A B", "C D", "E F"]
    assert split_authors("Fox Cabane, Olivia") == ["Fox Cabane, Olivia"]
    assert join_authors(["A"]) == "A"
    assert join_authors(["A", "B"]) == "A and B"
    assert join_authors(["A", "B", "C"]) == "A, B and C"
    assert join_authors([]) == ""


def test_clean_candidate():
    assert clean_candidate("a​b‮c") == "abc"
    assert clean_candidate("a\nb\tc\x00d e") == "a b cd e"
    assert clean_candidate("  lots   of   space  ") == "lots of space"
    assert clean_candidate("Tom &amp; Jerry") == "Tom & Jerry"
    assert clean_candidate("“Quoted”") == "Quoted"
    assert clean_candidate('"Quoted"') == "Quoted"
    assert clean_candidate("x" * 300) == "x" * 300
    assert clean_candidate("x" * 301) == ""
    assert clean_candidate(5) == ""


def test_apply_casing():
    assert apply_casing("title", "The Charisma Myth") == ("The Charisma Myth", None)
    new, change = apply_casing("author", "olivia fox cabane")
    assert new == "Olivia Fox Cabane"
    assert change == {"field": "author", "from": "olivia fox cabane",
                      "to": "Olivia Fox Cabane", "reason": "capitalization"}
