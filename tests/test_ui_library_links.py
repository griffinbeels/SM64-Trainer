"""The ways INTO the Library from elsewhere in the app (Task 7, spec
2026-08-07-library-page): the practice log's book mark, and the standards
table's per-tier deep links.

A render test, per this project's own rule: unit tests plus `node --check`
once shipped an invisible feature, and every claim below is about the real
DOM a browser builds.

task-7-caveats.md point 1 is why these tests exist at all: `openLibrary`
(app.js) had ZERO callers anywhere in `ui/` before this task, so the
`focusStrat`/`focusTier` deep-link effect in `librarytarget.js` (Task 4) had
never run in a real session. task-7-caveats.md point 3 is why the tests below
drive the MOUNTED page rather than navigating by `.entity-back`: every test in
`tests/test_ui_library_target.py` remounts `LibraryTarget` on every
navigation, so none of them exercise props changing while the component stays
mounted -- exactly the situation a deep link into an ALREADY-OPEN target page
creates.

Fixture (same as test_ui_library_target.py / test_ui_library_compare.py):
`arm_segment=FIXTURE_SEGMENT` (segment:6, "BitFS Pipe Entry" -- displayed as
"No Reds" · "Bowser in the Fire Sea" per entitysection.js's legacy-no-reds-
pipe naming, confirmed by rendering the real fixture before writing this file)
carries `last_strat="Pole Glitch"` (ui_fixture.py::FIXTURE_SEGMENT_STRAT),
which matches the bundled sheet's "Bowser in the Fire Sea Course" approach.
`seed_practice`'s star (star:2:4, "Fall onto the Caged Island") is the
active TARGET and carries the highest journal_id, so it is what Library's own
"no intent" auto-open (`lastPracticed`) lands on -- landing anywhere ELSE
after a book mark click is itself the proof a deep link, not auto-open, drove
the navigation.
"""
import json
import shutil
import sys
import time
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

from ui_fixture import FIXTURE_SEGMENT, _target_segment, serve_ui  # noqa: E402
from uilab import driver  # noqa: E402
from uilab_project import BOWSER_COURSE, BOWSER_LEVEL  # noqa: E402


def _post(base, path, payload):
    request = urllib.request.Request(
        f"{base}{path}", data=json.dumps(payload).encode(), method="POST",
        headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(request, timeout=10).read())

CLICK_PRACTICE_TAB = 'document.querySelector(\'.nav-item[title="Practice"]\').click()'

# The armed segment's card: identified by BOTH context and name, since two
# other padding entities (BitS Pipe Entry -- "Bowser in the Sky") share the
# same "No Reds" name (entitysection.js's `segmentFamily`, confirmed against
# the real render -- see this file's own module docstring).
CLICK_SEGMENT_BOOKMARK = """
(() => {
  const card = Array.from(document.querySelectorAll('.log-card')).find((c) => {
    const context = (c.querySelector('.log-card-context') || {}).textContent;
    const name = (c.querySelector('.log-card-name b') || {}).textContent;
    return context === 'Bowser in the Fire Sea' && name === 'No Reds';
  });
  if (!card) return 'no BitFS Pipe Entry (No Reds) card';
  const btn = card.querySelector('.log-card-library-link');
  if (!btn) return 'card has no book mark button';
  btn.click();
  return 'clicked';
})()
"""

CLICK_SECTION_BY_NAME = """
(() => {{
  const heads = Array.from(document.querySelectorAll('.library-section-head'));
  const head = heads.find((h) =>
    h.querySelector('.library-section-name').textContent === {name!r});
  if (!head) return 'no section named ' + {name!r};
  head.click();
  return 'clicked';
}})()
"""

OPEN_SECTION_NAME = (
    "(() => { const el = document.querySelector("
    "'.library-section.open .library-section-name'); "
    "return el ? el.textContent : null; })()"
)


@pytest.fixture(scope="module")
def library_server():
    with serve_ui(arm_segment=FIXTURE_SEGMENT, seed_editor_fixtures=True) as base:
        yield base


