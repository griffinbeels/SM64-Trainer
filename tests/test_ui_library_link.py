"""Round 5 -- the link door, rendered and CLICKED against the real server.

`library/adoptions.py` and its adopt/unadopt routes shipped with the backend
and had NO UI caller (the audit tool was the only assign surface). His ask,
verbatim: "Need a one click button that opens a menu to select one of the
[segments] for the star/area we're looking at, to link it." These tests drive
the whole loop -- button, menu, adopt, linked chip, unlink -- through the
fixture server, whose adoptions file lives in scratch (ui_fixture passes
`adoptions_path` into `create_app` precisely so this file can click adopt
without writing into the real data dir).
"""
import json
import shutil
import time
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

from ui_fixture import FIXTURE_SEGMENT, serve_ui  # noqa: E402
from uilab import driver  # noqa: E402

CLICK_LIBRARY_TAB = 'document.querySelector(\'.nav-item[title="Library"]\').click()'

# A segment of the test's OWN so the adopt can never collide with a vetted
# strategy an existing corpus segment might already carry ("Standard" is the
# adopted name whenever the approach is named after its target).
LINK_SEGMENT_NAME = "Round Five Link Target"


@pytest.fixture(scope="module")
def library_server():
    with serve_ui(arm_segment=FIXTURE_SEGMENT, seed_editor_fixtures=True) as base:
        request = urllib.request.Request(
            f"{base}/api/segments",
            data=json.dumps({
                "name": LINK_SEGMENT_NAME,
                "start_triggers": [{"type": "level_enter", "to": 16}],
                "waypoints": [],
                "end_triggers": [{"type": "star_grabbed", "star": 0,
                                  "course": 1}],
                "guards": [], "enabled": True, "match_mode": "strict",
            }).encode(),
            headers={"Content-Type": "application/json"}, method="POST")
        urllib.request.urlopen(request, timeout=10).read()
        yield base


@pytest.fixture
def library_page(library_server):
    with driver.get_driver().launch(headless=True) as page:
        page.goto(f"{library_server}/ui/index.html")
        page.wait_for(".log-list-card", timeout_ms=20000)
        page.evaluate(CLICK_LIBRARY_TAB)
        page.wait_for(".library-target .library-section", timeout_ms=15000)
        yield page


def _navigate_to_target(page, group_name, target_label):
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
    page.wait_for(".library-target", timeout_ms=15000)


def test_link_unlink_round_trip_links_every_strategy_at_once(library_page):
    """ROUND 7: the door is target-level, beside the page's own name, and one
    click adopts EVERY laddered approach ("If we link a segment, then it
    should automatically load ALL strategies for that segment"). 'BoB RTA'
    carries TWO laddered approaches and matches no segment by name, so both
    sections showing a standing after ONE link is the bulk proof; Unlink
    reverses the whole batch. Every state read off the real DOM after a real
    server round trip."""
    _navigate_to_target(library_page, "1. Bob-omb Battlefield",
                        "BoB RTA (RTA strat, Fadeout, w/ cannon cutscene)")
    library_page.wait_for(".library-target-titleline .library-link-button",
                          timeout_ms=15000)

    library_page.evaluate(
        "document.querySelector('.library-target-titleline .library-link-button').click()")
    library_page.wait_for(".search-menu", timeout_ms=10000)
    library_page.wait_for(".search-menu-option", timeout_ms=10000)

    picked = library_page.evaluate(f"""
      (() => {{
        const options = Array.from(document.querySelectorAll('.search-menu-option'));
        const mine = options.find((o) => o.textContent === {LINK_SEGMENT_NAME!r});
        if (!mine) return 'no option named ' + {LINK_SEGMENT_NAME!r}
          + ' among ' + options.length;
        mine.click();
        return 'clicked';
      }})()
    """)
    assert picked == "clicked", picked

    library_page.wait_for(".library-target-titleline .library-link-state.is-linked",
                          timeout_ms=15000)
    linked_text = library_page.evaluate(
        "document.querySelector('.library-target-titleline .library-link-state.is-linked').textContent")
    assert LINK_SEGMENT_NAME in linked_text, linked_text
    # the bulk proof: BOTH strategy sections now carry a standing
    library_page.wait_for(".library-section .library-your-standing",
                          timeout_ms=15000)
    standings = library_page.evaluate(
        "document.querySelectorAll('.library-section .library-your-standing').length")
    assert standings >= 2, (
        f"one link must grade EVERY laddered strategy; {standings} standings")

    library_page.evaluate(
        "document.querySelector('.library-unlink').click()")
    library_page.wait_for(".library-target-titleline .library-link-button",
                          timeout_ms=15000)
    assert library_page.evaluate(
        "document.querySelectorAll('.library-link-state.is-linked').length") == 0


