"""Every import door, RENDERED.

Unit tests plus `node --check` shipped an invisible feature in this project
once, so a UI change is not verified until the page draws it. These were
mutation-proved by pointing their mount at nothing and watching them go red.

Deliberately NOT tested here: the live sheet download. It is 7 MB over the
network and belongs to a document nobody here controls — the failure path is
covered by `tests/test_import_api.py`, which fakes the three ways a fetch can
fail, and the snapshot path is covered there end to end. The sheet door's
download is replaced in the SERVER instead (the fixture runs in-process,
`LibraryStore.refresh`), so what is driven here is the panel rather than
Google.
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

# The doors live behind one chip row, and nothing is open by default: as
# stacked panels they pushed Display and Sessions most of a drawer away.
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


def test_the_sheet_door_lists_every_held_row_by_name_under_its_reason(
        tmp_path, monkeypatch):
    """Round 3 (2026-08-23): "it makes more sense to just show all the things
    that failed as a list". The tally it replaced said "3 rows can't be used"
    and then "23 no_entity" -- counting KINDS where the sentence promised ROWS.
    Round 28: those rows are HELD rather than dropped -- kept aside until a
    link gives them a home -- so the box is not red, its sentence says so,
    and the same reason groups tell him which link to make.

    The live download is replaced in the SERVER (the fixture runs in-process),
    so the door reads the bundled snapshot and what is driven is the panel.
    GTM is the runner he reported on: 33 dropped rows before his Bowser
    correction landed, 25 after it (the 8 Bowser rows now import onto the
    seeded movements), in two reasons."""
    from sm64_events.library.store import LibraryStore
    monkeypatch.setattr(LibraryStore, "refresh",
                        lambda self, fetch_fn, overrides=None, step=None: {})

    from ui_fixture import serve_ui_live, _run_coro
    with serve_ui_live(tmp_path / "sheetdoor.db") as (base, service):
        # Deliberate deletions keep the held-row workflow reachable now that
        # missing Sheet entries are provisioned automatically on startup.
        for definition in service.db.segment_defs():
            if definition.get("category") == "Ultimate Sheet":
                service.db.delete_segment_def(definition["id"])
        _run_coro(service._segments_changed())
        with driver.get_driver().launch(headless=True) as page:
            page.goto(base)
            assert wait(page, ".practice-page")
            settle(page, 1500)
            assert page.evaluate(OPEN_SETTINGS)
            assert wait(page, ".importsection")
            open_door(page, "Ultimate Sheet")
            assert wait(page, ".importsheet .search-select-trigger")
            settle(page, 500)
            page.evaluate("document.querySelector("
                          "'.importsheet .search-select-trigger').click()")
            assert wait(page, ".importsheet .search-menu")
            settle(page)
            assert page.evaluate("""
              (() => {
                const pick = [...document.querySelectorAll(
                  '.importsheet .search-menu-option')]
                  .find((o) => o.textContent.trim() === 'GTM');
                if (pick) pick.click();
                return !!pick;
              })()
            """), "GTM is not in the runner list"
            settle(page, 300)
            segment_pbs_before = _segment_pb_count(base)
            page.evaluate(
                "document.querySelector('.importsheet .primary-button').click()")
            assert wait(page, ".importsheet .importdoor-held")
            settle(page, 500)
            drawn = page.evaluate("""
              (() => {
                const box = document.querySelector('.importsheet .importdoor-held');
                return {
                  heading: box.querySelector('.settings-note').textContent.trim(),
                  red: !!box.querySelector('.settings-note.is-bad'),
                  summary: document.querySelector(
                    '.importsheet .importdoor-summary').textContent.trim(),
                  groups: [...box.querySelectorAll('.importdoor-reject-group')].map(
                    (g) => ({reason: g.querySelector('.importdoor-reject-reason')
                                        .textContent.trim(),
                             rows: [...g.querySelectorAll('li code')]
                                     .map((c) => c.textContent)})),
                };
              })()
            """)
            assert drawn["heading"].startswith("24 rows kept aside"), drawn
            assert not drawn["red"], "a held row is not a failure"
            assert "24 kept aside" in drawn["summary"], drawn
            assert [g["reason"] for g in drawn["groups"]] == [
                "rows timing part of a star rather than the star (2)",
                "rows the trainer has no target for (22)"], drawn
            assert drawn["groups"][0]["rows"] == [
                "Hot-Foot-It into the Volcano — Inside the volcano — 0'08\"53",
                "Hot-Foot-It into the Volcano — Volcano entry — 0'08\"06"], drawn
            assert len(drawn["groups"][1]["rows"]) == 22, drawn
            # A CLASS of row, not one by position. This group is the sheet's
            # castle-movement rows, and the sheet is edited daily: between
            # the 2026-08-10 and 2026-09-01 snapshots one row was renamed
            # ("+ low dive -> SJ ending" became "+ full dive -> LJ ending")
            # and the order moved, so pinning `rows[0]` by name failed on a
            # change that dropped and gained nothing. Same reasoning as the
            # library's own count FLOORS (`.claude/rules/library.md`).
            assert sum("door" in row for row in drawn["groups"][1]["rows"]) >= 5, drawn
            assert all(" — " in row for row in drawn["groups"][1]["rows"]), drawn
            # His Bowser correction: the seeded movements took their rows --
            # none sits in the list -- and "Lakitu skip" name-matched the
            # seeded Lakitu Skip. Segment PBs on the page grew by the six
            # GTM has times for (BitFS/BitS No Reds, Bowser 1/2/3, Lakitu;
            # he has no BitDW Course row).
            assert not any("Bowser" in row or row.startswith("Lakitu")
                           for g in drawn["groups"] for row in g["rows"]), drawn
            assert _segment_pb_count(base) == segment_pbs_before + 6


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
    be below every tuning link in the drawer — and the doors must stay ONE
    section, because as stacked panels they pushed Display and Sessions most
    of a drawer away. A control you have to scroll to hunt for gets
    redesigned."""
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
            # ONE door since round 3 (2026-08-23): "Paste a list" and
            # "LiveSplit file" went in round 2 ("too difficult to get quite
            # right... we'll spend too much time getting distracted here")
            # and "My own sheet" in round 3 ("too much for us to handle, we
            # need to just get the Ultimate Sheet parsing as good as
            # possible"). The other way in is the box on a star's card.
            assert order["doors"] == 1, (
                f"the chip row does not hold exactly the one door: {order}")
            assert order["openDoors"] == 0, (
                "a door is open before anything was picked — the resting "
                "state has to be one heading and one row of chips, or the "
                "drawer is long again")


