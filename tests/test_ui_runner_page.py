"""The runner page (Task 5, spec 2026-08-20-ranked-leaderboard) and its two
doors: a leaderboard row (leaderboard.js) and a runner's name inside a
Library target page (librarytarget.js).

`serve_ui(arm_segment=FIXTURE_SEGMENT, seed_editor_fixtures=True)` is the
exact fixture test_ui_library_target.py already uses to reach a real,
populated target page (star:2:4, four real bundled-sheet approaches) — and
per test_ui_leaderboard.py's own docstring the SAME server also carries a
real 443-runner board with no extra seeding, since `LibraryStore` loads the
bundled Ultimate Sheet snapshot unconditionally. One server serves both
doors; no invented data.
"""
import json
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

CLICK_RANK_TAB = 'document.querySelector(\'.nav-item[title="Rank"]\').click()'

# The board is a CLOSED card by default (fourth read, 2026-08-23): every page
# that needs its rows opens it first, through the real head button.
OPEN_LEADERBOARD = """
  (() => {
    const head = document.querySelector('.leaderboard-card-head');
    if (head && head.getAttribute('aria-expanded') !== 'true') head.click();
    return !!head;
  })()
"""
CLICK_LIBRARY_TAB = 'document.querySelector(\'.nav-item[title="Library"]\').click()'
CLICK_PRACTICE_TAB = 'document.querySelector(\'.nav-item[title="Practice"]\').click()'
# Subdivision groups ship collapsed by default -- same helper
# test_ui_library_target.py uses to reveal the ExampleCard/PlainEntry rows a
# runner's name lives in.
EXPAND_DIVISIONS = (
    "Array.from(document.querySelectorAll("
    "'.library-section.open .library-division-head')).forEach((head) => "
    "head.getAttribute('aria-expanded') === 'true' || head.click())")

HEADERS = "Array.from(document.querySelectorAll('.rank-breakdown th')).map(e => e.textContent.trim())"


@pytest.fixture(scope="module")
def server():
    with serve_ui(arm_segment=FIXTURE_SEGMENT, seed_editor_fixtures=True) as base:
        yield base


@pytest.fixture
def rank_page(server):
    """A fresh page on the Rank tab -- the user's own board, nobody's runner
    page open yet."""
    with driver.get_driver().launch(headless=True) as page:
        page.goto(f"{server}/ui/index.html")
        page.wait_for(".log-list-card", timeout_ms=20000)
        page.evaluate(CLICK_RANK_TAB)
        page.wait_for(".leaderboard-card-head", timeout_ms=15000)
        assert page.evaluate(OPEN_LEADERBOARD)
        page.wait_for(".leaderboard-row", timeout_ms=15000)
        page.wait_ms(500)
        yield page


@pytest.fixture
def library_page(server):
    """A fresh page auto-opened onto a real target page with real community
    entries, divisions expanded so a runner's name is on screen."""
    with driver.get_driver().launch(headless=True) as page:
        page.goto(f"{server}/ui/index.html")
        page.wait_for(".log-list-card", timeout_ms=20000)
        page.evaluate(CLICK_LIBRARY_TAB)
        page.wait_for(".library-target .library-section", timeout_ms=15000)
        page.evaluate(EXPAND_DIVISIONS)
        page.wait_ms(200)
        yield page


def click_a_runner_row(page):
    """The leaderboard's own door: click the first row that is not the
    user's, return the runner name it named."""
    name = page.evaluate("""
      (() => {
        const row = Array.from(document.querySelectorAll('.leaderboard-row'))
          .find((candidate) => !candidate.classList.contains('is-you'));
        if (!row) return null;
        const name = row.querySelector('.leaderboard-name').textContent;
        row.click();
        return name;
      })()
    """)
    assert name, "no non-you leaderboard row to click"
    return name


def assert_reads_as_the_runner_page(page, expected_name):
    page.wait_for(".runner-page", timeout_ms=8000)
    # The page shell mounts before its independently fetched scope summary.
    page.wait_for(".runner-page .scope-chip", timeout_ms=8000)
    heading = page.evaluate("document.querySelector('.runner-page h2').textContent")
    assert heading == expected_name, (
        f"the runner page opened for {heading!r}, expected {expected_name!r}")


