"""Every import door, RENDERED.

Unit tests plus `node --check` shipped an invisible feature in this project
once, so a UI change is not verified until the page draws it. These were
mutation-proved by pointing their mount at nothing and watching them go red.

Deliberately NOT tested here: the live sheet download. It is 7 MB over the
network and belongs to a document nobody here controls — the failure path is
covered by `tests/test_import_api.py`, which fakes the three ways a fetch can
fail, and the snapshot path is covered there end to end. The LINK door's fetch
is replaced in the SERVER instead (the fixture runs in-process), so what is
driven there is the panel rather than Google.
"""
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))

from ui_fixture import serve_ui  # noqa: E402
from find_uilab import find_uilab  # noqa: E402

from source_scan import strip_comments  # noqa: E402

_MISSING = find_uilab()
if _MISSING:
    pytest.skip(_MISSING, allow_module_level=True)

from uilab import driver  # noqa: E402

SETTLE = "new Promise(r => setTimeout(r, MS))"

WAIT = """
(async () => {
  const until = Date.now() + 15000;
  while (Date.now() < until) {
    if (document.querySelector('SEL')) return true;
    await new Promise((r) => setTimeout(r, 50));
  }
  return false;
})()
"""

# The four doors live behind one chip row now, and nothing is open by default:
# four stacked panels pushed Display and Sessions most of a drawer away.
OPEN_DOOR = """
(() => {
  const chip = [...document.querySelectorAll('.importsection-doors .chip')]
    .find((candidate) => candidate.textContent.trim() === 'LABEL');
  if (chip) chip.click();
  return !!chip;
})()
"""

OPEN_SETTINGS = """
(() => {
  const button = [...document.querySelectorAll('button')].find(
    (candidate) => /settings/i.test(
      (candidate.getAttribute('aria-label') || '') + ' '
      + (candidate.title || '') + ' ' + candidate.textContent));
  if (button) button.click();
  return !!button;
})()
"""

# One box per tick: Preact commits after the tick, so writing two in one
# evaluate hands the second handler the state from before the first commit
# (`.claude/rules/ui-core.md`, and TimeFields' own docstring).
SET_BOX = """
(() => {
  const box = document.querySelectorAll('.addtime-body .timefield')[IDX];
  if (!box) return false;
  const setter = Object.getOwnPropertyDescriptor(
    window.HTMLInputElement.prototype, 'value').set;
  setter.call(box, 'VAL');
  box.dispatchEvent(new Event('input', {bubbles: true}));
  box.dispatchEvent(new FocusEvent('blur', {bubbles: false}));
  return true;
})()
"""


def wait(page, selector):
    return page.evaluate(WAIT.replace("SEL", selector))


def open_door(page, label):
    """Click the chip naming one import door, and confirm it was there."""
    assert page.evaluate(OPEN_DOOR.replace("LABEL", label)), (
        f"no import door called {label!r} — the chip row names the ways in, "
        "so a missing one is a door nobody can reach")


def settle(page, ms=300):
    page.evaluate(SETTLE.replace("MS", str(ms)))


