"""The shared JP/US page-level region switch (`versionswitch.js`) and its
wiring into the Library page (`library.js`, `librarytarget.js`).

His 2026-08-15 ruling: retire the Library's old PER-SECTION
`.library-jp-toggle` chip in favour of ONE switch, JP left / US right, in the
page's hero -- every section reads it, "for fun exploration of the
differences," and it grades nothing. ROUND 24 kept the switch and changed what
it holds: a SET, defaulting to BOTH regions on the Library ("By default, in
the Library, we should show BOTH rank standards combined... Right now,
information about runners / approaches feels hidden, which is not the
intent"), with at least one region always on and each segment drawn as its
country's flag rather than the two letters. So the gesture these tests make is
"turn a region OFF", never "pick a region", and the segment is found by its
`aria-label` -- the word is still there, it is just no longer the visible
carrier. A render test, per this project's own rule: unit tests plus
`node --check` once shipped an invisible feature, and every claim below is a
fact about the real DOM a browser builds.

Fixture: the same one `test_ui_library_target.py` uses --
`arm_segment=FIXTURE_SEGMENT, seed_editor_fixtures=True` lands auto-open
directly on star:2:4's target page ("Fall onto the Caged Island"), whose
"Owl strat w/o speed preservation" approach carries `ladder_jp` (US Mario
15.83s vs JP 16.00s, that file's own jp-toggle test documents it) and mixes
entries tagged both "us" and "jp" -- exactly what this file needs to prove
the switch both re-ladders AND filters.
"""
import json
import shutil
import sys
import urllib.request
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

from sm64_events.desktop.window import MIN_WINDOW_WIDTH  # noqa: E402
from ui_fixture import FIXTURE_SEGMENT, serve_ui  # noqa: E402
from uilab import driver  # noqa: E402

CLICK_LIBRARY_TAB = 'document.querySelector(\'.nav-item[title="Library"]\').click()'
# Subdivision groups ship collapsed by default; a test that needs entries on
# screen expands the open section's own divisions first (same pattern
# test_ui_library_target.py and test_ui_library_version_treatment.py use).
EXPAND_DIVISIONS = (
    "Array.from(document.querySelectorAll("
    "'.library-section.open .library-division-head')).forEach((head) => "
    "head.getAttribute('aria-expanded') === 'true' || head.click())")
OPEN_OWL_STRAT = """
(() => {
  const heads = Array.from(document.querySelectorAll('.library-section-head'));
  const head = heads.find((b) =>
    b.querySelector('.library-section-name').textContent
      === 'Owl strat w/o speed preservation');
  if (!head) return false;
  if (!head.closest('.library-section').classList.contains('open')) head.click();
  return true;
})()
"""
def _toggle(region):
    """Click one region segment. `aria-label` rather than the text, because
    round 24 replaced the two letters with the country's flag."""
    return ("Array.from(document.querySelectorAll('.version-switch-seg'))"
            f".find((seg) => seg.getAttribute('aria-label') === '{region}').click()")


TOGGLE_JP = _toggle("JP")
TOGGLE_US = _toggle("US")
PRESSED = ("Array.from(document.querySelectorAll('.version-switch-seg'))"
           ".map((seg) => seg.getAttribute('aria-pressed'))")
MARIO_CUTOFF = """
(() => {
  const rows = Array.from(document.querySelectorAll(
    '.library-section.open .library-toc-row'));
  // includes, not startsWith: the tier cell leads with the division-I cap
  // (round 2), and "Mario" is substring-unique among tier names.
  const row = rows.find((r) =>
    r.querySelector('.library-toc-tier').textContent.includes('Mario'));
  return row ? row.querySelector('.library-toc-cutoff').textContent.trim() : null;
})()
"""


def _put(base, path, payload):
    request = urllib.request.Request(
        f"{base}{path}", data=json.dumps(payload).encode(), method="PUT",
        headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(request, timeout=10).read())


