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


def _css_classes(css: str) -> set[str]:
    return set(re.findall(r"\.([a-zA-Z][\w-]*)", re.sub(r"/\*.*?\*/", "", css, flags=re.S)))


def test_every_submit_page_class_is_styled():
    css = _css_classes((STATIC_DIR / "style.css").read_text())
    page = (SUBMIT_ASSETS / "index.html").read_text()
    js = (SUBMIT_ASSETS / "submit.js").read_text()
    used = set()
    for attr in re.findall(r'class="([^"]*)"', page):
        used |= set(attr.split())
    # String literals passed to el(tag, cls, ...), className and classList.
    for lit in re.findall(r"el\('[a-z]+',\s*'([^']+)'", js):
        used |= set(lit.split())
    for lit in re.findall(r"classList\.(?:add|remove|toggle)\('([^']+)'", js):
        used.add(lit)
    # 'state-' + state builds a state class (state-queued is the plain pill).
    used = {c for c in used if not c.endswith("-")}
    missing = sorted(c for c in used if c not in css and c not in ("logo-light", "logo-dark",
                                                                   "icon-sun", "icon-moon"))
    assert not missing, missing
    assert {"check-preview", "change-item", "form-warn", "is-stale"} <= used


def test_check_preview_classes_are_in_the_showcase():
    showcase = (STATIC_DIR / "design-system.html").read_text()
    for cls in ("check-preview", "change-list", "change-item", "change-from", "change-to",
                "form-warn", "form-actions", "field-hint", "is-stale"):
        assert re.search(r'class="[^"]*\b' + cls + r'\b', showcase), cls


def test_submit_section_uses_tokens_only():
    css = (STATIC_DIR / "style.css").read_text()
    start = css.index("6b. Submit page")
    section = css[start:css.index("7. Motion", start)]
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b|rgba?\(|hsla?\(", section)
    for value in re.findall(r"border-radius:\s*([^;}]+)", section):
        assert "px" not in value and "var(--radius" in value, value


def test_submit_page_has_no_inline_handlers():
    page = (SUBMIT_ASSETS / "index.html").read_text()
    assert not re.search(r"\son[a-z]+\s*=", page, re.IGNORECASE)
