"""The Library's comparison tray and its grid overlay (librarytray.js).

Task 5, spec 2026-08-07-library-page. A render test, per this project's own
rule: unit tests plus `node --check` once shipped an invisible feature.

Fixture: same as test_ui_library_target.py -- `ui_fixture.py`'s default
seeding (star:2:4, "Fall onto the Caged Island") auto-opens a section with
several approaches, each carrying example cards with an enabled "+"."""
import shutil
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not on PATH")

from find_uilab import find_uilab  # noqa: E402

_MISSING = find_uilab()
if _MISSING:
    pytest.skip(_MISSING, allow_module_level=True)

from ui_fixture import FIXTURE_SEGMENT, serve_ui  # noqa: E402
from uilab import driver  # noqa: E402

CLICK_LIBRARY_TAB = 'document.querySelector(\'.nav-item[title="Library"]\').click()'

# Adds up to `n` examples via the open section's own enabled "+" buttons and
# returns how many it actually clicked -- a count short of `n` means the
# fixture's open section does not carry enough video-bearing examples, which
# is itself worth failing loudly on rather than silently asserting on fewer.
_ADD_N_EXAMPLES = """
(() => {{
  const cards = Array.from(document.querySelectorAll(
    '.library-section.open .library-example'));
  let added = 0;
  for (const card of cards) {{
    if (added >= {n}) break;
    const btn = card.querySelector('.library-example-plus');
    if (btn && !btn.disabled) {{ btn.click(); added++; }}
  }}
  return added;
}})()
"""


@pytest.fixture(scope="module")
def library_server():
    with serve_ui(arm_segment=FIXTURE_SEGMENT, seed_editor_fixtures=True) as base:
        yield base


@pytest.fixture
def library_page(library_server):
    """A FRESH page per test -- same reason test_ui_library_target.py gives:
    tray state (and an open grid overlay) must not leak between tests."""
    with driver.get_driver().launch(headless=True) as page:
        page.goto(f"{library_server}/ui/index.html")
        page.wait_for(".log-list-card", timeout_ms=20000)
        page.evaluate(CLICK_LIBRARY_TAB)
        page.wait_for(".library-target .library-section", timeout_ms=15000)
        yield page


def test_adding_examples_docks_the_tray_with_one_chip_each(library_page):
    added = library_page.evaluate(_ADD_N_EXAMPLES.format(n=3))
    assert added == 3, f"only {added} example cards had an enabled + button"
    library_page.wait_for(".library-tray-chip", timeout_ms=5000)
    assert library_page.evaluate(
        "document.querySelectorAll('.library-tray-chip').length") == 3


def test_play_all_opens_a_grid_shaped_by_gridshape_of_the_tray_count(library_page):
    added = library_page.evaluate(_ADD_N_EXAMPLES.format(n=3))
    assert added == 3, added
    library_page.wait_for(".library-tray-chip", timeout_ms=5000)

    library_page.evaluate("document.querySelector('.library-tray-playall').click()")
    library_page.wait_for(".library-grid iframe", timeout_ms=5000)

    assert library_page.evaluate(
        "document.querySelectorAll('.library-grid iframe').length") == 3
    # gridShape(3) -> {rows: 2, cols: 2} (librarymodel.js / test_library_model_js.py)
    cols = library_page.evaluate(
        "getComputedStyle(document.querySelector('.library-grid'))"
        ".gridTemplateColumns.trim().split(/\\s+/).length")
    assert cols == 2, cols


def test_the_overlay_carries_the_honesty_line_verbatim(library_page):
    added = library_page.evaluate(_ADD_N_EXAMPLES.format(n=1))
    assert added == 1, added
    library_page.evaluate("document.querySelector('.library-tray-playall').click()")
    library_page.wait_for(".library-grid-honesty", timeout_ms=5000)
    text = library_page.evaluate(
        "document.querySelector('.library-grid-honesty').textContent")
    assert text == ("Embeds play roughly together — for frame-accurate "
                     "sync, use Study in Compare."), text


def test_setting_a_trim_start_and_restarting_updates_the_first_iframe_src(library_page):
    added = library_page.evaluate(_ADD_N_EXAMPLES.format(n=2))
    assert added == 2, added
    library_page.wait_for(".library-tray-chip", timeout_ms=5000)

    library_page.evaluate("document.querySelector('.library-tray-playall').click()")
    library_page.wait_for(".library-grid iframe", timeout_ms=5000)
    before = library_page.evaluate(
        "document.querySelectorAll('.library-grid iframe')[0].src")
    assert "start=" in before, before

    # The trim editor lives on the TRAY chip, not inside the (now open) grid
    # overlay -- `.click()`/setting `.value` are synthetic DOM calls, so the
    # overlay sitting visually on top does not block them.
    library_page.evaluate(
        "document.querySelector('.library-tray-chip-trim').click()")
    library_page.wait_for(".library-tray-trim-start", timeout_ms=5000)
    library_page.evaluate("""
      (() => {
        const input = document.querySelector('.library-tray-trim-start');
        input.value = '12';
        input.dispatchEvent(new Event('input', {bubbles: true}));
      })()
    """)

    library_page.evaluate("document.querySelector('.library-grid-restart').click()")
    time.sleep(0.2)   # a fresh <iframe> mounts on the next commit
    after = library_page.evaluate(
        "document.querySelectorAll('.library-grid iframe')[0].src")
    assert "start=12" in after, (before, after)


_ENABLED_PLUS_COUNT = (
    "document.querySelectorAll("
    "'.library-section.open .library-example-plus:not(:disabled)').length")


def test_removing_a_chip_shrinks_the_tray_and_re_enables_its_plus_button(library_page):
    # Counts, not "the first + button": several cards can share one video
    # (librarytarget.js disables every card whose `entry.video` is already in
    # the tray, and some cards start disabled for the unrelated reason of
    # carrying no video at all -- library.md's "not every video link
    # resolves"), so a query for THE first `.library-example-plus` can land
    # on an always-disabled card that this test never touched.
    enabled_before = library_page.evaluate(_ENABLED_PLUS_COUNT)

    added = library_page.evaluate(_ADD_N_EXAMPLES.format(n=1))
    assert added == 1, added
    library_page.wait_for(".library-tray-chip", timeout_ms=5000)
    enabled_after_add = library_page.evaluate(_ENABLED_PLUS_COUNT)
    assert enabled_after_add < enabled_before, (enabled_before, enabled_after_add)

    library_page.evaluate(
        "document.querySelector('.library-tray-chip-remove').click()")
    # No "wait for absence" primitive on this driver -- poll instead, the
    # same idiom test_ui_library_target.py's own scroll assertion uses.
    for _ in range(50):
        if library_page.evaluate(
                "document.querySelectorAll('.library-tray-chip').length") == 0:
            break
        time.sleep(0.05)
    assert library_page.evaluate(
        "document.querySelectorAll('.library-tray-chip').length") == 0
    assert library_page.evaluate(_ENABLED_PLUS_COUNT) == enabled_before