def test_a_time_typed_by_hand_becomes_the_cards_personal_best(tmp_path):
    """The whole chain: the control draws, the field snaps to a displayable
    centisecond, the save lands, the card's PB is the imported one, and the
    time is a row in the card's own log."""
    with serve_ui(tmp_path / "addtime.db") as base:
        with driver.get_driver().launch(headless=True) as page:
            page.goto(base)
            assert wait(page, ".practice-page")
            settle(page, 2000)

            assert page.evaluate(
                "(() => { const b = document.querySelector('.addtime-open');"
                " if (b) b.click(); return !!b; })()"), (
                "no add-a-time control on the practice log card — the door "
                "this feature exists to open is not on the page")
            assert wait(page, ".addtime-body")
            settle(page)

            # 10.01 is a centisecond the timer can NEVER display (only 30 of
            # every 100 can be), so this exercises the snap rather than
            # assuming it.
            for index, value in ((0, "0"), (1, "10"), (2, "01")):
                if index == 2:
                    # Committing the seconds box must NOT fill this one in:
                    # "i typed '11' into the middle box, and then clicked into
                    # the right box, which autofilled '00'... It shouldn't
                    # prefill any text there" (2026-08-22). The echo of the
                    # control's own commit used to re-pad every box.
                    untouched = page.evaluate(
                        "document.querySelectorAll('.addtime-body .timefield')"
                        "[2].value")
                    assert untouched == "", (
                        f"the centis box was prefilled with {untouched!r} "
                        "after committing the seconds box")
                page.evaluate(
                    SET_BOX.replace("IDX", str(index)).replace("VAL", value))
                settle(page, 150)

            snap = page.evaluate(
                "(() => { const s = document.querySelector('.addtime-snap');"
                " return s ? s.textContent.replace(/\\s+/g,' ').trim() : null;"
                " })()")
            assert snap and '10"03' in snap, (
                f"the field must show what it will actually save; got {snap!r}")

            page.evaluate("document.querySelector('.addtime-save').click()")
            settle(page, 1500)
            status = page.evaluate(
                "(() => { const s = document.querySelector('.addtime-status');"
                " return s ? {text: s.textContent.trim(),"
                " cls: s.className} : null; })()")
            assert status and "is-ok" in status["cls"], (
                f"the save reported {status!r}")

            landed = page.evaluate("""
              (() => {
                const el = document.querySelector('.log-card-pb, .pbtag, .log-card');
                return el ? el.textContent : '';
              })()
            """)
            assert '0\'10"03' in landed, (
                "the card does not show the time that was just saved — an "
                "import that lands on a screen nobody sees did not happen")

            # And it is a ROW in the card's log, not only a number in its
            # head: "It should show the new entry in the practice log as an
            # entry row. This is because it then affords us all of the
            # functionality of a practice log entry row (deleting, undoing,
            # etc)" (2026-08-22).
            rows = page.evaluate(
                "[...document.querySelectorAll('.attempt-table tr')]"
                ".map((row) => row.textContent.replace(/\\s+/g, ' ').trim())")
            assert any('10"03' in row for row in rows), (
                f"the saved time is not an attempt row in the log; rows: {rows}")


def test_the_control_is_gated_on_the_KIND_not_on_course_id():
    """A segment id means nothing outside the database that assigned it and
    segments are RTA-only, so the server refuses one — which makes a control
    on a segment card DEAD, with its reason nowhere near the click.

    A source scan rather than a render, and the reason is worth stating: the
    obvious render test cannot fail. The two guards differ only on a segment
    that ORIGINATES IN A COURSE (`views.py` stamps `origin_course`), and every
    segment the fixture seeds is a castle movement, whose `course_id` is null
    — so `course_id != null` and `!isSegment(sec)` agree on every card the rig
    can draw. Mutation-proved that way: putting the bug back left the render
    version green.

    Probed in both directions below, per `tests/source_scan.py`.
    """
    source = (REPO / "src" / "sm64_events" / "ui" / "components"
              / "practicelog.js").read_text(encoding="utf-8")
    assert _gates_on_kind(source), (
        "the add-a-time mount is not gated on isSegment — a segment card will "
        "draw a control whose every save comes back 422")


def _gates_on_kind(source: str) -> bool:
    """True when the AddTime mount tests the entity KIND.

    Expressed as a function of source text so the probe below can feed it a
    comment-only sample and a real-code sample."""
    code = strip_comments(source)
    mount = [line for line in code.splitlines() if "${AddTime}" in line]
    return bool(mount) and all("isSegment(sec)" in line for line in mount)


def test_the_kind_guard_can_still_fail():
    """The guard's own calibration: a comment naming isSegment must not
    satisfy it, and the course_id form must not pass."""
    assert not _gates_on_kind(
        "// gated on !isSegment(sec) so segments get no ${AddTime}\n"
        "${sec.course_id != null && html`<${AddTime} />`}")
    assert _gates_on_kind("${!isSegment(sec) && html`<${AddTime} />`}")
    assert not _gates_on_kind("${sec.course_id != null && html`<${AddTime} />`}")


