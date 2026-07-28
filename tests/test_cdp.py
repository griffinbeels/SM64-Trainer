"""The CDP client, proved against the real app rather than a blank page."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from cdp import CHROME, chrome_session  # noqa: E402
from ui_fixture import serve_ui  # noqa: E402

pytestmark = pytest.mark.skipif(CHROME is None, reason="Chrome not installed")


@pytest.fixture(scope="module")
def page(tmp_path_factory):
    db = tmp_path_factory.mktemp("cdp") / "cdp.db"
    with serve_ui(db) as base, chrome_session(f"{base}/ui/index.html") as session:
        yield session


def test_session_evaluates_in_the_page(page):
    assert page.evaluate("1 + 1") == 2
    assert page.evaluate("document.title").strip() != ""


def test_the_app_actually_mounted(page):
    """A control assertion: everything below measures the real component tree,
    so prove there IS one before trusting a single number off it."""
    assert page.evaluate("document.querySelectorAll('.app-shell').length") == 1
    assert page.evaluate("document.querySelectorAll('.nav-item').length") > 0


def test_viewport_override_changes_the_reported_width(page):
    page.set_viewport(900, 1180)
    assert page.evaluate("window.innerWidth") == 900
    assert page.evaluate("window.innerHeight") == 1180
    page.set_viewport(1600, 900)
    assert page.evaluate("window.innerWidth") == 1600


def test_page_exceptions_are_collected(page):
    before = len(page.errors)
    page.evaluate("setTimeout(() => { throw new Error('probe-boom'); }, 0)")
    page.evaluate("new Promise(r => setTimeout(r, 250))")
    page.evaluate("1")          # one more round trip to drain the event queue
    assert len(page.errors) > before
    assert any("probe-boom" in e for e in page.errors)