def assert_no_editing_controls_on_the_runner_page(page):
    """Read-only, contract-mandated: no Ignore/Include control, no ✎ icon-
    repoint affordance, anywhere on the runner page. The entity-name DOORS
    (`.rank-entity-link`, round 1) are buttons too, but they change nothing
    -- they navigate -- so the count excludes exactly that class, and the
    ▶ that reveals a video beneath the row (`.rank-row-play`, third read)
    for the same reason: revealing is not editing."""
    ignore_buttons = page.evaluate(
        "document.querySelectorAll('.runner-page .rank-breakdown tbody "
        "button:not(.rank-entity-link):not(.rank-row-play)').length")
    assert ignore_buttons == 0, (
        f"found {ignore_buttons} button(s) in the runner page's breakdown rows "
        "-- the runner variant must carry no Ignore/Include control")
    edit_icons = page.evaluate("document.querySelectorAll('.runner-page .editicon').length")
    assert edit_icons == 0, f"found {edit_icons} ✎ icon(s) on the runner page"


# ---- Step 1's own three assertions, in one test ---------------------------

def test_the_runner_page_draws_name_chips_and_breakdown_with_no_editing_controls(rank_page):
    name = click_a_runner_row(rank_page)
    assert_reads_as_the_runner_page(rank_page, name)
    # The summary chips and breakdown use separate requests. A chip proves
    # the page's identity, not that the table we are asserting has arrived.
    rank_page.wait_for(".runner-page .rank-breakdown thead th", timeout_ms=8000)
    headers = rank_page.evaluate(HEADERS)
    assert "Their time" in headers and "Your time" in headers and "Gap" in headers, headers
    assert "Score (pts)" not in headers and "Gain (pts)" not in headers, headers
    assert_no_editing_controls_on_the_runner_page(rank_page)


def test_the_users_own_rank_page_still_shows_both(rank_page):
    """The regression guard for the `variant` change: before ever opening a
    runner, the user's OWN breakdown still carries its Ignore/Include
    control and the coverage strip still offers ✎."""
    headers = rank_page.evaluate(HEADERS)
    assert "Score (pts)" in headers and "Gain (pts)" in headers, headers
    assert "Their time" not in headers and "Gap" not in headers, headers
    ignore_or_include = rank_page.evaluate("""
      Array.from(document.querySelectorAll('.rank-page .rank-breakdown tbody button'))
        .some((btn) => btn.textContent.trim() === 'Ignore' || btn.textContent.trim() === 'Include')
    """)
    assert ignore_or_include, "no Ignore/Include button found on the user's own breakdown"
    edit_icons = rank_page.evaluate("document.querySelectorAll('.rank-page .editicon').length")
    assert edit_icons > 0, "no ✎ icon found in the user's own coverage strip"


def test_back_returns_to_the_leaderboard(rank_page):
    click_a_runner_row(rank_page)
    rank_page.wait_for(".runner-page", timeout_ms=8000)
    rank_page.evaluate("document.querySelector('.runner-page .entity-back').click()")
    rank_page.wait_for(".leaderboard-card-head", timeout_ms=8000)
    assert rank_page.evaluate(OPEN_LEADERBOARD)
    rank_page.wait_for(".leaderboard-row", timeout_ms=8000)
    assert rank_page.count(".runner-page") == 0, "the runner page never closed"


# ---- The runner page's own door into the Library (round 1, 2026-08-22) ----
# His first read: "I should be able to click on any of the icons within the
# Coverage list and jump straight to their Library entry for that specific
# time", and "click the name of any of the entities and be brought
# immediately to the library page for that entity, focused specifically on
# this player's entry". Both fire the SAME intent ({kind:"target", entity,
# runner, timeCs}); the Library's arrival effect finds the approach holding
# that runner's time and blinks the exact entry (`.library-arrival`, now
# carrying `data-runner`/`data-time-cs` on plain rows too, since most entries
# have no video). A tile the runner never ran has no entry to land on and
# stays the dead control fix round 1 made it -- that half of the contract is
# unchanged and still guarded below.

def click_a_lit_tile(page, scope_selector):
    """Click the first PRACTICED (non-`.is-unpracticed`) coverage tile inside
    `scope_selector`, return whether one was found."""
    return page.evaluate(f"""
      (() => {{
        const tile = document.querySelector(
          {json.dumps(scope_selector)} + ' .entity-tile:not(.is-unpracticed)');
        if (!tile) return false;
        tile.click();
        return true;
      }})()
    """)


