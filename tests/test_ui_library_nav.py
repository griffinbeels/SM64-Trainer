"""The Library tab itself -- navigation, auto-open, and the refresh header.

Task 3, spec 2026-08-07-library-page. A render test, per this project's own
rule: unit tests plus `node --check` once shipped an invisible feature, and
every claim below is a fact about the real DOM a browser builds, not about
what the source says it should build.
"""
import shutil
import sys
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


@pytest.fixture(scope="module")
def practiced_page():
    """The default fixture's own seeding (star:2:4 attempts, seeded last --
    see ui_fixture.py's own comment on FIXTURE_STAR) is what auto-open reads.
    `arm_segment`/`seed_editor_fixtures` match PROJECT's own config so this
    measures the same shape of instance the responsive sweep does."""
    with serve_ui(arm_segment=FIXTURE_SEGMENT, seed_editor_fixtures=True) as base, \
            driver.get_driver().launch(headless=True) as page:
        page.goto(f"{base}/ui/index.html")
        page.wait_for(".log-list-card", timeout_ms=20000)
        yield page


@pytest.fixture(scope="module")
def empty_page():
    """No stage, no target, no attempts -- lastPracticed has nothing to
    resolve, so this is the fixture that reaches the plain course grid rather
    than auto-open racing it out of the way."""
    with serve_ui(seed=False) as base, \
            driver.get_driver().launch(headless=True) as page:
        page.goto(f"{base}/ui/index.html")
        page.wait_for(".log-list-card", timeout_ms=20000)
        yield page


def test_the_tab_is_reachable_from_the_sidebar(practiced_page):
    """It exists, sits in the Play group (task-3-caveats.md point 2 -- not
    inside the group already named "Library"), and switching to it actually
    changes the page rather than being a dead nav entry."""
    found = practiced_page.evaluate(
        "return !!document.querySelector('.nav-item[title=\"Library\"]')")
    assert found, "no nav item titled \"Library\" in the sidebar"
    practiced_page.evaluate(CLICK_LIBRARY_TAB)
    practiced_page.wait_for(".library-page", timeout_ms=15000)
    current = practiced_page.evaluate(
        "const el = document.querySelector('.nav-item[title=\"Library\"]');"
        "return el ? el.getAttribute('aria-current') : null")
    assert current == "page", (
        "the Library nav item does not report itself current after being "
        "clicked -- either app.js never wired \"Library\" into `tab`, or the "
        "component never mounted")


def test_auto_open_lands_on_the_last_practiced_target(practiced_page):
    """The headline behaviour: no click past the tab itself, and the page
    already names the target the player was just practicing."""
    practiced_page.evaluate(CLICK_LIBRARY_TAB)
    practiced_page.wait_for(".library-target", timeout_ms=15000)
    label = practiced_page.evaluate(
        "const el = document.querySelector('.library-target h3');"
        "return el ? el.textContent : null")
    assert label == "Fall onto the Caged Island", (
        f"the Library target page reads {label!r} -- expected the seeded "
        "star's own library label (star:2:4). Either lastPracticed resolved "
        "the wrong entity or /api/library/entity/star:2:4 stopped mapping to "
        "it (see tools/ui_fixture.py's own comment on FIXTURE_STAR).")


def test_the_course_grid_covers_every_numbered_course_and_the_movement_groups(empty_page):
    """>= 20: the 15 numbered courses, Castle Secret Stars, Bowser Courses,
    and the three Castle Movements groups -- 20 in the bundled snapshot
    today, and the sheet only grows (library.md's own numbers section), so
    this is a floor, never an exact count."""
    empty_page.evaluate(CLICK_LIBRARY_TAB)
    empty_page.wait_for(".library-courses", timeout_ms=15000)
    names = empty_page.evaluate(
        "return Array.from(document.querySelectorAll("
        "'.library-courses .entity-grid .starname')).map(el => el.textContent)")
    assert len(names) >= 20, (
        f"{len(names)} course cards in the Library's course grid, expected "
        f">= 20. Names seen: {names!r}")
    movement_groups = [name for name in names if "Castle Movements" in name]
    assert len(movement_groups) == 3, (
        f"expected the three Castle Movements groups (Lobby/Basement/"
        f"Upstairs), found {movement_groups!r} among {names!r}")


def test_a_movement_group_s_targets_are_marked_browse_only(empty_page):
    """caveat 5: a Castle Movement's entity_key is null -- it is shown, never
    silently dropped, and marked so a click on it does not read as a real
    practiceable target."""
    empty_page.evaluate(CLICK_LIBRARY_TAB)
    empty_page.wait_for(".library-courses", timeout_ms=15000)
    opened = empty_page.evaluate("""
        (() => {
          const cells = Array.from(document.querySelectorAll(
            '.library-courses .entity-grid .starcell'));
          const lobby = cells.find(
            (cell) => cell.querySelector('.starname')?.textContent
              === 'Castle Movements (Lobby)');
          if (!lobby) return 'no Castle Movements (Lobby) group cell found';
          lobby.click();
          return 'clicked';
        })()
    """)
    assert opened == "clicked", opened
    empty_page.wait_for(".library-group", timeout_ms=15000)
    chip_count = empty_page.evaluate(
        "return document.querySelectorAll("
        "'.library-group .entity-grid .chip').length")
    assert chip_count > 0, (
        "no browse-only chip inside the Castle Movements (Lobby) group -- "
        "every target in it should carry one (miss_reason: castle_movement)")


def test_the_refresh_header_is_present(practiced_page):
    """Title, the human-time status line, and the Refresh control itself.

    ROUND 1 (2026-08-07): the status line speaks human time now, never a raw
    revision stamp — his words: "The text here for Sheet 2026-blah blah is
    unsettling. I think we should just say 'Last refreshed: [time] ago'."
    A locally-refreshed copy reads "Last refreshed: …"; the bundled seed
    reads "Bundled with the app …". A raw ISO stamp in this line is the
    regression this now guards against."""
    practiced_page.evaluate(CLICK_LIBRARY_TAB)
    practiced_page.wait_for(".library-page", timeout_ms=15000)
    has_refresh = practiced_page.evaluate(
        "return Array.from(document.querySelectorAll('.workshop-hero button'))"
        ".some(btn => btn.textContent.includes('Refresh'))")
    assert has_refresh, "no button reading \"Refresh\" in the Library header"
    revision_text = practiced_page.evaluate(
        "const el = document.querySelector('.workshop-title p');"
        "return el ? el.textContent : null")
    assert revision_text and (
        revision_text.startswith("Last refreshed:")
        or revision_text.startswith("Refreshed by you")
        or revision_text.startswith("Bundled with the app")), (
        f"the Library header's status line reads {revision_text!r} -- "
        "expected the round-1 human-time copy (library.js::statusLine)")
    assert "20" not in revision_text.split(":")[0], (
        f"a raw timestamp leaked back into the status line: {revision_text!r}")