@pytest.fixture
def practice_page(library_server):
    """A FRESH page per test, landed on the default Practice tab -- same
    isolation reasoning test_ui_library_target.py's own `library_page`
    fixture gives (tray/nav state must not leak between tests)."""
    with driver.get_driver().launch(headless=True) as page:
        page.goto(f"{library_server}/ui/index.html")
        page.wait_for(".log-list-card", timeout_ms=20000)
        yield page


# ---- the book mark --------------------------------------------------------

def test_the_book_mark_opens_the_library_on_that_cards_entity_and_strategy(practice_page):
    """Step 2's own contract: "click the book mark on the active target's
    card -> Library opens on that target with that strategy's section
    expanded." Uses the ARMED SEGMENT's card rather than the active star's,
    deliberately: Library's own no-intent auto-open (`lastPracticed`) already
    lands on the star (the freshest journal_id, ui_fixture.py's own comment),
    so a book mark click that ALSO lands there would prove nothing about the
    deep link actually firing. Landing on the segment's target page instead
    is the one outcome auto-open could never produce on its own."""
    clicked = practice_page.evaluate(CLICK_SEGMENT_BOOKMARK)
    assert clicked == "clicked", clicked

    practice_page.wait_for(".library-target .library-section.open",
                            timeout_ms=15000)
    heading = practice_page.evaluate(
        "(document.querySelector('.library-target-heading h3') || {}).textContent")
    assert heading == "Bowser in the Fire Sea Course", (
        f"expected the book mark to land on segment:6's own target page, "
        f"got heading {heading!r} (star:2:4's auto-open default is "
        f"'Fall onto the Caged Island')")

    chip = practice_page.evaluate(
        "(() => { const el = document.querySelector("
        "'.library-section.open .library-matched-chip'); "
        "return el ? el.textContent : null; })()")
    assert chip and "Pole Glitch" in chip, (
        f"expected the section matching the card's active strategy "
        f"(Pole Glitch) to be open; matched chip was {chip!r}")


def test_a_card_with_no_library_caller_renders_no_book_mark(practice_page):
    """`ui/tunelog.js`'s own inspector reuses `PracticeLog`/`LogCard` with no
    `openLibrary` prop at all (it passes `openCompare=null` the same way).
    The real app always passes one, so this is really a guard against a
    future call site that forgets to -- a book mark with nothing behind it
    would read as a dead control (acceptance.md's rule against exactly that),
    so the fold button is checked as a control: `LogCard` renders one for
    every real card, and this proves it is CONDITIONAL, not a permanent
    fixture."""
    present = practice_page.evaluate(
        "document.querySelectorAll('.log-card .log-card-library-link').length")
    assert present >= 1, "the real app must render the book mark on real cards"


# ---- the standards-ladder tier-row deep link -------------------------------