def _find_us_only_plain_entry(base):
    """One "us"-tagged, videoless entry off the Owl strat approach -- picked
    LIVE from the fixture's own served payload (never hand-typed), the same
    derive-at-test-time discipline test_ui_library_version_treatment.py
    already uses for this exact snapshot. Videoless keeps the DOM shape
    stable (a `PlainEntry`'s `.library-plain-runner`), never the richer,
    click-to-play `ExampleCard`."""
    data = json.loads(urllib.request.urlopen(
        f"{base}/api/library/entity/star:2:4", timeout=10).read())
    for target in data["targets"]:
        for approach in target.get("approaches") or []:
            if approach["name"] != "Owl strat w/o speed preservation":
                continue
            for entry in approach.get("entries") or []:
                if entry.get("version") == "us" and not entry.get("video"):
                    return entry
    return None


@pytest.fixture(scope="module")
def library_server():
    with serve_ui(arm_segment=FIXTURE_SEGMENT, seed_editor_fixtures=True) as base:
        yield base


@pytest.fixture
def library_page(library_server):
    """A FRESH page per test -- sharing one across the module let a clicked
    switch (or an opened section) leak into whatever ran after it, the same
    flakiness test_ui_library_target.py's own fixture already paid for."""
    with driver.get_driver().launch(headless=True) as page:
        page.goto(f"{library_server}/ui/index.html")
        page.wait_for(".log-list-card", timeout_ms=20000)
        page.evaluate(CLICK_LIBRARY_TAB)
        page.wait_for(".library-target .library-section", timeout_ms=15000)
        yield page


def test_exactly_one_switch_jp_left_us_right_both_on_by_default(library_page):
    result = library_page.evaluate("""
      (() => {
        const segs = document.querySelectorAll('.version-switch-seg');
        return {
          switchCount: document.querySelectorAll('.version-switch').length,
          labels: Array.from(segs).map((seg) => seg.getAttribute('aria-label')),
          flags: Array.from(segs).map((seg) => {
            const img = seg.querySelector('img.region-flag');
            return img ? img.getAttribute('src') : null;
          }),
          alts: Array.from(segs).map((seg) => {
            const img = seg.querySelector('img.region-flag');
            return img ? img.getAttribute('alt') : null;
          }),
          pressed: Array.from(segs).map((seg) => seg.getAttribute('aria-pressed')),
          oldChipCount: document.querySelectorAll('.library-jp-toggle').length,
        };
      })()
    """)
    assert result["switchCount"] == 1, result
    assert result["labels"] == ["JP", "US"], result
    assert result["flags"] == ["/ui/assets/flag_jp.svg", "/ui/assets/flag_us.svg"], (
        f"the segments are not drawing the fetched flag assets: {result}")
    # The word is never the flag's only carrier -- a failed asset load, a
    # screen reader and a hover all still say which region this is.
    assert result["alts"] == ["JP", "US"], result
    assert result["pressed"] == ["true", "true"], (
        f"the Library defaults to BOTH regions: {result}")
    assert result["oldChipCount"] == 0, (
        "the retired per-section .library-jp-toggle chip is still rendering")


def test_the_last_region_left_on_cannot_be_turned_off(library_page):
    """His rule, verbatim: "There must be at least one region enabled at all
    times." Enforced in the control itself rather than in each of the two
    pages that mount it -- and enforced by DISABLING the sole segment, so the
    reason lands where the click does instead of arriving as a message after
    the mistake."""
    library_page.evaluate(TOGGLE_JP)
    library_page.wait_ms(200)
    state = library_page.evaluate("""
      (() => {
        const segs = Array.from(document.querySelectorAll('.version-switch-seg'));
        const us = segs.find((seg) => seg.getAttribute('aria-label') === 'US');
        return { pressed: segs.map((seg) => seg.getAttribute('aria-pressed')),
                 usDisabled: us.disabled, usTitle: us.getAttribute('title') };
      })()
    """)
    assert state["pressed"] == ["false", "true"], state
    assert state["usDisabled"] is True, (
        f"US is the only region left on and must refuse to switch off: {state}")
    assert "at least one region" in (state["usTitle"] or ""), state
    library_page.evaluate(TOGGLE_US)          # a no-op, and must stay one
    library_page.wait_ms(200)
    assert library_page.evaluate(PRESSED) == ["false", "true"]


