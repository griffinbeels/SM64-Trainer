"""The Library's progression-first target page (librarytarget.js).

Task 4, spec 2026-08-07-library-page. A render test, per this project's own
rule: unit tests plus `node --check` once shipped an invisible feature, and
every claim below is a fact about the real DOM a browser builds.

Fixture: `ui_fixture.py`'s default seeding (star:2:4, "Fall onto the Caged
Island") is a real bundled-sheet target with four approaches, all four
carrying `matched_strategy`, one carrying `ladder_jp` (caveat 5 -- no extra
seeding needed to exercise the JP toggle), and the fixture's own active
strategy (`FIXTURE_STRAT = "TJ Owlless"`) matches the entity's SECOND section
by Mario cutoff rather than its first -- so "auto-expand follows the active
strategy" is a real assertion here, not a tautology that would also pass for
"opens the first section"."""
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
def library_server():
    with serve_ui(arm_segment=FIXTURE_SEGMENT, seed_editor_fixtures=True) as base:
        yield base


@pytest.fixture
def library_page(library_server):
    """A FRESH page per test. Sharing one across the module let a typed
    search query (or a JP toggle, or a clicked-open section) leak into
    whatever ran after it -- the exact flakiness
    tests/test_ui_hundred_coin_render.py already paid for once, same fix
    here. Booting the server is the expensive half and stays module-scoped;
    auto-open lands directly on star:2:4's target page (lastPracticed reads
    the fixture's own seeded attempts)."""
    with driver.get_driver().launch(headless=True) as page:
        page.goto(f"{library_server}/ui/index.html")
        page.wait_for(".log-list-card", timeout_ms=20000)
        page.evaluate(CLICK_LIBRARY_TAB)
        page.wait_for(".library-target .library-section", timeout_ms=15000)
        yield page


# ---- the three tests the plan's own contract specifies verbatim ----------

def test_sections_run_beginner_to_expert_and_bands_slowest_first(library_page):
    order = library_page.evaluate(
        "Array.from(document.querySelectorAll('.library-section-name'))"
        ".map(e => e.textContent)")
    marios = library_page.evaluate(
        "Array.from(document.querySelectorAll('.library-section'))"
        ".map(e => +e.dataset.mario)")
    assert marios == sorted(marios, reverse=True), (order, marios)
    tiers = library_page.evaluate(
        "Array.from(document.querySelectorAll("
        "'.library-section.open .library-band')).map(e => e.dataset.tier)")
    assert tiers.index("Bronze") < tiers.index("Mario"), tiers


def test_exactly_one_section_opens_and_it_is_the_first_without_a_selection(library_page):
    assert library_page.evaluate(
        "document.querySelectorAll('.library-section.open').length") == 1


def test_search_filters_across_sections(library_page):
    library_page.evaluate("document.querySelector('.library-search').value = 'zzz-nobody'")
    library_page.evaluate(
        "document.querySelector('.library-search')"
        ".dispatchEvent(new Event('input', {bubbles: true}))")
    assert library_page.evaluate(
        "document.querySelectorAll('.library-example:not(.hidden)').length") == 0


# ---- supplementary coverage for what the three tests above cannot see ----

def test_auto_open_follows_the_active_strategy_not_the_first_section(library_page):
    """The fixture's active strategy on star:2:4 is "TJ Owlless"
    (ui_fixture.py::FIXTURE_STRAT), matched to "Fall onto the Caged Island" --
    the SECOND section by Mario cutoff (Owl strat's 15.83s sorts first). If
    autoExpandName had silently degraded to "always the first section" this
    would still pass the plan's own count==1 test, so this is the assertion
    that actually distinguishes the two behaviours."""
    opened = library_page.evaluate(
        "document.querySelector('.library-section.open .library-section-name')"
        ".textContent")
    assert opened == "Fall onto the Caged Island", opened


def test_matched_strategy_chip_and_your_standing_render(library_page):
    chip = library_page.evaluate(
        "const el = document.querySelector('.library-section.open .library-matched-chip');"
        "return el ? el.textContent : null")
    assert chip and "TJ Owlless" in chip, chip
    standing = library_page.evaluate(
        "const el = document.querySelector('.library-section.open .library-your-standing');"
        "return el ? el.textContent.trim() : null")
    assert standing, "no your-rank/PB line on the section matching the active strategy"


def test_jp_toggle_switches_the_ladder_and_the_band_cutoffs(library_page):
    """"Owl strat w/o speed preservation" is the one approach on this star
    carrying `ladder_jp` (US Mario 15.83s, JP 16.00s -- genuinely different
    values, not a toggle that redraws the same numbers). Opening it, reading
    the TOC's Mario row, toggling, and reading again proves the toggle
    recomputes `bandsOf` against a DIFFERENT ladder rather than merely
    flipping a label."""
    opened = library_page.evaluate("""
      (() => {
        const heads = Array.from(document.querySelectorAll('.library-section-head'));
        const head = heads.find((b) =>
          b.querySelector('.library-section-name').textContent
            === 'Owl strat w/o speed preservation');
        if (!head) return false;
        head.click();
        return true;
      })()
    """)
    assert opened, "could not find the Owl strat section to open"
    library_page.wait_for(".library-section.open .library-jp-toggle", timeout_ms=10000)

    def mario_cutoff():
        return library_page.evaluate("""
          (() => {
            const rows = Array.from(document.querySelectorAll(
              '.library-section.open .library-toc-row'));
            const row = rows.find((r) =>
              r.querySelector('.library-toc-tier').textContent.trim().startsWith('Mario'));
            return row ? row.querySelector('.library-toc-cutoff').textContent.trim() : null;
          })()
        """)

    before = mario_cutoff()
    assert before, "no Mario TOC row found on the JP-carrying section"
    library_page.evaluate(
        "document.querySelector('.library-section.open .library-jp-toggle').click()")
    after = mario_cutoff()
    assert after and after != before, (
        f"JP toggle did not change the Mario cutoff: {before!r} -> {after!r}")