def test_a_stars_subsections_render_as_pieces_with_the_link_door(library_page):
    """Subsections rendered NOWHERE before round 5 -- 122 sheet rows the page
    silently dropped. On a star target ('Big Bob-omb on the Summit' carries
    four, all laddered) they are compact Pieces rows, each with the link
    affordance; the star's own strategy sections offer NO link strip (a
    star's approaches auto-adopt at scrape time -- linking them again is
    noise)."""
    _navigate_to_target(library_page, "1. Bob-omb Battlefield",
                        "Big Bob-omb on the Summit")
    library_page.wait_for(".library-pieces", timeout_ms=15000)
    pieces = library_page.evaluate(
        "document.querySelectorAll('.library-pieces .library-piece').length")
    assert pieces >= 4, pieces
    piece_buttons = library_page.evaluate(
        "document.querySelectorAll('.library-pieces .library-link-button').length")
    assert piece_buttons >= 4, piece_buttons
    section_strips = library_page.evaluate(
        "document.querySelectorAll('.library-section .library-link-state').length")
    assert section_strips == 0, (
        f"a star target's strategy sections must not offer the link door; "
        f"found {section_strips}")
    header_doors = library_page.evaluate(
        "document.querySelectorAll('.library-target-titleline .library-link-state,"
        " .library-target-titleline .library-matched-chip').length")
    assert header_doors == 0, (
        f"a star target links nothing -- its approaches auto-adopt; "
        f"found {header_doors} header doors")


def test_a_name_matched_movement_shows_the_association_and_its_standing(library_page):
    """Round 6: "we should autoassign any segments that exist already."
    'Lakitu skip' name-matches the corpus segment 'Lakitu Skip', so its
    sections wear the "= your segment" chip and a standing WITHOUT any click
    -- and with no recorded time on that segment in this fixture, the
    standing is Capless, verbatim his rule ("if there are no times, it's
    capless"). The offer strip is gone: a match already IS the association."""
    _navigate_to_target(library_page, "Castle Movements (Lobby)", "Lakitu skip")
    library_page.wait_for(".library-target-titleline .library-matched-chip",
                          timeout_ms=15000)
    chip = library_page.evaluate(
        "document.querySelector('.library-target-titleline .library-matched-chip').textContent")
    assert "Lakitu Skip" in chip, chip
    library_page.wait_for(".library-section .library-your-standing",
                          timeout_ms=15000)
    standing_icons = library_page.evaluate(
        """Array.from(document.querySelectorAll(
             '.library-section .library-your-standing [data-tier]'))
           .map((icon) => icon.getAttribute('data-tier'))""")
    if not standing_icons:
        standing_icons = library_page.evaluate(
            """Array.from(document.querySelectorAll(
                 '.library-section .library-your-standing'))
               .map((el) => el.textContent)""")
    assert standing_icons, "no standing rendered on a matched movement"
    offers = library_page.evaluate(
        "document.querySelectorAll('.library-target .library-link-button').length")
    assert offers == 0, (
        f"a name-matched target must not also offer the link door; {offers}")


def test_linking_shows_a_standing_immediately(library_page):
    """Round 6 item 1, verbatim: "the second we link it, it reads whatever
    the data was for that segment ... if there are no times, it's capless."
    The freshly built link-target segment has no attempts, so the instant the
    link lands the section shows a Capless standing with no further clicks."""
    _navigate_to_target(library_page, "Castle Movements (Lobby)",
                        "Lobby door (L) - CCM wooden door")
    library_page.wait_for(".library-target-titleline .library-link-button",
                          timeout_ms=15000)
    library_page.evaluate(
        "document.querySelector('.library-target-titleline .library-link-button').click()")
    library_page.wait_for(".search-menu-option", timeout_ms=10000)
    library_page.evaluate(f"""Array.from(document.querySelectorAll('.search-menu-option'))
      .find((o) => o.textContent === {LINK_SEGMENT_NAME!r}).click()""")
    library_page.wait_for(".library-target-titleline .library-link-state.is-linked",
                          timeout_ms=15000)
    library_page.wait_for(".library-section.open .library-your-standing",
                          timeout_ms=15000)
    standing = library_page.evaluate(
        "document.querySelector('.library-section.open .library-your-standing').textContent")
    assert "no times yet" in standing, standing
    # leave the fixture as found for the other tests
    library_page.evaluate("document.querySelector('.library-unlink').click()")
    library_page.wait_for(".library-target-titleline .library-link-button",
                          timeout_ms=15000)


