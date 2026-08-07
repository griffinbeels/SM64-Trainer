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


def test_study_in_compare_is_enabled_once_the_tray_has_an_item(library_page):
    """TASK 6 supersedes FIX ROUND 1's test of the same name. `onStudy` is
    wired for real now (library.js's `studyInCompare`), and the always-
    visible "coming soon" note this test used to pin is gone with it --
    there is nothing left to explain once the control can act. What
    survives is the PRINCIPLE the note existed to satisfy (acceptance.md: a
    disabled control must explain itself where the click lands), now checked
    from the other side: a control that CAN act must not still claim it
    can't, which is exactly the bug a stale disabled-by-default would be."""
    added = library_page.evaluate(_ADD_N_EXAMPLES.format(n=1))
    assert added == 1, added
    library_page.wait_for(".library-tray-chip", timeout_ms=5000)
    result = library_page.evaluate("""
      (() => {
        const btn = document.querySelector('.library-tray-study');
        const note = document.querySelector('.library-tray-study-note');
        return {
          disabled: btn ? btn.disabled : null,
          label: btn ? btn.textContent.trim() : null,
          noteText: note ? note.textContent.trim() : null,
        };
      })()
    """)
    assert result["disabled"] is False, result
    assert result["label"] == "Study in Compare", result
    assert result["noteText"] is None, "no reason should render once the button can act"


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
    # Counts, not "the first + button": librarytarget.js disables every card
    # whose own tray key is already in the tray, and some cards start
    # disabled for the unrelated reason of carrying no video at all --
    # library.md's "not every video link resolves" -- so a query for THE
    # first `.library-example-plus` can land on an always-disabled card this
    # test never touched.
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


# ---- fix round 1 (controller finding 1): tray key collision across sibling
# entities sharing one recording ------------------------------------------

def test_a_video_shared_across_sibling_entities_does_not_collide_in_the_tray(library_page):
    """FIX ROUND 1. Measured against the real bundled snapshot: JoSniffy's
    youtu.be/ANqWo4v9qfc evidences BOTH star:2:4 "Fall onto the Caged Island"
    (this fixture's auto-opened target) and star:2:5 "Blast Away the Wall",
    sibling stars under the "2. Whomp's Fortress" course group -- one
    recording standing as evidence for two different stars, which is
    ordinary in this corpus (8 videos do this on the real snapshot; 605 are
    cited at more than one time). Before this fix the tray keyed an item by
    `entry.video` alone, so adding star:2:4's entry silently disabled
    star:2:5's OWN entry for the same runner -- a different star, refused
    with no way to add it."""
    added_first = library_page.evaluate("""
      (() => {
        // Scoped to `.library-target`, not a bare query: the tray persists
        // across navigation and its OWN chip thumb can share this src once
        // the first add has happened, and it sits earlier in DOM order.
        const img = document.querySelector('.library-target img[src*="ANqWo4v9qfc"]');
        if (!img) return 'no thumb for the shared video on star:2:4';
        const card = img.closest('.library-example');
        const btn = card.querySelector('.library-example-plus');
        if (!btn || btn.disabled) return 'plus button missing or disabled on star:2:4';
        btn.click();
        return 'clicked';
      })()
    """)
    assert added_first == "clicked", added_first
    library_page.wait_for(".library-tray-chip", timeout_ms=5000)
    assert library_page.evaluate(
        "document.querySelectorAll('.library-tray-chip').length") == 1

    # Back to the course grid, into "2. Whomp's Fortress", onto the sibling
    # star -- confirms the fixture still reaches the real pair before
    # trusting anything that follows.
    library_page.evaluate("document.querySelector('.entity-back').click()")
    library_page.wait_for(".library-courses", timeout_ms=15000)
    opened_group = library_page.evaluate("""
      (() => {
        const cell = Array.from(document.querySelectorAll('.library-courses .starcell'))
          .find((c) => c.querySelector('.starname')?.textContent === "2. Whomp's Fortress");
        if (!cell) return "no Whomp's Fortress group cell";
        cell.click();
        return 'clicked';
      })()
    """)
    assert opened_group == "clicked", opened_group
    library_page.wait_for(".library-group", timeout_ms=15000)
    picked_target = library_page.evaluate("""
      (() => {
        const cell = Array.from(document.querySelectorAll('.library-group .starcell'))
          .find((c) => c.querySelector('.starname')?.textContent === 'Blast Away the Wall');
        if (!cell) return 'no Blast Away the Wall cell';
        cell.click();
        return 'clicked';
      })()
    """)
    assert picked_target == "clicked", picked_target
    # Waits for the auto-expanded section's DISCLOSED body, not just the
    # section header -- `Disclose` mounts its contents a tick after `open`
    # flips (collapsible.js's own docstring), so a bare `.library-section`
    # wait can win a race against an empty body and read as "no thumb"
    # rather than as the disabled-button signal this test is actually after.
    library_page.wait_for(".library-section.open .library-example", timeout_ms=10000)

    # The sibling star's OWN entry for the same video must not read as
    # already added.
    added_second = library_page.evaluate("""
      (() => {
        // Scoped to `.library-target`, not a bare query: the tray persists
        // across navigation and its OWN chip thumb can share this src once
        // the first add has happened, and it sits earlier in DOM order.
        const img = document.querySelector('.library-target img[src*="ANqWo4v9qfc"]');
        if (!img) return 'no thumb for the shared video on star:2:5';
        const card = img.closest('.library-example');
        const btn = card.querySelector('.library-example-plus');
        if (!btn) return 'no plus button on star:2:5';
        if (btn.disabled) return 'disabled -- the collision is back';
        btn.click();
        return 'clicked';
      })()
    """)
    assert added_second == "clicked", added_second
    library_page.wait_for(".library-tray-chip:nth-child(2)", timeout_ms=5000)
    assert library_page.evaluate(
        "document.querySelectorAll('.library-tray-chip').length") == 2


