"""Guards on static/style.css that the Python tests can check without a browser."""

import re

from gttp.config import STATIC_DIR


def test_decorative_glows_never_intercept_clicks():
    """The ::before glows extend well past their element (the cover glow sits
    over the book page's back link), so each must be click-through."""
    css = (STATIC_DIR / "style.css").read_text()
    for selector in (".today::before", ".cover-glow::before"):
        m = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", css)
        assert m, selector
        assert "pointer-events: none" in m.group(1), selector


SUBMIT_ASSETS = STATIC_DIR.parent / "src" / "gttp" / "submit_assets"


def test_submit_page_has_no_inline_script_style_or_colors():
    """The submit page's CSP allows no inline script or style, and the design
    system allows no literal colors outside the token blocks."""
    page = (SUBMIT_ASSETS / "index.html").read_text()
    scripts = re.findall(r"<script\b([^>]*)>", page)
    assert scripts and all("src=" in attrs for attrs in scripts)
    assert "style=" not in page and "<style" not in page
    # theme-color meta is the browser chrome color, mirrored from the public pages.
    body = page.replace('content="#14213D"', "")
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", body)


def test_submit_js_never_uses_inner_html():
    js = (SUBMIT_ASSETS / "submit.js").read_text()
    assert "innerHTML" not in js and "outerHTML" not in js and "insertAdjacentHTML" not in js


def test_submit_ui_never_reaches_the_public_site():
    """write_site copies everything in static/ to the public site/."""
    assert not [p for p in STATIC_DIR.rglob("submit*")]


def test_error_tokens_in_every_theme_block():
    css = (STATIC_DIR / "style.css").read_text()
    for token in ("--color-error:", "--color-error-tint:"):
        assert css.count(token) == 3, token