def force_pseudo_state(page, selector, classes):
    """Force CSS pseudo-classes (`["hover", "focus-visible"]`) on the ONE
    element matching `selector`, via CDP `CSS.forcePseudoState` -- real input
    state, so `getComputedStyle` reflects it exactly the way the browser
    would after an actual hover/keyboard focus. uilab's `Page` protocol has
    no hover verb (dispatching a synthetic `mouseover` does NOT flip Chromium's
    internal `:hover` match, only real pointer/input-level state does), so this
    reaches `page._cdp` directly rather than inventing a driver-specific
    workaround here -- `matched_styles` already reaches the same CDP session
    for the same reason (cascade explanation the DOM cannot answer)."""
    cdp = page._cdp
    cdp.send("DOM.enable")
    cdp.send("CSS.enable")
    root = cdp.send("DOM.getDocument")["root"]["nodeId"]
    node = cdp.send("DOM.querySelector", {"nodeId": root, "selector": selector})["nodeId"]
    if not node:
        raise LookupError(f"no element matches {selector!r}")
    cdp.send("CSS.forcePseudoState", {"nodeId": node, "forcedPseudoClasses": classes})



ARRIVAL = """
  (() => {
    const hit = document.querySelector('.library-page .library-arrival');
    return JSON.stringify(hit ? {runner: hit.dataset.runner, timeCs: hit.dataset.timeCs,
      openDivision: !!hit.closest('.library-division-body')} : null);
  })()
"""


def assert_landed_on_the_runners_entry(page, runner):
    page.wait_for(".library-target", timeout_ms=8000)
    page.wait_ms(900)           # the arrival blink lands ~550ms after the section opens
    landed = json.loads(page.evaluate(ARRIVAL))
    assert landed and landed["runner"] == runner, (
        f"the Library did not land on {runner}'s entry: {landed}")
    assert landed["openDivision"], "the entry's own DivisionGroup is not open"


def test_a_lit_coverage_tile_on_the_runner_page_opens_their_library_entry(rank_page):
    runner = click_a_runner_row(rank_page)
    rank_page.wait_for(".runner-page .entity-tile", timeout_ms=8000)
    rank_page.wait_ms(200)
    found = click_a_lit_tile(rank_page, ".runner-page")
    assert found, "no practiced coverage tile on the runner page to click"
    rank_page.wait_ms(200)
    # Still never EntityDetail -- that panel reads the VIEWING user's own
    # attempts/PB, not the runner's (fix round 1's live repro, darkdog47:
    # 9575 pts beside PB 0'11"43, two people's numbers with no label).
    assert rank_page.count(".runner-page .entity-detail") == 0, (
        "a runner-page coverage tile opened EntityDetail instead of the Library")
    assert_landed_on_the_runners_entry(rank_page, runner)
    # And the Rank tab brings him BACK to the runner he left, not to the
    # board: a trip through the page's own door must not lose the page.
    rank_page.evaluate(CLICK_RANK_TAB)
    rank_page.wait_ms(200)
    assert rank_page.count(".runner-page") == 1, (
        "returning from the Library lost the runner page")


def test_an_entity_name_in_the_runner_breakdown_opens_their_library_entry(rank_page):
    runner = click_a_runner_row(rank_page)
    rank_page.wait_for(".runner-page .rank-entity-link", timeout_ms=8000)
    rank_page.evaluate("document.querySelector('.runner-page .rank-entity-link').click()")
    assert_landed_on_the_runners_entry(rank_page, runner)