def test_a_tier_row_link_lands_on_that_bands_scrolled_into_view(practice_page):
    """Step 2's second half: "click a Silver row in the practice standards
    table -> Library lands with the Silver band scrolled into view
    (`window.scrollY > 0` and the anchor's `getBoundingClientRect().top`
    within the viewport)." Uses the active card (`.log-card-active`, the
    star:2:4 / "Fall onto the Caged Island" / "TJ Owlless" card) -- its
    ladder carries a real "Silver" tier (confirmed against the bundled
    snapshot before writing this file)."""
    opened = practice_page.evaluate("""
      (() => {
        const card = document.querySelector('.log-card.log-card-active');
        if (!card) return 'no active card';
        const toggle = card.querySelector('.standards-toggle');
        if (!toggle) return 'no standards toggle on the active card';
        toggle.click();
        return 'clicked';
      })()
    """)
    assert opened == "clicked", opened
    practice_page.wait_for(
        ".log-card.log-card-active .stdtable", timeout_ms=10000)

    clicked = practice_page.evaluate("""
      (() => {
        const cells = Array.from(document.querySelectorAll(
          '.log-card.log-card-active .stdtable td[title]'));
        const cell = cells.find((td) => (td.title || '').includes('\\u00b7 Silver on xcams'));
        if (!cell) return 'no Silver tier row found';
        const link = cell.querySelector('.std-tier-link');
        if (!link) return 'Silver row has no library link (no active strategy?)';
        link.click();
        return 'clicked';
      })()
    """)
    assert clicked == "clicked", clicked

    practice_page.wait_for(".library-target .library-section.open",
                            timeout_ms=15000)
    # The 80ms settle timer (librarytarget.js's own comment: it beats a race
    # against Disclose mounting the section body a tick after `open` flips)
    # plus the smooth scrollIntoView animation -- same margin
    # test_ui_library_target.py's own TOC-row scroll test uses.
    time.sleep(0.6)

    heading = practice_page.evaluate(
        "(document.querySelector('.library-target-heading h3') || {}).textContent")
    assert heading == "Fall onto the Caged Island", heading

    result = practice_page.evaluate("""
      (() => {
        const band = document.querySelector(
          '.library-section.open .library-band[data-tier="Silver"]');
        if (!band) return {found: false};
        const rect = band.getBoundingClientRect();
        return {found: true, scrollY: window.scrollY,
                top: rect.top, viewportHeight: window.innerHeight};
      })()
    """)
    assert result["found"], "no Silver band anchor on the open section"
    assert result["scrollY"] > 0, (
        f"expected the page to have scrolled toward the band: {result}")
    assert 0 <= result["top"] <= result["viewportHeight"], (
        f"the Silver band should be within the viewport after landing: {result}")


def test_no_active_strategy_renders_no_tier_row_link():
    """The deep-link effect (librarytarget.js) requires a truthy
    `focusStrat` to resolve a section at all -- a link built with no active
    strategy to carry would open the Library and land nowhere, which reads as
    broken rather than as "did less than it promised" (acceptance.md's rule).
    Uses a fresh, un-seeded fixture (`seed=False` reads as `attempts=False`
    in `seed_practice`'s own call inside `serve_ui`, so nothing ever sets an
    active strategy) rather than hunting for an unranked card in the default
    one, which carries none."""
    import tempfile
    with tempfile.TemporaryDirectory() as scratch:
        with serve_ui(Path(scratch) / "no_strat.db", stage=(2, 24),
                      target=(2, 4)) as base:
            with driver.get_driver().launch(headless=True) as page:
                page.goto(f"{base}/ui/index.html")
                page.wait_for(".log-list-card", timeout_ms=20000)
                # Zero attempts anywhere in this fixture means nothing wins
                # the auto-open slot (practicelog.js's own `autoOpenKey` --
                # "a card only auto-opens once it has recorded something"),
                # so the card starts CLOSED and `Disclose` never mounts its
                # body (collapsible.js: `mounted` starts at `open`). Open it
                # by hand before reaching for the standards toggle inside.
                fold = page.evaluate("""
                  (() => {
                    const btn = document.querySelector(
                      '.log-card:not(.is-unassigned) .log-card-fold');
                    if (!btn) return 'no entity card rendered';
                    btn.click();
                    return 'clicked';
                  })()
                """)
                assert fold == "clicked", fold
                page.wait_for(".log-card-body .standards-toggle", timeout_ms=10000)
                opened = page.evaluate("""
                  (() => {
                    const toggle = document.querySelector('.standards-toggle');
                    if (!toggle) return 'no standards toggle at all';
                    toggle.click();
                    return 'clicked';
                  })()
                """)
                assert opened == "clicked", opened
                page.wait_for(".stdtable", timeout_ms=10000)
                links = page.evaluate(
                    "document.querySelectorAll('.stdtable .std-tier-link').length")
                rows = page.evaluate(
                    "document.querySelectorAll('.stdtable tbody tr').length")
                assert rows > 0, "fixture rendered no standards rows to check"
                assert links == 0, (
                    f"expected no tier-row library links with no active "
                    f"strategy set, found {links} across {rows} rows")


# ---- fix round: the deep link is consumed ONCE, not re-applied on every
# `rows` change (task-7-caveats.md point 2, "the heart of this task") -------