def test_the_sheet_picker_fills_from_the_bundled_snapshot(tmp_path):
    """The names come from the shipped snapshot precisely so the list is
    there the moment the drawer opens — no network, no wait."""
    with serve_ui(tmp_path / "importsheet.db") as base:
        with driver.get_driver().launch(headless=True) as page:
            page.goto(base)
            assert wait(page, ".practice-page")
            settle(page, 1500)
            assert page.evaluate(OPEN_SETTINGS)
            assert wait(page, ".importsection")
            open_door(page, "Ultimate Sheet")
            assert wait(page, ".importsheet"), (
                "no import panel in the settings drawer")
            settle(page, 800)

            page.evaluate("document.querySelector("
                          "'.importsheet .search-select-trigger').click()")
            assert wait(page, ".importsheet .search-menu")
            settle(page)
            menu = page.evaluate("""
              (() => {
                const m = document.querySelector('.importsheet .search-menu');
                return {options: m.querySelectorAll('.search-menu-option').length,
                        hasFilter: !!m.querySelector('.search-menu-filter'),
                        hasDentorious: [...m.querySelectorAll('.search-menu-option')]
                          .some((o) => o.textContent.trim() === 'DentoriousRed')};
              })()
            """)
            assert menu["options"] > 400, (
                f"the runner list came back with {menu['options']} names — it "
                "is not reading the bundled snapshot")
            assert menu["hasFilter"], (
                "448 names with no filter box is the shape he ruled against")
            assert menu["hasDentorious"], (
                "the runner this feature was built for is not in the list")


PASTE_BLOCK = (
    "# my golds\\n"
    "BoB 1\\t0:23.57\\n"
    "WF 6, 8.86, LJ\\n"
    "Sneaky Chungus Skip\\t12.00\\n")

TYPE_BLOCK = """
(() => {
  const box = document.querySelector('.importpaste-box');
  if (!box) return false;
  const setter = Object.getOwnPropertyDescriptor(
    window.HTMLTextAreaElement.prototype, 'value').set;
  setter.call(box, "BLOCK");
  box.dispatchEvent(new Event('input', {bubbles: true}));
  return true;
})()
"""


def test_the_paste_door_previews_without_writing_anything(tmp_path):
    """A few hundred lines is where a silent misread is expensive, so the
    panel says what WOULD land before it lands any of it — and the preview
    runs the same planner the button then performs."""
    with serve_ui(tmp_path / "paste.db") as base:
        with driver.get_driver().launch(headless=True) as page:
            page.goto(base)
            assert wait(page, ".practice-page")
            settle(page, 1500)
            assert page.evaluate(OPEN_SETTINGS)
            assert wait(page, ".importsection")
            open_door(page, "Paste a list")
            assert wait(page, ".importpaste"), "no paste panel in the drawer"
            settle(page, 500)
            assert page.evaluate(TYPE_BLOCK.replace("BLOCK", PASTE_BLOCK))
            settle(page, 300)

            before = _star_pb_count(base)
            page.evaluate(
                "document.querySelector('.importpaste .primary-button').click()")
            settle(page, 1500)
            state = page.evaluate("""
              (() => {
                const s = document.querySelector('.importpaste');
                return {button: s.querySelector('.primary-button').textContent.trim(),
                        rejects: s.querySelectorAll('.importdoor-rejects li').length};
              })()
            """)
            assert state["button"].startswith("Import"), state
            assert state["rejects"] == 1, state
            assert _star_pb_count(base) == before, (
                "checking a block WROTE something — the preview is the whole "
                "reason to trust the button")


def test_a_line_it_could_not_read_keeps_its_number_and_its_text(tmp_path):
    """He has to be able to FIND the line to fix it. A count would not do."""
    with serve_ui(tmp_path / "pasterejects.db") as base:
        with driver.get_driver().launch(headless=True) as page:
            page.goto(base)
            assert wait(page, ".practice-page")
            settle(page, 1500)
            assert page.evaluate(OPEN_SETTINGS)
            assert wait(page, ".importsection")
            open_door(page, "Paste a list")
            assert wait(page, ".importpaste")
            settle(page, 500)
            page.evaluate(TYPE_BLOCK.replace("BLOCK", PASTE_BLOCK))
            settle(page, 300)
            page.evaluate(
                "document.querySelector('.importpaste .primary-button').click()")
            settle(page, 1500)
            row = page.evaluate("""
              (() => {
                const li = document.querySelector('.importdoor-rejects li');
                if (!li) return null;
                return {number: li.querySelector('.importdoor-lineno').textContent,
                        text: li.querySelector('code').textContent,
                        reason: li.querySelector('.meta').textContent};
              })()
            """)
            assert row, "the rejected line is not drawn at all"
            assert row["number"] == "4", row
            assert "Sneaky Chungus Skip" in row["text"], row
            assert row["reason"].strip(), "no reason given for the rejection"