def test_an_unpracticed_tile_on_the_runner_page_stays_a_dead_control(rank_page):
    click_a_runner_row(rank_page)
    rank_page.wait_for(".runner-page .entity-tile", timeout_ms=8000)
    rank_page.wait_ms(200)
    counts = json.loads(rank_page.evaluate("""
      JSON.stringify({
        unpracticed: document.querySelectorAll('.runner-page .entity-tile.is-unpracticed').length,
        unpracticedStatic: document.querySelectorAll('.runner-page .entity-tile.is-unpracticed.is-static').length,
        litStatic: document.querySelectorAll('.runner-page .entity-tile:not(.is-unpracticed).is-static').length,
      })"""))
    assert counts["unpracticed"] >= 1, (
        "the leader has run everything -- no unpracticed tile to judge; pick a "
        "runner with a gap")
    assert counts["unpracticedStatic"] == counts["unpracticed"], (
        "an unpracticed tile on the runner page is clickable, with no entry to land on")
    assert counts["litStatic"] == 0, "a practiced tile on the runner page is not a door"
    # Dead-control contract (fix round 1, final review H2): a static tile
    # must not LOOK clickable -- no pointer, and nothing moves on hover or
    # keyboard focus, or the global `button:hover`/`:focus-visible` rules
    # have beaten `.entity-tile.is-static` again.
    TILE = ".runner-page .entity-tile.is-static"
    cursor = rank_page.evaluate(f"getComputedStyle(document.querySelector({TILE!r})).cursor")
    assert cursor == "default", f"a static coverage tile still shows cursor: {cursor!r}"
    rest = rank_page.evaluate(f"""
      (() => {{
        const s = getComputedStyle(document.querySelector({TILE!r}));
        return {{background: s.backgroundColor, border: s.borderTopColor, outline: s.outlineStyle}};
      }})()
    """)
    force_pseudo_state(rank_page, TILE, ["hover", "focus-visible"])
    hovered = rank_page.evaluate(f"""
      (() => {{
        const s = getComputedStyle(document.querySelector({TILE!r}));
        return {{background: s.backgroundColor, border: s.borderTopColor, outline: s.outlineStyle}};
      }})()
    """)
    assert hovered == rest, (
        f"a static coverage tile changed on hover/focus: {rest} -> {hovered} "
        "-- a dead control must show no affordance at all")


STRIP_FITS = """
  (() => {
    const strip = document.querySelector(%s + ' .rank-coverage-strip');
    const s = getComputedStyle(strip);
    return JSON.stringify({scroll: strip.scrollHeight, client: strip.clientHeight,
      overflow: s.overflowY, tiles: strip.querySelectorAll('.entity-tile').length});
  })()
"""


def assert_every_tile_is_visible(page, scope_selector):
    """His second item: "tall enough so that we can see every single star /
    segment at the same time. I shouldn't have to scroll." -- the strip's
    scroll height equals its visible height, and nothing clips it."""
    fit = json.loads(page.evaluate(STRIP_FITS % json.dumps(scope_selector)))
    assert fit["tiles"] > 40, f"fixture strip too small to prove anything: {fit}"
    assert fit["overflow"] not in ("auto", "scroll"), f"the strip scrolls: {fit}"
    assert fit["scroll"] == fit["client"], f"the strip clips its own tiles: {fit}"


def test_the_coverage_strip_shows_every_tile_without_scrolling(rank_page):
    assert_every_tile_is_visible(rank_page, ".rank-page")       # his own tab
    click_a_runner_row(rank_page)
    rank_page.wait_for(".runner-page .entity-tile", timeout_ms=8000)
    rank_page.wait_ms(200)
    assert_every_tile_is_visible(rank_page, ".runner-page")


def test_clicking_a_lit_coverage_tile_still_opens_the_panel_on_your_own_tab(rank_page):
    """The regression guard for this fix round: before ever opening a
    runner, the user's OWN coverage tile is still a real control."""
    found = click_a_lit_tile(rank_page, ".rank-page")
    assert found, "no practiced coverage tile on the user's own tab to click"
    rank_page.wait_ms(200)
    assert rank_page.count(".rank-page .entity-detail") == 1, (
        "clicking a practiced coverage tile on the user's own tab did not "
        "open EntityDetail")
    static_tiles = rank_page.evaluate(
        "document.querySelectorAll('.rank-page .entity-tile.is-static').length")
    assert static_tiles == 0, (
        f"found {static_tiles} .is-static tile(s) on the user's own tab -- "
        "every tile there must stay interactive")


# ---- the second door: a runner's name inside the Library ------------------

def test_a_library_entry_runners_name_opens_the_same_runner_page(library_page):
    name = library_page.evaluate("""
      (() => {
        const link = document.querySelector('.library-target .library-runner-link');
        if (!link) return null;
        const name = link.textContent;
        link.click();
        return name;
      })()
    """)
    assert name, "no clickable runner name found on the opened target page"
    assert_reads_as_the_runner_page(library_page, name)
    assert_no_editing_controls_on_the_runner_page(library_page)