def test_a_manual_section_pick_survives_a_repeat_of_the_same_deep_link(practice_page):
    """`focusStrat`/`focusTier` ride `entry.rows`, and `approaches`
    (librarytarget.js) is a `useMemo` over `rows` -- so a SECOND `openLibrary`
    call for the exact same (entity, strat, tier), which re-fetches `rows`
    into a brand-new array, must not silently revert a section the user has
    since picked for himself. This is precisely the caveat's own worked
    example ("a second intent for the same entity") reproduced with the
    simplest available trigger: clicking the SAME book mark twice.

    Sequence: book mark segment:6 (lands on "Bowser in the Fire Sea Course",
    the Pole-Glitch-matched section) -> manually open a DIFFERENT sibling
    section ("No pole glitch") -> back to Practice -> click the SAME book
    mark again -> assert "No pole glitch" is STILL the open section, not
    reverted to the stale Pole Glitch link.
    """
    clicked = practice_page.evaluate(CLICK_SEGMENT_BOOKMARK)
    assert clicked == "clicked", clicked
    practice_page.wait_for(".library-target .library-section.open",
                            timeout_ms=15000)

    switched = practice_page.evaluate(
        CLICK_SECTION_BY_NAME.format(name="No pole glitch"))
    assert switched == "clicked", switched
    practice_page.wait_for(".library-section.open .library-section-name",
                            timeout_ms=10000)

    def _wait_for_open_section(name, timeout_s=8):
        deadline = time.time() + timeout_s
        last = None
        while time.time() < deadline:
            last = practice_page.evaluate(OPEN_SECTION_NAME)
            if last == name:
                return last
            time.sleep(0.05)
        return last

    manual_open = _wait_for_open_section("No pole glitch")
    assert manual_open == "No pole glitch", (
        f"manual section switch did not stick: {manual_open}")

    back = practice_page.evaluate(CLICK_PRACTICE_TAB)
    practice_page.wait_for(".log-list-card", timeout_ms=15000)
    clicked_again = practice_page.evaluate(CLICK_SEGMENT_BOOKMARK)
    assert clicked_again == "clicked", clicked_again
    practice_page.wait_for(".library-target .library-section.open",
                            timeout_ms=15000)
    # Give a re-applied (buggy) effect the SAME margin the scroll test above
    # gives a correct one, so a regression has every chance to show itself.
    time.sleep(0.6)

    still_open = practice_page.evaluate(OPEN_SECTION_NAME)
    assert still_open == "No pole glitch", (
        f"a repeat of the SAME deep link reverted the user's own pick back "
        f"to the stale target ({still_open!r}) -- the focus must be "
        f"consumed once, not re-applied on every `rows` change")


# ---- fix round 1 (review finding): a paired Bowser reds/pipe card must open
# the STAR's Library page, not its own segment: entity route ------------------

