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