def test_the_syntheticyou_row_never_offers_a_runner_link(library_page):
    """`entry._isYou` (the leaderboard-mode PB insert, librarymodel.js) has
    no runner behind it, and must never render as a clickable name."""
    switched = library_page.evaluate("""
      (() => {
        const seg = Array.from(document.querySelectorAll('.library-mode-seg'))
          .find((btn) => btn.textContent.trim() === 'Leaderboard');
        if (!seg) return false;
        seg.click();
        return true;
      })()
    """)
    if not switched:
        pytest.skip("no open section carries the Leaderboard mode switch")
    library_page.wait_ms(200)
    you_is_a_link = library_page.evaluate("""
      (() => {
        const row = document.querySelector('.library-leaderboard-row.is-you');
        return !!(row && row.querySelector('.library-runner-link'));
      })()
    """)
    assert not you_is_a_link, "the synthetic You row rendered as a clickable runner link"

# ---- Round 1, second read (2026-08-22): links look like links, every row
# leads with its art, and the breakdown opens in route order --------------

LINK_LOOK = """
  (() => {
    const link = document.querySelector(%s);
    const plain = document.querySelector(%s);
    const ls = getComputedStyle(link), ps = getComputedStyle(plain);
    return JSON.stringify({linkColor: ls.color, plainColor: ps.color,
      decoration: ls.textDecorationLine, cursor: ls.cursor});
  })()
"""


def assert_reads_as_a_link(page, link_selector, plain_selector):
    """His ruling: "Anything that's a link should be obvious that it's a
    link" -- at REST, not only on hover: a colour the surrounding text does
    not have, an underline, and a pointer."""
    look = json.loads(page.evaluate(LINK_LOOK % (json.dumps(link_selector), json.dumps(plain_selector))))
    assert look["linkColor"] != look["plainColor"], f"the link wears the plain text colour: {look}"
    assert "underline" in look["decoration"], f"the link is not underlined at rest: {look}"
    assert look["cursor"] == "pointer", look


def test_every_text_door_reads_as_a_link_at_rest(rank_page, library_page):
    click_a_runner_row(rank_page)
    rank_page.wait_for(".runner-page .rank-entity-link", timeout_ms=8000)
    assert_reads_as_a_link(rank_page, ".runner-page .rank-entity-link",
                           ".runner-page .rank-cell-points")
    library_page.wait_for(".library-target .library-runner-link", timeout_ms=8000)
    assert_reads_as_a_link(library_page, ".library-target .library-runner-link",
                           ".library-target .library-plain-time")


ROW_ART = """
  JSON.stringify((() => {
    const rows = Array.from(document.querySelectorAll(%s + ' .rank-table tbody tr'));
    return {rows: rows.length,
      withArt: rows.filter((row) => {
        const art = row.querySelector('.rank-cell-name .rank-row-icon');
        return art && art.getAttribute('src') && art.getBoundingClientRect().width > 0;
      }).length};
  })())
"""


def assert_every_row_leads_with_its_art(page, scope_selector):
    """"to the left of all the names, we should include their star/segment
    icons" -- on both the runner page and his own tab, since both render
    through the one `Breakdown`."""
    art = json.loads(page.evaluate(ROW_ART % json.dumps(scope_selector)))
    assert art["rows"] > 10, art
    assert art["withArt"] == art["rows"], f"rows without a painted icon: {art}"


SERVED_ORDER = """
  fetch('/api/marelo?scope=overall').then((r) => r.json())
    .then((body) => JSON.stringify(body.entities.map((e) => e.label)))
"""
DRAWN_ORDER = """
  JSON.stringify(Array.from(document.querySelectorAll(%s + ' .rank-table tbody tr .rank-cell-name'))
    .map((cell) => cell.textContent.trim()))
"""


def assert_breakdown_opens_in_served_order(page, scope_selector):
    """"Default sort should be Route Order" -- the rows open exactly as the
    server ordered them (route order on a route, scope order on Overall),
    and the toggle names that state."""
    label = page.evaluate(f"document.querySelector({scope_selector!r} + ' .rank-breakdown-head .chip').textContent")
    assert label == "Sort: route order", label
    served = json.loads(page.evaluate(SERVED_ORDER))
    drawn = json.loads(page.evaluate(DRAWN_ORDER % json.dumps(scope_selector)))
    assert drawn[:20] == served[:20], f"drawn {drawn[:5]}... vs served {served[:5]}..."