# ---- fix round 2 (controller finding, self-measured): the one remaining
# collision after approach+runner+time -------------------------------------

def test_two_different_recordings_of_the_same_run_do_not_collide_in_the_tray(library_page):
    """FIX ROUND 2. Measured directly against the real bundled snapshot
    (every video-bearing entry reachable from this page -- approaches only,
    since subsections never render here): after fix round 1's
    approach+runner+time key, exactly ONE real collision survived --
    star:16:0 "Xiah cycle pipe entry" (target "Bowser in the Dark World Red
    Coins", group "Bowser Courses"), Benji, both entries at time_cs 5023, but
    TWO DIFFERENT recordings of the same trick (a JP-version upload,
    B9wXEVjv1WU, and a US-version upload, U42IDMKO180). `entryTrayKey` now
    appends the video as a third field."""
    library_page.evaluate("document.querySelector('.entity-back').click()")
    library_page.wait_for(".library-courses", timeout_ms=15000)
    opened_group = library_page.evaluate("""
      (() => {
        const cell = Array.from(document.querySelectorAll('.library-courses .starcell'))
          .find((c) => c.querySelector('.starname')?.textContent === 'Bowser Courses');
        if (!cell) return 'no Bowser Courses group cell';
        cell.click();
        return 'clicked';
      })()
    """)
    assert opened_group == "clicked", opened_group
    library_page.wait_for(".library-group", timeout_ms=15000)
    picked_target = library_page.evaluate("""
      (() => {
        const cell = Array.from(document.querySelectorAll('.library-group .starcell'))
          .find((c) => c.querySelector('.starname')?.textContent
            === 'Bowser in the Dark World Red Coins');
        if (!cell) return 'no "Bowser in the Dark World Red Coins" cell';
        cell.click();
        return 'clicked';
      })()
    """)
    assert picked_target == "clicked", picked_target
    library_page.wait_for(".library-target .library-section.open", timeout_ms=15000)
    # This target's several sections are not auto-expanded to "Xiah cycle
    # pipe entry" by default -- open it explicitly. Auto-expand's own effect
    # (LibraryTarget's one-shot `useEffect`) fires ASYNCHRONOUSLY after the
    # section headers first render, so a click issued before it settles gets
    # silently overwritten the instant it does -- caught by this exact test
    # flaking on its own MUTATION run (`.library-section.open` sometimes
    # named the target's own first approach instead), never by a plain run,
    # since a plain run doesn't care which section is open. Waiting for the
    # auto-expanded `.open` class to already exist, above, is what orders
    # this click strictly AFTER that effect.
    opened_section = library_page.evaluate("""
      (() => {
        const heads = Array.from(document.querySelectorAll('.library-section-head'));
        const head = heads.find((h) =>
          h.querySelector('.library-section-name').textContent === 'Xiah cycle pipe entry');
        if (!head) return 'no "Xiah cycle pipe entry" section';
        head.click();
        return 'clicked';
      })()
    """)
    assert opened_section == "clicked", opened_section
    library_page.wait_for(".library-section.open .library-example", timeout_ms=10000)

    def benji_cards_script(index, extra):
        return f"""
          (() => {{
            const cards = Array.from(document.querySelectorAll(
              '.library-section.open .library-example'))
              .filter((c) => c.querySelector('.library-example-runner')
                ?.textContent === 'Benji');
            if (cards.length !== 2) return `expected 2 Benji cards, found ${{cards.length}}`;
            {extra}
          }})()
        """

    add_first = library_page.evaluate(benji_cards_script(0, """
            const btn = cards[0].querySelector('.library-example-plus');
            if (!btn || btn.disabled) return 'first Benji card has no enabled + button';
            btn.click();
            return 'clicked';
    """))
    assert add_first == "clicked", add_first
    library_page.wait_for(".library-tray-chip", timeout_ms=5000)
    assert library_page.evaluate(
        "document.querySelectorAll('.library-tray-chip').length") == 1

    # The causal check: adding the FIRST recording must not disable the
    # second -- a fresh evaluate() call, after Preact has had a tick to
    # commit the first add (ui-core.md: reading in the same tick as the
    # dispatch sees the PRE-render value).
    add_second = library_page.evaluate(benji_cards_script(1, """
            const btn = cards[1].querySelector('.library-example-plus');
            if (!btn) return 'no plus button on the second Benji card';
            if (btn.disabled) return 'disabled -- the collision is back';
            btn.click();
            return 'clicked';
    """))
    assert add_second == "clicked", add_second
    library_page.wait_for(".library-tray-chip:nth-child(2)", timeout_ms=5000)
    assert library_page.evaluate(
        "document.querySelectorAll('.library-tray-chip').length") == 2