def test_the_segment_editor_links_from_the_other_side(library_page, library_server):
    """ROUND 8: "when we *create* a segment, we should be able to link it to
    a pre-existing segment in the library. In both scenarios ... we should
    be able to link the two together." The editor's Sheet-library select
    drives the SAME adopt_target door: picking a target links it, the
    Library page then wears the linked chip for that target, and picking
    "Not linked" clears it."""
    import urllib.request as request_lib

    def api(path):
        with request_lib.urlopen(f"{library_server}{path}", timeout=10) as response:
            return json.loads(response.read())

    index = api("/api/library")
    wanted = next(
        target for group in index["groups"] for target in group["targets"]
        if target["label"] == "Lobby door (L) - CCM wooden door")
    segment = next(row for row in api("/api/segments")
                   if row["name"] == LINK_SEGMENT_NAME)

    library_page.evaluate('document.querySelector(\'.nav-item[title="Segments"]\').click()')
    library_page.wait_for(".segments-page .library-search", timeout_ms=15000)
    library_page.evaluate(f"""(() => {{
      const box = document.querySelector('.segments-page .library-search');
      const setter = Object.getOwnPropertyDescriptor(
        window.HTMLInputElement.prototype, 'value').set;
      setter.call(box, {LINK_SEGMENT_NAME!r});
      box.dispatchEvent(new Event('input', {{ bubbles: true }}));
    }})()""")
    library_page.wait_for(".segment-row-main", timeout_ms=15000)
    library_page.evaluate("document.querySelector('.segment-row-main').click()")
    library_page.wait_for(".builder-liblink .search-select-trigger",
                          timeout_ms=15000)

    # Round 9: the row is the shared filterable popup now -- open it, type
    # into the filter, click the one option left standing.
    library_page.evaluate(
        "document.querySelector('.builder-liblink .search-select-trigger').click()")
    library_page.wait_for(".builder-liblink .search-menu-filter", timeout_ms=10000)
    library_page.evaluate(f"""(() => {{
      const box = document.querySelector('.builder-liblink .search-menu-filter');
      const setter = Object.getOwnPropertyDescriptor(
        window.HTMLInputElement.prototype, 'value').set;
      setter.call(box, {wanted["label"]!r});
      box.dispatchEvent(new Event('input', {{ bubbles: true }}));
    }})()""")
    picked_option = library_page.evaluate(f"""(() => {{
      const options = Array.from(document.querySelectorAll(
        '.builder-liblink .search-menu-option'));
      const mine = options.find((o) => o.textContent === {wanted["label"]!r});
      if (!mine) return 'no option among ' + options.length;
      mine.click();
      return 'clicked';
    }})()""")
    assert picked_option == "clicked", picked_option
    deadline = time.time() + 15
    while time.time() < deadline:
        linked = api("/api/library/adoptions")["by_entity"].get(
            f"segment:{segment['id']}", [])
        if linked:
            break
        time.sleep(0.3)
    assert linked and linked[0]["index"] == wanted["index"], linked

    # the OTHER door now shows it: the Library target wears the linked chip
    library_page.evaluate('document.querySelector(\'.nav-item[title="Library"]\').click()')
    library_page.wait_for(".library-target", timeout_ms=15000)
    _navigate_to_target(library_page, "Castle Movements (Lobby)",
                        "Lobby door (L) - CCM wooden door")
    library_page.wait_for(".library-target-titleline .library-link-state.is-linked",
                          timeout_ms=15000)
    linked_text = library_page.evaluate(
        "document.querySelector('.library-target-titleline .library-link-state.is-linked').textContent")
    assert LINK_SEGMENT_NAME in linked_text, linked_text

    # leave the fixture as found
    library_page.evaluate(
        "document.querySelector('.library-unlink').click()")
    library_page.wait_for(".library-target-titleline .library-link-button",
                          timeout_ms=15000)
