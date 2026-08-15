"""The shared JP/US page-level version switch (`versionswitch.js`) and its
wiring into the Library page (`library.js`, `librarytarget.js`).

His 2026-08-15 ruling: retire the Library's old PER-SECTION
`.library-jp-toggle` chip in favour of ONE switch, JP left / US right, in the
page's hero -- every section reads it, "for fun exploration of the
differences," and it grades nothing. A render test, per this project's own
rule: unit tests plus `node --check` once shipped an invisible feature, and
every claim below is a fact about the real DOM a browser builds.

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
CLICK_JP = ("Array.from(document.querySelectorAll('.version-switch-seg'))"
            ".find((seg) => seg.textContent.trim() === 'JP').click()")
CLICK_US = ("Array.from(document.querySelectorAll('.version-switch-seg'))"
            ".find((seg) => seg.textContent.trim() === 'US').click()")
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


def test_exactly_one_switch_jp_left_us_right_us_pressed_by_default(library_page):
    result = library_page.evaluate("""
      (() => {
        const segs = document.querySelectorAll('.version-switch-seg');
        return {
          switchCount: document.querySelectorAll('.version-switch').length,
          texts: Array.from(segs).map((seg) => seg.textContent.trim()),
          pressed: Array.from(segs).map((seg) => seg.getAttribute('aria-pressed')),
          oldChipCount: document.querySelectorAll('.library-jp-toggle').length,
        };
      })()
    """)
    assert result["switchCount"] == 1, result
    assert result["texts"] == ["JP", "US"], result
    assert result["pressed"] == ["false", "true"], (
        f"US should be pressed by default: {result}")
    assert result["oldChipCount"] == 0, (
        "the retired per-section .library-jp-toggle chip is still rendering")


def test_clicking_jp_reladders_the_section_and_filters_out_a_us_entry(
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
        f"{us_entry['runner']!r} (US-tagged) is not visible while the page "
        "still defaults to US")
    before = library_page.evaluate(MARIO_CUTOFF)
    assert before, "no Mario TOC row found on the JP-carrying section"

    library_page.evaluate(CLICK_JP)
    library_page.evaluate(EXPAND_DIVISIONS)  # a fresh band list mounts collapsed again
    after = library_page.evaluate(MARIO_CUTOFF)
    assert after and after != before, (
        f"the version switch did not change the Mario cutoff: {before!r} -> {after!r}")
    assert not runner_visible(), (
        f"{us_entry['runner']!r} (US-tagged) is still visible after switching to JP")
    pressed = library_page.evaluate(
        "Array.from(document.querySelectorAll('.version-switch-seg'))"
        ".map((seg) => seg.getAttribute('aria-pressed'))")
    assert pressed == ["true", "false"], pressed

    library_page.evaluate(CLICK_US)
    library_page.evaluate(EXPAND_DIVISIONS)
    restored = library_page.evaluate(MARIO_CUTOFF)
    assert restored == before, (
        f"switching back to US did not restore the Mario cutoff: {before!r} -> {restored!r}")
    assert runner_visible(), (
        f"{us_entry['runner']!r} did not come back after switching back to US")


def test_default_follows_the_effective_version_setting(library_server):
    """PUT /api/mode {"version": "jp"} flips the fixture's effective version
    -- persisted into scratch (aa9608da), so it is safe -- and a FRESH page
    must default its switch to JP with no click at all. Restores "us" in a
    `finally`, whatever happens, so this test leaves the shared module-scoped
    fixture exactly as every sibling test in this file found it."""
    try:
        flipped = _put(library_server, "/api/mode", {"version": "jp"})
        assert flipped["effective"] == "jp", flipped

        with driver.get_driver().launch(headless=True) as page:
            page.goto(f"{library_server}/ui/index.html")
            page.wait_for(".log-list-card", timeout_ms=20000)
            page.evaluate(CLICK_LIBRARY_TAB)
            page.wait_for(".version-switch", timeout_ms=15000)
            pressed = page.evaluate(
                "Array.from(document.querySelectorAll('.version-switch-seg'))"
                ".map((seg) => seg.getAttribute('aria-pressed'))")
            assert pressed == ["true", "false"], (
                f"a fresh page did not default to JP once the setting flipped: {pressed}")
    finally:
        restored = _put(library_server, "/api/mode", {"version": "us"})
        assert restored["effective"] == "us", restored


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