def test_the_book_mark_and_the_tier_link_open_the_same_paired_entity():
    """FIX ROUND 1 (task-7 review). A Bowser reds/pipe PAIR publishes exactly
    ONE sheet target, under the STAR's key -- `standardsIdentity(sec).entity`
    (entitysection.js), the identity the card's OWN standards ladder already
    grades against (`sec.pipe_star_entity` for a paired segment, never
    `segment:<id>`, because the sheet has no reds->pipe SEGMENT target at
    all). Opening the book mark on `entityKey(sec)` instead sent a paired
    segment's card to `segment:<id>`'s own Library page, which returns 200
    with ZERO approaches (never a 404 -- a real but barren page), while the
    ladder one click below kept showing the star's real, populated one.

    Reuses the exact fixture recipe test_fixture_reaches_the_real_page.py's
    own pairing test established (reconcile_full_corpus + enter_level +
    _target_segment onto seg:reds->pipe:bitdw) -- a genuinely separate
    scenario from this file's other tests, so it earns its own `serve_ui()`
    instance rather than the shared `practice_page` fixture, matching that
    file's own reasoning for the same recipe.
    """
    with serve_ui(reconcile_full_corpus=True,
                 stage=(BOWSER_COURSE, BOWSER_LEVEL),
                 target=(BOWSER_COURSE, 0),
                 enter_level=BOWSER_LEVEL) as base:
        segments = json.loads(urllib.request.urlopen(
            f"{base}/api/segments", timeout=10).read())
        pipe = next(s for s in segments
                   if (s.get("seed_key") or "") == "seg:reds->pipe:bitdw")
        _target_segment(base, pipe["id"])
        # A strategy, so the standards panel's `activeStrat` is non-null and
        # the tier-row link actually renders -- test_no_active_strategy_
        # renders_no_tier_row_link (above) pins that it does not without one,
        # deliberately, not a gap to route around here.
        _post(base, "/api/strat", {"kind": "segment",
                                    "segment_id": pipe["id"],
                                    "strat_tag": "Standard"})

        with driver.get_driver().launch(headless=True) as page:
            page.goto(f"{base}/ui/index.html")
            page.wait_for(".log-list-card", timeout_ms=20000)

            def open_card_and_standards():
                # The card's HEAD (book mark included) always renders; the
                # BODY (the standards panel) needs the fold opened by hand --
                # the freshly-targeted segment has recorded no attempts, so
                # it does not win the auto-open slot (practicelog.js's own
                # rule).
                result = page.evaluate("""
                  (() => {
                    const card = document.querySelector('.log-card.log-card-active');
                    if (!card) return 'no active card';
                    if (!card.querySelector('.log-card-library-link')) return 'no book mark';
                    card.querySelector('.log-card-fold').click();
                    return 'clicked';
                  })()
                """)
                assert result == "clicked", result
                page.wait_for(".log-card.log-card-active .standards-toggle",
                              timeout_ms=10000)
                page.evaluate("document.querySelector("
                              "'.log-card.log-card-active .standards-toggle').click()")
                page.wait_for(".log-card.log-card-active .std-tier-link",
                              timeout_ms=10000)

            def read_landed_library():
                # Waits for `.library-target-page` -- the OUTER wrapper
                # library.js renders the instant `stage === "target"`,
                # regardless of what `LibraryTarget` finds inside it -- never
                # for the heading `<h3>` or an open section. Both of those
                # depend on CONTENT that is exactly what this test is
                # checking: an entity with zero approaches renders an EMPTY
                # `<h3></h3>` (librarytarget.js: `label` falls back to `""`
                # when `rows` is `[]`), which Playwright's default
                # `state="visible"` wait treats as hidden (an empty text node
                # collapses) -- so waiting on the heading turned the mutated
                # run into a second flavour of TIMEOUT rather than a content
                # assertion, no more diagnosable than the first one this
                # rewrite was meant to fix. The wrapper's own presence is
                # unconditional, so it stays a reliable sync point either
                # way, and `approaches > 0` below carries the entire signal.
                page.wait_for(".library-target-page", timeout_ms=15000)
                heading = page.evaluate(
                    "(document.querySelector('.library-target-heading h3') || {}).textContent")
                approaches = page.evaluate(
                    "document.querySelectorAll('.library-section').length")
                return heading, approaches

            open_card_and_standards()
            page.evaluate("document.querySelector("
                          "'.log-card.log-card-active .log-card-library-link').click()")
            book_mark_heading, book_mark_approaches = read_landed_library()

            page.evaluate(CLICK_PRACTICE_TAB)
            page.wait_for(".log-list-card", timeout_ms=15000)
            open_card_and_standards()
            page.evaluate("document.querySelector("
                          "'.log-card.log-card-active .std-tier-link').click()")
            tier_link_heading, tier_link_approaches = read_landed_library()

    assert book_mark_approaches > 0, (
        "the book mark opened a real Library page with ZERO approaches -- "
        "it is still resolving the paired segment's OWN (target-less) "
        "identity rather than standardsIdentity(sec).entity")
    assert tier_link_approaches > 0, tier_link_approaches
    assert book_mark_heading == tier_link_heading, (
        f"the book mark and the tier-row link opened two DIFFERENT Library "
        f"pages for the SAME card: {book_mark_heading!r} vs "
        f"{tier_link_heading!r} -- one card, one ladder, must be one door")