def test_turning_us_off_reladders_the_section_and_filters_out_a_us_entry(
        library_page, library_server):
    us_entry = _find_us_only_plain_entry(library_server)
    assert us_entry, (
        "no videoless US-tagged entry on the Owl strat approach -- "
        "the fixture's bundled snapshot no longer matches this test's premise")

    opened = library_page.evaluate(OPEN_OWL_STRAT)
    assert opened, "could not find the Owl strat section to open"
    library_page.wait_for(".version-switch", timeout_ms=10000)
    library_page.evaluate(EXPAND_DIVISIONS)

    def runner_visible():
        return library_page.evaluate(f"""
          Array.from(document.querySelectorAll(
            '.library-section.open .library-plain-runner'))
            .some((el) => el.textContent === {us_entry["runner"]!r})
        """)

    assert runner_visible(), (
        f"{us_entry['runner']!r} (US-tagged) is not visible with both regions on")
    before = library_page.evaluate(MARIO_CUTOFF)
    assert before, "no Mario TOC row found on the JP-carrying section"

    library_page.evaluate(TOGGLE_US)          # JP only
    library_page.evaluate(EXPAND_DIVISIONS)  # a fresh band list mounts collapsed again
    after = library_page.evaluate(MARIO_CUTOFF)
    assert after and after != before, (
        f"turning US off did not change the Mario cutoff: {before!r} -> {after!r}")
    assert not runner_visible(), (
        f"{us_entry['runner']!r} (US-tagged) is still visible with US turned off")
    assert library_page.evaluate(PRESSED) == ["true", "false"]

    library_page.evaluate(TOGGLE_US)          # both again
    library_page.evaluate(EXPAND_DIVISIONS)
    restored = library_page.evaluate(MARIO_CUTOFF)
    assert restored == before, (
        f"turning US back on did not restore the Mario cutoff: {before!r} -> {restored!r}")
    assert runner_visible(), (
        f"{us_entry['runner']!r} did not come back after turning US back on")


def test_the_effective_version_still_picks_which_ladder_the_bands_come_from(
        library_server):
    """Round 24 split one question into two. WHICH ENTRIES are listed is the
    region SET, and the Library's default for that is BOTH regardless of the
    setting. WHICH LADDER the bands are cut from is still ONE region, and that
    still follows the session's effective version -- so flipping the setting
    to JP must move the Mario cutoff on a fresh page with nobody clicking
    anything, while both segments stay on.

    PUT /api/mode {"version": "jp"} persists into scratch (aa9608da), so it is
    safe; "us" is restored in a `finally` whatever happens, leaving the shared
    module-scoped fixture exactly as every sibling test found it."""
    def cutoff_on_a_fresh_page():
        with driver.get_driver().launch(headless=True) as page:
            page.goto(f"{library_server}/ui/index.html")
            page.wait_for(".log-list-card", timeout_ms=20000)
            page.evaluate(CLICK_LIBRARY_TAB)
            page.wait_for(".library-target .library-section", timeout_ms=15000)
            assert page.evaluate(OPEN_OWL_STRAT), "no Owl strat section"
            page.wait_for(".version-switch", timeout_ms=10000)
            page.evaluate(EXPAND_DIVISIONS)
            return page.evaluate(PRESSED), page.evaluate(MARIO_CUTOFF)

    us_pressed, us_cutoff = cutoff_on_a_fresh_page()
    assert us_pressed == ["true", "true"], us_pressed
    assert us_cutoff, "no Mario TOC row on the JP-carrying section"
    try:
        flipped = _put(library_server, "/api/mode", {"version": "jp"})
        assert flipped["effective"] == "jp", flipped
        jp_pressed, jp_cutoff = cutoff_on_a_fresh_page()
        assert jp_pressed == ["true", "true"], (
            f"the Library shows both regions whatever the setting says: {jp_pressed}")
        assert jp_cutoff and jp_cutoff != us_cutoff, (
            "the bands did not move to the JP ladder when the setting flipped: "
            f"{us_cutoff!r} -> {jp_cutoff!r}")
    finally:
        restored = _put(library_server, "/api/mode", {"version": "us"})
        assert restored["effective"] == "us", restored


