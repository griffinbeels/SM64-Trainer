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
# Subdivision groups ship collapsed by default (round 1, 2026-08-07); a test
# that needs example cards on screen expands every one in the open section.
EXPAND_DIVISIONS = (
    "Array.from(document.querySelectorAll("
    "'.library-section.open .library-division-head')).forEach((head) => "
    "head.getAttribute('aria-expanded') === 'true' || head.click())")


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
    # Expand the subdivisions FIRST and prove cards are on screen -- with
    # everything collapsed a zero count would pass vacuously.
    library_page.evaluate(EXPAND_DIVISIONS)
    library_page.wait_for(".library-section.open .library-example", timeout_ms=10000)
    library_page.evaluate("document.querySelector('.library-target-search').value = 'zzz-nobody'")
    library_page.evaluate(
        "document.querySelector('.library-target-search')"
        ".dispatchEvent(new Event('input', {bubbles: true}))")
    assert library_page.evaluate(
        "document.querySelectorAll('.library-example').length") == 0
    assert library_page.evaluate(
        "document.querySelectorAll('.library-plain-entry').length") == 0


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


def test_clicking_the_open_sections_head_closes_it(library_page):
    """Round 3: "we should be able to fully collapse all of them" -- the
    single-open accordion allowed zero-open on mount but never by gesture."""
    library_page.evaluate(
        "document.querySelector('.library-section.open .library-section-head').click()")
    deadline = time.time() + 5
    remaining = 1
    while time.time() < deadline:
        remaining = library_page.evaluate(
            "document.querySelectorAll('.library-section.open').length")
        if remaining == 0:
            break
        time.sleep(0.05)
    assert remaining == 0, "the open section did not close on its own header's click"


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
        if (!head.closest('.library-section').classList.contains('open')) head.click();
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
            // includes, not startsWith: the tier cell leads with the
            // division-I cap since round 2, and the cap's own numeral glyph
            // ("1") is part of textContent. "Mario" is substring-unique among
            // tier names (unlike Toad/Toadsworth), so includes is safe here.
            const row = rows.find((r) =>
              r.querySelector('.library-toc-tier').textContent.includes('Mario'));
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
    # POLL for the scroll rather than sleeping a fixed margin: a smooth
    # scrollIntoView plays out over a browser-chosen number of frames, and
    # under full-suite load it outlasts any margin measured on an idle
    # machine. The sibling test in test_ui_library_links.py used 0.6s here and
    # went red in a full-suite run on 2026-08-07 while passing alone every
    # time; both are polls now.
    import time
    deadline = time.time() + 8
    after = before
    while time.time() < deadline:
        after = library_page.evaluate("window.scrollY")
        if after > before:
            break
        time.sleep(0.05)
    assert after > before, (before, after)


def test_plus_adds_to_the_tray_and_then_disables(library_page):
    # Subdivisions ship collapsed (round 1); example cards only exist inside
    # an expanded one, so open them all before hunting for a card.
    library_page.evaluate(EXPAND_DIVISIONS)
    library_page.wait_for(".library-section.open .library-example", timeout_ms=10000)
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


# ---- fix round 3 (controller finding): auto-expand never fires on an
# entity-less target -------------------------------------------------------

def _navigate_to_target(page, group_name, target_label):
    """Back to the course grid, into `group_name`, onto `target_label`."""
    page.evaluate("document.querySelector('.entity-back').click()")
    page.wait_for(".library-courses", timeout_ms=15000)
    opened_group = page.evaluate(f"""
      (() => {{
        const cell = Array.from(document.querySelectorAll('.library-courses .starcell'))
          .find((c) => c.querySelector('.starname')?.textContent === {group_name!r});
        if (!cell) return 'no ' + {group_name!r} + ' group cell';
        cell.click();
        return 'clicked';
      }})()
    """)
    assert opened_group == "clicked", opened_group
    page.wait_for(".library-group", timeout_ms=15000)
    picked_target = page.evaluate(f"""
      (() => {{
        const cell = Array.from(document.querySelectorAll('.library-group .starcell'))
          .find((c) => c.querySelector('.starname')?.textContent === {target_label!r});
        if (!cell) return 'no ' + {target_label!r} + ' target cell';
        cell.click();
        return 'clicked';
      }})()
    """)
    assert picked_target == "clicked", picked_target
    page.wait_for(".library-target .library-section", timeout_ms=15000)


def test_auto_expand_opens_the_first_strategy_on_an_entity_less_target(library_page):
    """FIX ROUND 3. `entityKey` is `null` for every target with no entity --
    129 of the bundled snapshot's 252 targets (113 castle_movement, 15 route,
    1 not_a_target), 190 approaches between them. `useRef(null)`'s own
    initial value is `null`, so the auto-expand one-shot's guard
    (`openedEntity.current === entityKey`) was ALREADY satisfied on the very
    first mount of any one of these pages, and the effect returned before
    ever running: no section opened, on more than half the library's target
    pages. "BoB RTA (RTA strat, Fadeout, w/ cannon cutscene)" is a real
    route target (miss_reason "route", no entity) with 2 approaches on the
    bundled snapshot -- confirmed here, not assumed, since a sheet update
    could in principle collapse it to zero."""
    _navigate_to_target(library_page, "1. Bob-omb Battlefield",
                         "BoB RTA (RTA strat, Fadeout, w/ cannon cutscene)")
    section_count = library_page.evaluate(
        "document.querySelectorAll('.library-section').length")
    assert section_count > 0, "expected approaches on BoB RTA; got none"
    # The auto-expand EFFECT fires a tick after the section headers first
    # render (it is a `useEffect`, not part of the render itself), so this
    # waits for the settled `.open` state rather than racing it -- the same
    # timing this file's own Benji-collision test (test_ui_library_tray.py)
    # had to account for once it started manually opening a section too.
    library_page.wait_for(".library-target .library-section.open", timeout_ms=10000)
    open_count = library_page.evaluate(
        "document.querySelectorAll('.library-section.open').length")
    assert open_count == 1, (
        f"expected the first strategy auto-expanded on an entity-less "
        f"target; {open_count} sections were open")