def test_band_anchors_are_unique_and_every_toc_row_resolves_one(library_page):
    result = library_page.evaluate("""
      (() => {
        const open = document.querySelector('.library-section.open');
        const bandIds = Array.from(open.querySelectorAll('.library-band')).map((b) => b.id);
        const tocRows = open.querySelectorAll('.library-toc-row').length;
        return {bandIds, tocRows,
                allResolve: bandIds.every((id) => id && !!document.getElementById(id))};
      })()
    """)
    assert result["bandIds"], "no band anchors on the open section"
    assert len(result["bandIds"]) == len(set(result["bandIds"])), (
        f"duplicate band anchor ids: {result['bandIds']}")
    assert result["tocRows"] == len(result["bandIds"]), result
    assert result["allResolve"], result["bandIds"]


def test_clicking_a_toc_row_scrolls_to_its_band(library_page):
    before = library_page.evaluate("window.scrollY")
    library_page.evaluate("""
      (() => {
        const rows = document.querySelectorAll('.library-section.open .library-toc-row');
        rows[rows.length - 1].click();   // the fastest tier's row -- its band sits lowest
      })()
    """)
    import time
    time.sleep(0.4)   # smooth scrollIntoView plays out over a few frames
    after = library_page.evaluate("window.scrollY")
    assert after > before, (before, after)


def test_plus_adds_to_the_tray_and_then_disables(library_page):
    result = library_page.evaluate("""
      (() => {
        const cards = Array.from(document.querySelectorAll(
          '.library-section.open .library-example'));
        const card = cards.find((c) => {
          const btn = c.querySelector('.library-example-plus');
          return btn && !btn.disabled;
        });
        if (!card) return {found: false};
        const before = card.querySelector('.library-example-plus').disabled;
        card.querySelector('.library-example-plus').click();
        return {found: true, before};
      })()
    """)
    assert result["found"], "no example card with an enabled + button was found"
    assert result["before"] is False
    library_page.wait_for(
        ".library-section.open .library-example-plus:disabled", timeout_ms=5000)


# ---- fix round 1 (controller finding 1): section/anchor identity collision -

def test_sibling_targets_sharing_an_unmatched_approach_name_open_independently(library_page):
    """FIX ROUND 1. CCM's 100-coin star (star:4:6) maps to FOUR sheet
    targets, and two of them (indices 23 and 24 in the bundled snapshot) each
    carry an approach literally named "100 coin star Xcam" with no
    `matched_strategy` -- an UNMATCHED-name collision, confirmed against the
    real snapshot to be the MORE common half (6 entities share it, every
    100-coin star with more than one sheet target), not the rarer matched-
    strategy case this file originally reported as the only kind. Before
    target-index disambiguation, both sections shared one identity
    (`matched_strategy || name`), so `open === expanded` was satisfied for
    BOTH the instant either was clicked -- opening one silently opened both.
    """
    # Back out of the auto-opened star:2:4 page, into the course grid.
    library_page.evaluate("document.querySelector('.entity-back').click()")
    library_page.wait_for(".library-courses", timeout_ms=15000)
    opened_group = library_page.evaluate("""
      (() => {
        const cell = Array.from(document.querySelectorAll('.library-courses .starcell'))
          .find((c) => c.querySelector('.starname')?.textContent === '4. Cool, Cool Mountain');
        if (!cell) return 'no CCM group cell';
        cell.click();
        return 'clicked';
      })()
    """)
    assert opened_group == "clicked", opened_group
    library_page.wait_for(".library-group", timeout_ms=15000)
    picked_target = library_page.evaluate("""
      (() => {
        const cell = Array.from(document.querySelectorAll('.library-group .starcell'))
          .find((c) => (c.querySelector('.starname')?.textContent || '')
            .includes('100c'));
        if (!cell) return 'no 100-coin target cell in the CCM group (looking for "100c" in the label)';
        cell.click();
        return 'clicked';
      })()
    """)
    assert picked_target == "clicked", picked_target
    library_page.wait_for(".library-target .library-section", timeout_ms=15000)

    # Confirms the fixture actually reached the real collision (a sheet
    # update could in principle rename one of the two rows and quietly
    # de-fang this test) before trusting anything that follows.
    names = library_page.evaluate(
        "Array.from(document.querySelectorAll('.library-section-name'))"
        ".map(e => e.textContent)")
    assert names.count("100 coin star Xcam") == 2, (
        f"expected the known real duplicate on star:4:6; got {names}")

    ids = library_page.evaluate(
        "Array.from(document.querySelectorAll('.library-section')).map(e => e.id)")
    assert len(ids) == len(set(ids)), f"duplicate section anchor ids: {ids}"

    open_count_after_second_click = library_page.evaluate("""
      (() => {
        const heads = Array.from(document.querySelectorAll('.library-section-head'))
          .filter((h) => h.querySelector('.library-section-name').textContent
            === '100 coin star Xcam');
        heads[1].click();
        return document.querySelectorAll('.library-section.open').length;
      })()
    """)
    assert open_count_after_second_click == 1, (
        f"clicking the second of two identically-named sections left "
        f"{open_count_after_second_click} sections open -- they are sharing "
        "one identity again")