def test_a_livesplit_file_previews_and_names_what_did_not_land(tmp_path):
    """The file picker is the one control script cannot set, so this is the
    only way to reach the feature at all — and it exists because a gold is a
    real-time split, which is why a star among them has to be named back."""
    splits = tmp_path / "sample.lss"
    with serve_ui(tmp_path / "livesplit.db") as base:
        import json
        import urllib.request
        with urllib.request.urlopen(f"{base}/api/segments") as reply:
            rows = json.loads(reply.read())
        names = [row["name"] for row in
                 (rows if isinstance(rows, list)
                  else rows.get("segments", []))][:2]
        segments = "".join(
            f"<Segment><Name>{name}</Name><BestSegmentTime>"
            f"<RealTime>00:00:{10 + index:02d}.5000000</RealTime>"
            "</BestSegmentTime></Segment>"
            for index, name in enumerate(names))
        splits.write_text(
            '<?xml version="1.0" encoding="UTF-8"?><Run version="1.7.0">'
            f"<Segments>{segments}"
            "<Segment><Name>BoB 1</Name><BestSegmentTime>"
            "<RealTime>00:00:23.5700000</RealTime></BestSegmentTime>"
            "</Segment></Segments></Run>", encoding="utf-8")

        with driver.get_driver().launch(headless=True) as page:
            page.goto(base)
            assert wait(page, ".practice-page")
            settle(page, 1500)
            assert page.evaluate(OPEN_SETTINGS)
            assert wait(page, ".importsection")
            open_door(page, "LiveSplit file")
            assert wait(page, ".importlivesplit"), "no LiveSplit panel"
            settle(page, 500)
            before = _segment_pb_count(base)

            page.set_input_files(".importlivesplit-file", str(splits))
            settle(page, 1500)
            state = page.evaluate("""
              (() => {
                const s = document.querySelector('.importlivesplit');
                return {button: (s.querySelector('.primary-button')||{}).textContent,
                        rejects: [...s.querySelectorAll('.importdoor-rejects li')]
                          .map((li) => li.querySelector('code').textContent)};
              })()
            """)
            assert state["button"].strip() == "Import 2", state
            assert state["rejects"] == ["BoB 1"], state
            assert _segment_pb_count(base) == before, (
                "picking a file WROTE something — it must preview first")

            page.evaluate("document.querySelector("
                          "'.importlivesplit .primary-button').click()")
            settle(page, 1500)
            assert _segment_pb_count(base) == before + 2, (
                "the golds did not reach the practice log — a segment best "
                "with no attempt behind it has to earn a card, same as a star")


