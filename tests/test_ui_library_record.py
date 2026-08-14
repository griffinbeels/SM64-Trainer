"""Task 0096 -- the RECORD door: a library row that has no segment yet opens
the SAME recorder the Segments tab does (segmenttimeline.js, one
implementation so the two doors cannot drift), pre-named after the row and
pre-parented to its target's entity, and the save links itself to the row.

Driven end to end against the fixture server: door -> recorder -> two picks
-> Save -> the piece wears the linked chip naming the segment the save just
minted, and /api/segments shows that segment carrying the star as its parent
-- the recording became a [[subsection]] without one manual step beyond
picking the moments.
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

from ui_fixture import FIXTURE_SEGMENT, serve_ui  # noqa: E402
from uilab import driver  # noqa: E402

CLICK_LIBRARY_TAB = 'document.querySelector(\'.nav-item[title="Library"]\').click()'


@pytest.fixture(scope="module")
def library_server():
    with serve_ui(arm_segment=FIXTURE_SEGMENT, seed_editor_fixtures=True) as base:
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


def _wait_until(page, script, timeout_s=15, note=""):
    """Poll a JS expression until it returns something truthy; return it."""
    deadline = time.time() + timeout_s
    result = None
    while time.time() < deadline:
        result = page.evaluate(script)
        if result:
            return result
        time.sleep(0.15)
    raise AssertionError(f"timed out waiting for {note or script}: {result!r}")


def test_an_entity_less_target_offers_record_beside_link(library_page):
    """The whole-target door (round 7) grows the same offer: a movement
    nobody has a segment for can be recorded right there. 'BoB RTA' is the
    entity-less target the link round-trip test already leans on."""
    _navigate_to_target(library_page, "1. Bob-omb Battlefield",
                        "BoB RTA (RTA strat, Fadeout, w/ cannon cutscene)")
    library_page.wait_for(".library-target-titleline .library-link-button",
                          timeout_ms=15000)
    assert library_page.evaluate(
        "document.querySelectorAll("
        "'.library-target-titleline .library-record-button').length") == 1


def test_the_record_door_records_names_parents_and_links_in_one_save(library_page):
    """The whole loop on a piece row, end to end. Three claims, in the order
    they can fail: the recorder opens PRE-NAMED after the row with nothing
    picked (and the prefilled name survives the auto-name across picks); the
    parent pill already names the owning star, so the save mints a
    subsection; and Save alone leaves the piece wearing the linked chip that
    names the new segment -- no menu, no rename, no manual link anywhere."""
    _navigate_to_target(library_page, "1. Bob-omb Battlefield",
                        "Big Bob-omb on the Summit")
    library_page.wait_for(".library-pieces .library-record-button",
                          timeout_ms=15000)
    piece_name = library_page.evaluate("""
      (() => {
        const piece = Array.from(document.querySelectorAll('.library-piece-section'))
          .find((row) => row.querySelector('.library-record-button'));
        if (!piece) return null;
        const name = piece.querySelector('.library-section-name').textContent;
        piece.querySelector('.library-record-button').click();
        return name;
      })()
    """)
    assert piece_name, "no recordable subsection row on Big Bob-omb"

    library_page.wait_for(".record-picks", timeout_ms=15000)
    assert library_page.evaluate(
        "document.querySelectorAll('.record-row.picked').length") == 0

    # Pick the two OLDEST rows (the list draws newest-first) -- the same pair
    # every recorder story picks, for the same reason: any level/star pair
    # the fixture holds synthesizes fine.
    for want in (1, 2):
        _wait_until(library_page,
                    "document.querySelectorAll('.record-row').length > 0",
                    note="recorder rows")
        library_page.evaluate("""
          (() => {
            const rows = Array.from(document.querySelectorAll('.record-row'))
              .filter((row) => !row.classList.contains('picked'));
            if (rows.length) rows[rows.length - 1].click();
          })()
        """)
        _wait_until(library_page,
                    f"document.querySelectorAll('.record-row.picked').length === {want}",
                    note=f"{want} picked rows")

    library_page.wait_for(".record-review", timeout_ms=15000)
    shown = library_page.evaluate(
        "document.querySelector('.record-review .builder-name input').value")
    assert shown == piece_name, (
        f"the recorder shows {shown!r} where the row is named {piece_name!r} "
        "-- the prefill is missing or the auto-name overwrote it")
    parent = library_page.evaluate(
        "document.querySelector('.record-parent .entity-trigger').textContent")
    assert "Big Bob-omb" in parent, (
        f"the parent pill must already name the owning star; it reads {parent!r}")

    # Save arms once the backtest lands; then one click does save + link.
    _wait_until(library_page, """
      (() => {
        const btn = Array.from(document.querySelectorAll(
          '.modal .builder-actions button'))
          .find((b) => b.textContent.includes('Save segment'));
        return !!btn && !btn.disabled;
      })()
    """, note="Save enabled")
    library_page.evaluate("""
      Array.from(document.querySelectorAll('.modal .builder-actions button'))
        .find((b) => b.textContent.includes('Save segment')).click()
    """)
    _wait_until(library_page,
                "!document.querySelector('.record-picks')",
                note="recorder closed")
    library_page.wait_for(".library-pieces .library-link-state.is-linked",
                          timeout_ms=15000)
    linked_text = _wait_until(library_page, """
      (() => {
        const chip = document.querySelector(
          '.library-pieces .library-link-state.is-linked');
        return chip ? chip.textContent : null;
      })()
    """, note="linked chip")
    assert piece_name in linked_text, (
        f"the linked chip must name the auto-named segment: {linked_text!r}")

    # The server-side half: the minted segment carries the star as its
    # parent -- the recording IS a subsection, not a loose segment that
    # happens to share a name.
    base = library_page.evaluate("location.origin")
    segments = json.loads(urllib.request.urlopen(
        f"{base}/api/segments", timeout=10).read())
    mine = [row for row in segments if row.get("name") == piece_name]
    assert mine, f"no segment named {piece_name!r} after the save"
    parents = mine[0].get("parents") or []
    assert any(str(parent).startswith("star:1:") for parent in parents), (
        f"the saved segment must be parented to the BoB star; parents={parents}")