def test_both_breakdowns_lead_with_art_and_open_in_route_order(rank_page):
    assert_every_row_leads_with_its_art(rank_page, ".rank-page")
    assert_breakdown_opens_in_served_order(rank_page, ".rank-page")
    click_a_runner_row(rank_page)
    rank_page.wait_for(".runner-page .rank-entity-link", timeout_ms=8000)
    assert_every_row_leads_with_its_art(rank_page, ".runner-page")
    assert_breakdown_opens_in_served_order(rank_page, ".runner-page")


# ---- Round 1, third read (2026-08-23): the art is part of the door, and a
# row with a video plays it beneath itself ---------------------------------

def test_the_entity_art_sits_inside_the_doors_hit_target(rank_page):
    """"The course icon should be clickable as well. It should bring me to
    the exact same position as if I clicked the link normally" -- one
    button holds both, so there is no second click path to keep in sync."""
    click_a_runner_row(rank_page)
    rank_page.wait_for(".runner-page .rank-entity-link", timeout_ms=8000)
    assert rank_page.evaluate(
        "!!document.querySelector('.runner-page .rank-entity-link .rank-row-icon')"), (
        "the entity's art is outside the name's door")


PLAY_COLUMN = """
  JSON.stringify((() => {
    const rows = Array.from(document.querySelectorAll('.runner-page .rank-table tbody tr:not(.rank-video-row)'));
    return {rows: rows.length, plays: rows.filter((row) => row.querySelector('.rank-row-play')).length,
      headers: document.querySelectorAll('.runner-page .rank-table th').length};
  })())
"""
EXPANDED = """
  JSON.stringify((() => {
    const btn = document.querySelector('.runner-page .rank-row-play');
    const videoRow = btn.closest('tr').nextElementSibling;
    const isVideoRow = !!(videoRow && videoRow.classList.contains('rank-video-row'));
    return {isVideoRow, expanded: btn.getAttribute('aria-expanded'),
      player: !!(isVideoRow && videoRow.querySelector('.library-example-media iframe, .library-example-media video, .library-example-media a')),
      openRows: document.querySelectorAll('.runner-page .rank-video-row').length};
  })())
"""


def test_a_row_with_a_video_plays_it_beneath_itself(rank_page):
    """"click on a Play button (like the exact one in the practice log)
    within each row... the furthest right column (if the video exists)...
    the video should appear directly underneath the entry in the row, like
    an expandable dropdown". Same ▶/chevron `icon-button` and the same
    `replay-row` the practice log uses; the player is the Library card's
    own `ExampleMedia`, never a second embed path."""
    click_a_runner_row(rank_page)
    rank_page.wait_for(".runner-page .rank-row-play", timeout_ms=8000)
    column = json.loads(rank_page.evaluate(PLAY_COLUMN))
    assert 0 < column["plays"] < column["rows"], (
        f"expected some rows with a video and some without: {column}")
    assert column["headers"] == 6, column
    rank_page.evaluate("document.querySelector('.runner-page .rank-row-play').click()")
    rank_page.wait_ms(400)
    opened = json.loads(rank_page.evaluate(EXPANDED))
    assert opened["isVideoRow"] and opened["player"] and opened["openRows"] == 1, opened
    assert opened["expanded"] == "true", opened
    rank_page.evaluate("document.querySelector('.runner-page .rank-row-play').click()")
    rank_page.wait_ms(200)
    assert rank_page.count(".runner-page .rank-video-row") == 0, "the video row did not close"


# ---- Round 1, fourth read (2026-08-23): the board is its own closed card at
# the top of the Rank tab, and it folds open and shut ----------------------

@pytest.fixture
def closed_rank_page(server):
    """A fresh Rank tab with the board card left exactly as it loads."""
    with driver.get_driver().launch(headless=True) as page:
        page.goto(f"{server}/ui/index.html")
        page.wait_for(".log-list-card", timeout_ms=20000)
        page.evaluate(CLICK_RANK_TAB)
        page.wait_for(".leaderboard-card-head", timeout_ms=15000)
        page.wait_ms(300)
        yield page