def test_the_sheet_import_narrates_its_steps_on_a_progress_line(tmp_path, monkeypatch):
    """ROUND 29 item 4, his words: "we should have a similar progress bar,
    like the one we made for the copy sheet column button. I want to see my
    progress as it's happening, otherwise it feels laggy and unresponsive."

    The download is replaced in the SERVER with a slow fake that reports the
    refresh's own three steps (the real one takes 10-15 s on a ~5.6 MB
    sheet; half a second here is that shape, compressed). The line under
    the button must show MORE THAN ONE distinct sentence during the import
    -- a single "Importing..." would tell him nothing about which step he
    is on -- its fill must move, and the outcome must still land under it."""
    import time

    from sm64_events.library.store import LibraryStore

    def slow_refresh(self, fetch_fn, overrides=None, step=None):
        for fraction, message in ((0.05, "Downloading the current sheet…"),
                                  (0.45, "Building the library from the sheet's rows…"),
                                  (0.7, "Fitting the rank ladders…")):
            if step:
                step(fraction, message)
            time.sleep(0.25)
        return {}

    monkeypatch.setattr(LibraryStore, "refresh", slow_refresh)

    with serve_ui(tmp_path / "sheetprogress.db") as base:
        with driver.get_driver().launch(headless=True) as page:
            page.goto(base)
            assert wait(page, ".practice-page")
            settle(page, 1500)
            assert page.evaluate(OPEN_SETTINGS)
            assert wait(page, ".importsection")
            open_door(page, "Ultimate Sheet")
            assert wait(page, ".importsheet .search-select-trigger")
            settle(page, 500)
            page.evaluate("document.querySelector("
                          "'.importsheet .search-select-trigger').click()")
            assert wait(page, ".importsheet .search-menu")
            settle(page)
            assert page.evaluate("""
              (() => {
                const pick = [...document.querySelectorAll(
                  '.importsheet .search-menu-option')]
                  .find((o) => o.textContent.trim() === 'DentoriousRed');
                if (pick) pick.click();
                return !!pick;
              })()
            """), "DentoriousRed is not in the runner list"
            settle(page, 300)

            # Sample every distinct sentence and fill width the line shows
            # while the import runs -- the steps are the point.
            page.evaluate("""
              (() => {
                window.__steps = [];
                window.__fills = [];
                const read = () => {
                  const el = document.querySelector('.importsheet .job-status');
                  if (!el) return;
                  const text = el.textContent.trim();
                  if (text && window.__steps[window.__steps.length - 1] !== text) {
                    window.__steps.push(text);
                  }
                  const fill = el.querySelector('.job-status-fill').style.width;
                  if (window.__fills[window.__fills.length - 1] !== fill) {
                    window.__fills.push(fill);
                  }
                };
                window.__stepTimer = setInterval(read, 30);
              })()
            """)
            page.evaluate(
                "document.querySelector('.importsheet .primary-button').click()")
            assert wait(page, ".importsheet .importdoor-summary")
            settle(page, 300)
            page.evaluate("clearInterval(window.__stepTimer)")
            steps = page.evaluate("window.__steps")
            fills = page.evaluate("window.__fills")
            summary = page.evaluate(
                "document.querySelector('.importsheet .importdoor-summary')"
                ".textContent.trim()")
            line_after = page.count(".importsheet .job-status")

    assert len(steps) >= 2, f"the line showed one sentence for the whole import: {steps}"
    # The server's own step sentences, in the server's order -- the fake
    # refresh reports three, and the matching/landing steps after it run in
    # a few ms on the snapshot, so a 30 ms sampler may or may not catch them.
    assert "Downloading" in steps[0], steps
    assert any("Building" in step or "Fitting" in step for step in steps), steps
    widths = [int(fill.rstrip("%")) for fill in fills if fill]
    assert len(widths) >= 2 and widths == sorted(widths), (
        f"the fill did not move forward: {fills}")
    assert "17 times added" in summary, summary
    assert line_after == 0, "the line must give way to the outcome once the import lands"