def test_auto_expand_re_fires_navigating_between_two_entity_less_targets(library_page):
    """FIX ROUND 3, second half. `entityKey` is `null` for EVERY entity-less
    target, so keying the one-shot on it (or on a ref that merely started
    non-null) would still fail here: hopping from one entity-less target to
    another never changes the key, so the guard reads "already opened this
    page" and the SECOND page inherits whatever the first one's `expanded`
    state was -- 0 sections open here (a stale target-scoped identity from
    the first page cannot match any approach on the second)."""
    _navigate_to_target(library_page, "1. Bob-omb Battlefield",
                         "BoB RTA (RTA strat, Fadeout, w/ cannon cutscene)")
    library_page.wait_for(".library-target .library-section.open", timeout_ms=10000)
    first_open = library_page.evaluate(
        "document.querySelectorAll('.library-section.open').length")
    assert first_open == 1, first_open

    _navigate_to_target(library_page, "2. Whomp's Fortress",
                         "WF RTA (RTA strat, Fadeout)")
    library_page.wait_for(".library-target .library-section.open", timeout_ms=10000)
    second_open = library_page.evaluate(
        "document.querySelectorAll('.library-section.open').length")
    assert second_open == 1, (
        f"expected the SECOND entity-less target to auto-expand its own "
        f"first strategy too; {second_open} sections were open -- it "
        "inherited the first page's state instead of re-firing")


# ---- fix round 4 (controller finding, from the re-reviewer's own probe):
# a section click the instant its header exists must never be reverted -----

def test_a_click_the_instant_the_header_exists_is_never_reverted(library_server):
    """FIX ROUND 4. The re-reviewer's own method, deliberately NOT the
    `wait_for(".library-section.open")` this file's other tests use --
    that wait passes only once auto-expand has already committed, which
    defines the race away instead of testing it. Clicks a section OTHER
    than the one auto-expand picks (star:2:4's fixture: auto-expand opens
    "Fall onto the Caged Island", confirmed by
    `test_auto_open_follows_the_active_strategy_not_the_first_section`
    above; "Double jump owlless" is a real sibling approach on the same
    entity) the instant its own header exists.

    FIRST ATTEMPT AT THIS TEST WAS A FALSE NEGATIVE, recorded here because
    it is the actual proof this test has teeth. A two-step version --
    Python `wait_for(".library-section-head")`, THEN a separate
    `evaluate()` to click -- passed 10/10 even with the round-3 EFFECT-BASED
    code restored (the exact code the re-reviewer measured 5/10 reverts
    against). The Python<->browser round trip between those two calls is
    slower than Preact's own effect scheduling, so by the time the click
    script ran, the effect had already settled -- the test was measuring
    IPC latency, not the render race. Fixed by arming a MutationObserver
    and clicking inside its callback in ONE atomic in-browser script, with
    no round trip between "the header exists" and "click it": the observer
    fires as a microtask right after Preact's synchronous commit, ahead of
    its own rAF-scheduled effect, which is the actual ordering a real
    user's click competes against.
    """
    target_name = "Double jump owlless"
    click_and_wait_for_settle = f"""
      (async () => {{
        const clickIfPresent = () => {{
          const heads = Array.from(document.querySelectorAll('.library-section-head'));
          const head = heads.find((h) =>
            h.querySelector('.library-section-name').textContent === {target_name!r});
          if (!head) return false;
          if (!head.closest('.library-section').classList.contains('open')) head.click();
          return true;
        }};
        const clicked = await new Promise((resolve) => {{
          if (clickIfPresent()) {{ resolve(true); return; }}
          const observer = new MutationObserver(() => {{
            if (clickIfPresent()) {{ observer.disconnect(); resolve(true); }}
          }});
          observer.observe(document.body, {{ childList: true, subtree: true }});
          // Armed BEFORE this click, in the same synchronous script --
          // nothing round-trips to Python between arming and navigating.
          document.querySelector('.nav-item[title="Library"]').click();
          setTimeout(() => {{ observer.disconnect(); resolve(false); }}, 15000);
        }});
        if (!clicked) return 'timed out waiting for the header';
        // One more microtask turn for a PENDING revert (the round-3 bug's
        // own rAF-scheduled effect, if it were still there) to actually
        // land before this script returns -- reading immediately would
        // pass even under the bug, since the click's own optimistic state
        // has not been overwritten yet.
        await new Promise((resolve) => setTimeout(resolve, 300));
        return document.querySelector(
          '.library-section.open .library-section-name')?.textContent || null;
      }})()
    """
    for trial in range(10):
        with driver.get_driver().launch(headless=True) as page:
            page.goto(f"{library_server}/ui/index.html")
            page.wait_for(".log-list-card", timeout_ms=20000)
            open_name = page.evaluate(click_and_wait_for_settle)
            assert open_name == target_name, (trial, open_name)