def test_a_sheet_link_reads_previews_and_names_the_rows_that_did_not_land(
        tmp_path, monkeypatch):
    """The fetch is replaced in the SERVER (the fixture runs in-process), so
    what is driven here is the panel rather than Google."""
    from library_fixture import build_workbook

    import sm64_events.server.import_api as import_api
    personal = build_workbook({
        "Times": {
            (1, 1): {"text": "Star"}, (1, 2): {"text": "Time"},
            (2, 1): {"text": "BoB 1"}, (2, 2): {"text": "0:23.57"},
            (3, 1): {"text": "WF 6"}, (3, 2): {"text": "8.86"},
            (5, 1): {"text": "Chungus Skip"}, (5, 2): {"text": "12.00"},
        },
    })
    monkeypatch.setattr(import_api, "_fetch_bytes", lambda _url: personal)

    with serve_ui(tmp_path / "link.db") as base:
        with driver.get_driver().launch(headless=True) as page:
            page.goto(base)
            assert wait(page, ".practice-page")
            settle(page, 1500)
            assert page.evaluate(OPEN_SETTINGS)
            assert wait(page, ".importsection")
            open_door(page, "My own sheet")
            assert wait(page, ".importlink"), "no sheet-link panel"
            settle(page, 500)
            before = _star_pb_count(base)

            page.evaluate("""
              (() => {
                const box = document.querySelector('.importlink-url');
                const setter = Object.getOwnPropertyDescriptor(
                  window.HTMLInputElement.prototype, 'value').set;
                setter.call(box,
                  "https://docs.google.com/spreadsheets/d/1J20aivGnvLlAuyRIMM/edit");
                box.dispatchEvent(new Event('input', {bubbles: true}));
              })()
            """)
            settle(page, 300)
            page.evaluate(
                "document.querySelector('.importlink .primary-button').click()")
            settle(page, 1800)
            state = page.evaluate("""
              (() => {
                const s = document.querySelector('.importlink');
                return {button: s.querySelector('.primary-button').textContent.trim(),
                        rejects: [...s.querySelectorAll('.importdoor-rejects li')]
                          .map((li) => li.querySelector('code').textContent)};
              })()
            """)
            assert state["button"] == "Import 2", state
            assert _star_pb_count(base) == before, (
                "reading the sheet WROTE something — it must preview first")
            # A row that could not be read names its TAB and its ROW: "row 5
            # of Times" is advice somebody can follow.
            assert any(text.startswith("Times!5:") for text in state["rejects"]), \
                state

            page.evaluate(
                "document.querySelector('.importlink .primary-button').click()")
            settle(page, 1800)
            assert _star_pb_count(base) == before + 2


def _segment_pb_count(base):
    import json
    import urllib.request
    with urllib.request.urlopen(f"{base}/api/session?scope=lifetime") as reply:
        view = json.loads(reply.read())
    return sum(1 for s in view["segments"] if (s.get("pb") or {}).get("rta"))


def _star_pb_count(base):
    import json
    import urllib.request
    with urllib.request.urlopen(f"{base}/api/session?scope=lifetime") as reply:
        view = json.loads(reply.read())
    return sum(1 for s in view["stars"] if (s.get("pb") or {}).get("igt"))


def test_the_import_section_sits_above_display_and_stays_one_section(tmp_path):
    """A one-off setup gesture a new arrival makes on their first day must not
    be below every tuning link in the drawer — and the four doors must stay
    ONE section, because as four they pushed Display and Sessions most of a
    drawer away. A control you have to scroll to hunt for gets redesigned."""
    with serve_ui(tmp_path / "importplace.db") as base:
        with driver.get_driver().launch(headless=True) as page:
            page.goto(base)
            assert wait(page, ".practice-page")
            settle(page, 1500)
            assert page.evaluate(OPEN_SETTINGS)
            assert wait(page, ".importsection")
            settle(page, 400)
            order = page.evaluate("""
              (() => {
                const drawer = document.querySelector('.settings-drawer');
                const sections = [...drawer.querySelectorAll('.settings-section')];
                const heads = sections.map(
                  (s) => (s.querySelector('h3') || {}).textContent || '');
                return {import: heads.findIndex((h) => /Bring in times/.test(h)),
                        display: heads.findIndex((h) => /^Display$/.test(h)),
                        sections: document.querySelectorAll(
                          '.settings-drawer .importsection').length,
                        doors: document.querySelectorAll(
                          '.importsection-doors .chip').length,
                        openDoors: document.querySelectorAll(
                          '.importsection .importdoor').length};
              })()
            """)
            assert order["import"] >= 0 and order["display"] >= 0, order
            assert order["import"] < order["display"], (
                "the import section sank below Display and its tuning links")
            assert order["sections"] == 1, (
                f"the doors have gone back to separate sections: {order}")
            assert order["doors"] == 4, (
                f"a door is missing from the chip row: {order}")
            assert order["openDoors"] == 0, (
                "a door is open before anything was picked — the resting "
                "state has to be one heading and one row of chips, or the "
                "drawer is long again")