CARD_PLACE = """
  JSON.stringify((() => {
    const page = document.querySelector('.rank-page');
    const kids = Array.from(page.children).map((el) =>
      el.classList.contains('leaderboard-card') ? 'leaderboard-card'
        : el.classList.contains('rank-card') ? 'rank-card' : el.className.split(' ')[0]);
    const card = document.querySelector('.leaderboard-card');
    return {order: kids, expanded: card.querySelector('.leaderboard-card-head').getAttribute('aria-expanded'),
      title: card.querySelector('.leaderboard-card-title').textContent.trim(),
      rows: card.querySelectorAll('.leaderboard-row').length,
      bodyH: card.querySelector('.leaderboard-card-disclose').getBoundingClientRect().height};
  })())
"""


def card_state(page):
    return json.loads(page.evaluate(CARD_PLACE))


def test_the_board_is_a_closed_card_between_the_chips_and_the_marelo_card(closed_rank_page):
    """"it should up top, and it should be a dropdown Titled 'Leaderboard'.
    Closed by default... underneath all of the route selectors... and above
    the MARELO card. It should be its own card." Closed means folded to
    zero height with nothing drawn -- the CARD holds the fetched board so the
    open can measure real rows."""
    state = card_state(closed_rank_page)
    order = state["order"]
    assert order.index("scope-chip-row") < order.index("leaderboard-card") < order.index("rank-card"), order
    assert state["title"] == "Leaderboard"
    assert state["expanded"] == "false" and state["rows"] == 0 and state["bodyH"] == 0, state


def test_the_board_card_animates_open_and_shut(closed_rank_page):
    """"Animate open / closed, reusing our dropdown system" -- the fold is
    `collapsible.js::Disclose`, so the body's height is mid-travel shortly
    after the click and settled later, in both directions."""
    page = closed_rank_page
    # The card fetches while closed so the fold opens onto real rows; give
    # the fetch its moment, then open.
    page.wait_ms(1200)
    def travel_samples():
        # A handful of samples across the run's first ~250ms -- one sample at
        # a fixed offset is a coin flip on where the frame lands.
        samples = []
        for _ in range(5):
            page.wait_ms(50)
            samples.append(card_state(page)["bodyH"])
        return samples
    page.evaluate("document.querySelector('.leaderboard-card-head').click()")
    opening = travel_samples()
    page.wait_ms(600)
    opened = card_state(page)
    assert opened["expanded"] == "true" and opened["rows"] > 20, opened
    assert any(0 < h < opened["bodyH"] for h in opening), (
        f"the open did not travel: {opening} vs {opened['bodyH']} settled")
    page.evaluate("document.querySelector('.leaderboard-card-head').click()")
    closing = travel_samples()
    page.wait_ms(600)
    closed = card_state(page)
    assert closed["expanded"] == "false" and closed["bodyH"] == 0, closed
    assert any(0 < h < opened["bodyH"] for h in closing), (
        f"the close cut instead of folding: {closing}")


# ---- Fifth read (2026-08-23): segments are ignored by default, and his own
# PB's replay plays beneath its row ----------------------------------------

def test_segments_outside_bowser_and_hundred_coin_are_ignored_by_default(closed_rank_page):
    """"by default all segments should be ignored by default (other than
    the Bowser stages / 100C stars)... The user can go in and manually
    include those later". On his own tab an ignored entity is the inert row
    with an Include button; the tricks are among them and the three Bowser
    course entries (sixth read: "just the Bowser Course entries (i.e., No
    Reds)... should be part of the default ranking") are not."""
    page = closed_rank_page
    page.wait_for(".rank-page .rank-table tbody tr", timeout_ms=15000)
    state = json.loads(page.evaluate("""
      JSON.stringify((() => {
        const rows = Array.from(document.querySelectorAll('.rank-page .rank-table tbody tr'));
        const excluded = rows.filter((r) => r.classList.contains('is-excluded'))
          .map((r) => r.querySelector('.rank-cell-name').textContent.trim());
        const includeButtons = rows.filter((r) => Array.from(r.querySelectorAll('button.chip'))
          .some((b) => b.textContent.trim() === 'Include')).length;
        return {excluded, includeButtons};
      })())"""))
    assert any(name in ("LBLJ", "MIPS Clip", "Lakitu Skip") for name in state["excluded"]), state
    assert not any("Pipe Entry" in name for name in state["excluded"]), state
    assert state["includeButtons"] == len(state["excluded"]) > 0, state