def test_narrowing_to_one_region_says_so_and_refiles_the_overall_block(
        library_page, library_server):
    """Whole-branch review 2026-08-15, findings 5 + 6, carried into round 24's
    shape: the hero switch always says what it is doing (explain, never dim),
    and the Overall Rank Standards block above the sections re-fetches on the
    page's LADDER region rather than sitting on the grading ladder while every
    section below it re-files under JP. The default note names the state the
    round was opened about -- both regions, nothing hidden."""
    library_page.wait_for(".version-switch", timeout_ms=10000)
    read_note = ("(document.querySelector('.workshop-hero .version-switch-note')"
                 " || {}).textContent")
    assert library_page.evaluate(read_note) == "Both regions shown"
    library_page.evaluate(TOGGLE_US)          # JP only
    library_page.wait_ms(400)
    note = library_page.evaluate(read_note)
    assert note == "JP only · you are graded on US", note
    # The overall block's OWN request named the version -- read it off the
    # page's resource timeline rather than inferring it from a rendered value
    # (the fixture star's overall ladder need not differ between versions).
    requests = library_page.evaluate("""
      performance.getEntriesByType('resource')
        .map((entry) => entry.name)
        .filter((name) => name.includes('/api/ranks/standards?'))
    """)
    assert any("version=jp" in name for name in requests), requests
    library_page.evaluate(TOGGLE_US)          # both again
    library_page.wait_ms(400)
    assert library_page.evaluate(read_note) == "Both regions shown"


def test_a_matched_strategys_standing_is_the_served_one_at_the_graded_version(
        library_page):
    """Finding 4: at the graded version the section keeps the SERVED standing
    (graded on the standards ladder, in the active rank mode) rather than
    re-walking the PB against the sheet's own ladder -- the two ladders differ
    for every matched approach in the shipped snapshot, so a re-walk here
    would contradict the practice card's medal. Round-tripping the switch
    must therefore land back on the identical badge."""
    library_page.wait_for(".version-switch", timeout_ms=10000)
    read = ("Array.from(document.querySelectorAll('.library-section .library-your-standing'))"
            ".map((el) => el.textContent.trim())")
    before = library_page.evaluate(read)
    assert before, "no standing badges rendered on the target page"
    library_page.evaluate(TOGGLE_US)          # JP only
    library_page.wait_ms(400)
    library_page.evaluate(TOGGLE_US)          # both again
    library_page.wait_ms(400)
    assert library_page.evaluate(read) == before


def test_hero_does_not_overflow_at_the_minimum_supported_width(library_page):
    """The supported minimum width (CLAUDE.md, 2026-07-29): the version
    switch now lives in the hero beside Refresh (`.workshop-hero-actions`),
    and neither may push the page wider than the floor both
    `desktop/window.py::MIN_WINDOW_WIDTH` and `uilab_project.py`'s own sweep
    already enforce everywhere else."""
    library_page.set_viewport(MIN_WINDOW_WIDTH, 900)
    library_page.wait_ms(200)
    overflow = library_page.evaluate(
        f"document.documentElement.scrollWidth - {MIN_WINDOW_WIDTH}")
    assert overflow <= 0, (
        f"the page overflows by {overflow}px at {MIN_WINDOW_WIDTH}px wide")
