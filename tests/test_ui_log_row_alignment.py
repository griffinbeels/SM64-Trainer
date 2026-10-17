"""Things in the practice log that must line up, measured on the real page.

His live report, 2026-10-17 (Lineup round 7):
  * "the PB / goal values (16"10 and 16"06) clearly don't align" -- the narrow
    plate gave the PB line a .3rem gap and the GOAL line kept its trigger
    button's .4rem, so the GOAL time sat 1.6px right of the PB time.
  * "depending on the elements added to the row, the buttons are staggered /
    in totally different positions every time" -- the narrow log sized every
    row's grid by its own content, and the action cell right-aligned whatever
    it held, so ▷ moved whenever Save PB was absent.
  * "the checkmark ... is hidden underneath the rank cap ... the rank cap
    serves as the checkmark now" -- the ✔ left the time; every row keeps a
    mark in the mark column instead.
  * "the recording button isn't even centered!" -- in the sidebar rail; then,
    round 8, "there shouldn't be 2 dots" -- the rail says words instead.
  * Round 8: the PB / GOAL pair "should be CENTERED INSIDE THE BOUNDING BOX
    ... while still being aligned with each other".
  * Round 8: "the X button seems ... off center" -- the × is an icon now.

Both log layouts are measured: the table (a wide pane) and the per-row grid
(a pane of 700px or less -- his window at his zoom; 800 here).
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from playwright.sync_api import sync_playwright  # noqa: E402
from ui_fixture import FIXTURE_COURSE, FIXTURE_STAR, _run_coro, serve_ui_live  # noqa: E402
from test_tracker_service import ev  # noqa: E402

CARD = ".log-card.log-card-active"
ROWS = f"{CARD} .attempt-table tr:not(.replay-row):not(.pb-replay-notice)"
MEASURE = """(() => {
  const left = el => el ? Math.round(el.getBoundingClientRect().left * 10) / 10 : null;
  const rows = [...document.querySelectorAll(%s)];
  const plate = document.querySelector('%s .goal-plate');
  return {
    narrow: getComputedStyle(rows[0]).display === 'grid',
    rows: rows.map(tr => ({
      success: tr.querySelector('.attempt-result').classList.contains('good'),
      other: tr.classList.contains('other-strat'),
      result: left(tr.querySelector('.attempt-result')),
      words: tr.querySelector('.attempt-result').textContent.trim(),
      mark: !!tr.querySelector('.attempt-medal .hat, .attempt-medal svg, .attempt-medal img, .attempt-glyph'),
      delta: left(tr.querySelector('.attempt-delta')),
      // Round 9: the time and the gap/GOLD!! are centred in their columns.
      resultOff: (() => { const c = tr.querySelector('.attempt-result'); const r = document.createRange();
        r.selectNodeContents(c); const a = r.getBoundingClientRect(), b = c.getBoundingClientRect();
        return (a.left + a.width / 2) - (b.left + b.width / 2); })(),
      deltaOff: (() => { const c = tr.querySelector('.attempt-delta'), s = c.querySelector('.pb-swap');
        if (!s) return 0; const a = s.getBoundingClientRect(), b = c.getBoundingClientRect();
        return (a.left + a.width / 2) - (b.left + b.width / 2); })(),
      strategy: left(tr.querySelector('.attempt-strategy')),
      replay: left(tr.querySelector('.attempt-actions button.icon-button')),
      slot: left(tr.querySelector('.attempt-pb-slot')),
      clear: left([...tr.querySelectorAll('.attempt-actions button')].pop()),
      unfinished: !!tr.querySelector('.attempt-pb-slot button.pb-unfinished[disabled]'),
      // The ×'s mark against its button's centre, both axes.
      xOff: (() => { const b = [...tr.querySelectorAll('.attempt-actions button')].pop();
        const i = b.querySelector('svg'); if (!i) return null;
        const br = b.getBoundingClientRect(), ir = i.getBoundingClientRect();
        return [ir.left + ir.width / 2 - (br.left + br.width / 2), ir.top + ir.height / 2 - (br.top + br.height / 2)]; })(),
    })),
    pb: left(plate && (plate.querySelector('.goal-plate-pb .pblink') || plate.querySelector('.goal-plate-pb .pbtag'))),
    goal: left(plate && plate.querySelector('.goal-plate-time')),
    // The PB/GOAL pair inside its box: the words' left edge and the times'
    // right edge (the goal's ✓ hangs outside the time's box), against the box.
    plateGaps: plate && (() => {
      const box = plate.getBoundingClientRect();
      const words = [...plate.querySelectorAll('.goal-plate-word')].map(e => e.getBoundingClientRect());
      const times = [plate.querySelector('.goal-plate-pb .pbtag'), plate.querySelector('.goal-plate-time')]
        .map(e => e.getBoundingClientRect());
      return [Math.min(...words.map(r => r.left)) - box.left, box.right - Math.max(...times.map(r => r.right))];
    })(),
  };
})()""" % (json.dumps(ROWS), CARD)
RAIL = """(() => {
  const box = document.querySelector('.sidebar-status');
  const centre = el => { const r = el.getBoundingClientRect(); return r.left + r.width / 2; };
  const shown = el => el && getComputedStyle(el).display !== 'none' && el.getBoundingClientRect().width > 0;
  const word = box.querySelector('.status-short'), rec = box.querySelector('.recording-short');
  const dot = box.querySelector('.recording-status .dot');
  const b = box.getBoundingClientRect(), r = rec.getBoundingClientRect();
  return { box: centre(box), word: shown(word) && word.textContent,
           rec: shown(rec) && rec.textContent, recCentre: centre(rec),
           recOn: rec.classList.contains('is-on'), recColour: getComputedStyle(rec).color,
           light: shown(box.querySelector('.status-light')),
           dotMark: getComputedStyle(dot, '::before').content,
           // the box hugs the word: padding above and below it, nothing else
           above: r.top - b.top, below: b.bottom - r.bottom };
})()"""
STATUS = {"recording": False, "capture_state": "off", "capture_gated": False,
          "idle": False, "disk_bytes": 0, "max_buffer_bytes": 0}


def _render(play, base, width):
    browser = play.chromium.launch(headless=True, args=["--disable-gpu"])
    try:
        page = browser.new_page(viewport={"width": width, "height": 1100})
        page.add_init_script("localStorage.setItem('scope', 'lifetime')")
        page.route("**/api/replay/status", lambda route: route.fulfill(
            status=200, content_type="application/json", body=json.dumps(STATUS)))
        page.goto(base)
        page.locator(ROWS).nth(2).wait_for(timeout=20000)
        page.locator(".sidebar-status .recording-status .dot").wait_for(timeout=20000)
        page.wait_for_timeout(600)
        return page.evaluate(MEASURE), page.evaluate(RAIL)
    finally:
        browser.close()


def _one(values, what, seen):
    assert len(set(values)) == 1, f"{what} moves between rows: {sorted(set(values))} {seen}"


def _reset_then_finish(service, anchor):
    """A reset on the fixture's star (its practised strategy), then a finish,
    so the card holds both kinds of row and nothing is left running."""
    for event in (ev("practice_reset", anchor, {"igt_frames_before": 0}),
                  ev("practice_reset", anchor + 300, {"igt_frames_before": 300}),
                  ev("star_collected", anchor + 660, {"course_id": FIXTURE_COURSE, "star_id": FIXTURE_STAR,
                                                      "igt_frames": 360, "igt_timed_at": "xcam"})):
        _run_coro(service.publish(event))


def test_the_log_lines_up_in_both_layouts_and_the_rail_dot_is_centred():
    with serve_ui_live() as (base, service):
        _reset_then_finish(service, 30000)
        with sync_playwright() as play:
            wide, _ = _render(play, base, 1500)
            narrow, rail = _render(play, base, 800)
    assert not wide["narrow"] and narrow["narrow"], (wide["narrow"], narrow["narrow"])
    for seen in (wide, narrow):
        rows = seen["rows"]
        assert any(r["success"] for r in rows) and any(not r["success"] for r in rows), rows
        for what in ("result", "delta", "strategy", "replay", "slot", "clear"):
            _one([r[what] for r in rows], what, rows)
        # The time and the gap/GOLD!! sit centred in their columns (round 9:
        # "I'd like to test where the text is all CENTERED").
        assert all(abs(r["resultOff"]) < 1 and abs(r["deltaOff"]) < 1 for r in rows), (
            [(r["words"], round(r["resultOff"], 1), round(r["deltaOff"], 1)) for r in rows])
        # The × is a drawn mark sitting on its button's centre.
        assert all(r["xOff"] and max(map(abs, r["xOff"])) < 0.75 for r in rows), [r["xOff"] for r in rows]
        # Every row has a mark, and the ✔ is no longer beside the time.
        assert all(r["mark"] for r in rows), rows
        assert not any(r["words"].startswith("✔") for r in rows), rows
        # A failure on the practised strategy wears a disabled Save; a finish never does.
        assert any(r["unfinished"] for r in rows if not r["success"] and not r["other"]), rows
        assert not any(r["unfinished"] for r in rows if r["success"] or r["other"]), rows
        if seen["pb"] is not None:
            assert abs(seen["pb"] - seen["goal"]) < 0.5, (seen["pb"], seen["goal"])
    # The narrow plate (his layout) centres the PB/GOAL pair in its box.
    left_gap, right_gap = narrow["plateGaps"]
    assert abs(left_gap - right_gap) < 1, narrow["plateGaps"]
    # The narrow sidebar holds one word, REC, grey while not recording ("there
    # shouldn't be 2 dots", then "Remove the "Live" and "Paused" text", round
    # 8): no connection word while connected, no dot, centred, and the box
    # sized to it (equal room above and below).
    assert rail["word"] is False and rail["rec"] == "REC" and not rail["recOn"], rail
    assert rail["recColour"] == "rgb(143, 160, 181)" and not rail["light"], rail   # --muted, not --coral
    assert rail["dotMark"] in ("none", "normal"), rail
    assert abs(rail["recCentre"] - rail["box"]) < 0.75, rail
    assert abs(rail["above"] - rail["below"]) < 1.5, rail