def test_his_own_pb_with_a_replay_gets_the_same_play_button(server):
    """"if I have a PB and I've saved a replay for it... it should show the
    same video dropdown option for each row here." The fixture records
    nothing, so `/api/replay/available` is answered in-page with the
    fixture star's PB attempt -- the only way to reach the state without a
    recording -- and the row's ▶ must mount the practice log's own player
    beneath it. With nothing replayable (the real fixture answer) no row
    carries a ▶ at all."""
    with driver.get_driver().launch(headless=True) as page:
        page.goto(f"{server}/ui/index.html")
        page.wait_for(".log-list-card", timeout_ms=20000)
        page.evaluate(CLICK_RANK_TAB)
        page.wait_for(".rank-page .rank-table tbody tr", timeout_ms=15000)
        page.wait_ms(300)
        assert page.count(".rank-page .rank-row-play") == 0, "a ▶ with nothing replayable"
        pb_attempt = page.evaluate(
            "fetch('/api/marelo?scope=overall').then((r) => r.json())"
            ".then((b) => (b.entities.find((e) => e.pb_attempt_id != null) || {}).pb_attempt_id)")
        assert pb_attempt is not None, "the fixture seeded no PB with an attempt id"
        page.evaluate(f"""
          (() => {{
            const real = window.fetch;
            window.fetch = (url, opts) => (typeof url === 'string' && url.endsWith('/api/replay/available'))
              ? Promise.resolve(new Response(JSON.stringify({{available: [{pb_attempt}]}}),
                  {{headers: {{'Content-Type': 'application/json'}}}}))
              : real(url, opts);
          }})()""")
        # Leave and re-enter the tab: RankPage remounts and fetches both the
        # breakdown and the (now answered) replay list afresh.
        page.evaluate(CLICK_PRACTICE_TAB)
        page.wait_ms(200)
        page.evaluate(CLICK_RANK_TAB)
        page.wait_for(".rank-page .rank-table tbody tr", timeout_ms=15000)
        page.wait_ms(500)
        assert page.count(".rank-page .rank-row-play") == 1, "the PB's row carries no ▶"
        page.evaluate("document.querySelector('.rank-page .rank-row-play').click()")
        page.wait_ms(500)
        # The practice log's own player: `.replay-player` once footage is
        # extracted, `.replay-state` while extracting or when (as here, with
        # no recording behind the fixture) there is none to extract.
        assert page.count(".rank-page .rank-video-row .replay-player, "
                          ".rank-page .rank-video-row .replay-state") == 1, (
            "pressing ▶ on his own row did not mount the practice log's player beneath it")


# ---- The fork closed (2026-08-23): his OWN tab's entity names are doors too,
# landing on his PB's subdivision -------------------------------------------

def test_his_own_breakdown_names_open_the_library_on_his_pb_subdivision(closed_rank_page):
    """"should the entity names on your own Rank tab be doors to the Library,
    landing on your own PB's entry" -- yes. Every own row is a door (the
    Library page exists for every entity); a row with a PB lands on the
    subdivision his standing sits in, opened and blinking, on the section he
    is graded on."""
    page = closed_rank_page
    page.wait_for(".rank-page .rank-table tbody tr", timeout_ms=15000)
    page.wait_ms(300)
    doors = json.loads(page.evaluate("""
      JSON.stringify((() => {
        const links = Array.from(document.querySelectorAll('.rank-page .rank-entity-link'));
        return {links: links.length,
          rows: document.querySelectorAll('.rank-page .rank-table tbody tr:not(.rank-video-row)').length,
          pb: links.filter((l) => l.title.includes("PB")).length};
      })())"""))
    assert doors["links"] == doors["rows"] > 0, doors
    assert doors["pb"] >= 1, "the fixture seeded no PB row to land from"
    page.evaluate("""Array.from(document.querySelectorAll('.rank-page .rank-entity-link'))
      .find((l) => l.title.includes("PB")).click()""")
    page.wait_for(".library-target", timeout_ms=8000)
    page.wait_ms(1100)
    landed = json.loads(page.evaluate("""
      JSON.stringify((() => {
        const group = document.querySelector('.library-page .library-section.open .library-division.is-you');
        return {found: !!group, open: !!(group && group.classList.contains('open')),
          blink: !!(group && group.classList.contains('library-arrival'))};
      })())"""))
    assert landed == {"found": True, "open": True, "blink": True}, landed
