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
  * Round 10: "maybe actually all the text needs to be left aligned" -- round
    9's centred columns left ragged edges where one column holds different
    kinds of text; every entry now starts on its column's edge, the Time
    Save under GOLD!! included. "when "undo pb" is visible, the size of the
    play button to its left ... is smaller" -- the buttons measured
    identical; a disabled Save faded its whole frame, so it now keeps the
    frame and dims only its words. "that thick yellow line to the left of
    #1" -- the PB row has no edge ribbon.
  * Round 11: "the "GOLD!!" text should be in line with the rest of the row
    ... move the (-8.77 (263 frames)) to the right of GOLD!!" -- the word sits
    on the time's mid-line and the margin beside it, clear of the strategy
    picker. "make sure that the visual balance of each row is maintained no
    matter the circumstances" -- every row draws ▷ | PB | × as three frames:
    a run on another strategy and every row while no strategy is picked wear
    a disabled Save saying why.

Both log layouts are measured: the table (a wide pane) and the per-row grid
(a pane of 700px or less -- his window at his zoom; 800 here).
"""
import json
import os
import sys
import urllib.request
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
      // Round 10: where each column's text starts -- the time (or reset),
      // the gap or GOLD!!, and the Time Save under GOLD!!.
      resultText: (() => { const r = document.createRange();
        r.selectNodeContents(tr.querySelector('.attempt-result')); return left(r); })(),
      deltaText: (() => { const s = tr.querySelector(tr.classList.contains('is-pb') ? '.pb-gold' : '.pb-swap-num');
        return s && s.textContent.trim() ? left(s) : null; })(),
      saveText: left(tr.querySelector('.pb-save.is-shown')),
      // Round 11: GOLD!! on the time's mid-line, the margin beside it.
      goldBox: (() => { const g = tr.classList.contains('is-pb') && tr.querySelector('.pb-gold');
        if (!g) return null; const r = g.getBoundingClientRect(), t = tr.querySelector('.attempt-result b');
        const tb = t.getBoundingClientRect();
        return { right: r.right, mid: r.top + r.height / 2, timeMid: tb.top + tb.height / 2 }; })(),
      saveBox: (() => { const e = tr.querySelector('.pb-save.is-shown'); if (!e) return null;
        const r = e.getBoundingClientRect(), sel = tr.querySelector('.attempt-strategy select');
        return { left: r.left, right: r.right, mid: r.top + r.height / 2,
                 next: Math.min(sel ? sel.getBoundingClientRect().left : Infinity,
                                tr.querySelector('.attempt-actions').getBoundingClientRect().left) }; })(),
      slotButton: !!tr.querySelector('.attempt-pb-slot > button'),
      buttonBoxes: (() => { const slots = tr.querySelector('.attempt-action-slots');
        return [slots.firstElementChild, slots.querySelector('.attempt-pb-slot button'), slots.lastElementChild]
          .map(e => { const r = e.getBoundingClientRect();
            return {width:r.width, height:r.height, mid:r.top+r.height/2}; }); })(),
      reason: (tr.querySelector('.attempt-pb-slot button.pb-unavailable') || {}).title || null,
      pb: tr.classList.contains('is-pb'),
      edge: getComputedStyle(tr.querySelector('td')).boxShadow,
      // A disabled Save draws the same frame as ▷ and dims only its words.
      frame: (() => { const b = tr.querySelector('.attempt-pb-slot button.pb-unavailable');
        if (!b) return null; const s = getComputedStyle(b), p = getComputedStyle(tr.querySelector('.attempt-actions button.icon-button'));
        return [s.opacity, s.borderTopColor === p.borderTopColor, getComputedStyle(b.firstElementChild).opacity]; })(),
      strategy: left(tr.querySelector('.attempt-strategy')),
      replay: left(tr.querySelector('.attempt-actions button.icon-button')),
      slot: left(tr.querySelector('.attempt-pb-slot')),
      clear: left([...tr.querySelectorAll('.attempt-actions button')].pop()),
      unavailable: !!tr.querySelector('.attempt-pb-slot button.pb-unavailable[disabled]'),
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


def _render(play, base, width, save_newest=False, zoom=1, clear_one=False):
    browser = play.chromium.launch(headless=True, args=["--disable-gpu"])
    try:
        page = browser.new_page(viewport={"width": width, "height": 1100})
        page.add_init_script("localStorage.setItem('scope', 'lifetime')")
        page.route("**/api/replay/status", lambda route: route.fulfill(
            status=200, content_type="application/json", body=json.dumps(STATUS)))
        page.goto(base)
        page.evaluate("zoom => document.documentElement.style.zoom=zoom", zoom)
        page.locator(ROWS).nth(2).wait_for(timeout=20000)
        page.locator(".sidebar-status .recording-status .dot").wait_for(timeout=20000)
        if save_newest:
            # His case: the newest run saved as the PB, GOLD!! and Undo PB on
            # its row, the Time Save under the word once it has popped.
            page.locator(ROWS).nth(0).locator(".attempt-pb-slot button").click()
            page.locator(f"{ROWS}.is-pb .pb-save.is-shown").wait_for(timeout=20000)
            page.wait_for_timeout(1800)
        if clear_one:
            page.locator(f"{ROWS}:not(.is-pb) button[title='Clear this attempt as a mistake']").first.click()
            page.locator(f"{CARD} .attempt-footer-tools button").click()
            page.locator(f"{ROWS}.cleared button[aria-label='Restore attempt']").wait_for()
        page.wait_for_timeout(600)
        if os.environ.get("SM64_ACTION_SIZE_EVIDENCE"):
            output = Path(os.environ["SM64_ACTION_SIZE_EVIDENCE"])
            output.mkdir(parents=True, exist_ok=True)
            tag = "pb" if page.locator(f"{ROWS}.is-pb").count() else "unpicked"
            page.locator(f"{CARD} .attempt-action-slots").first.screenshot(
                path=str(output / f"{tag}-actions-{width}-{zoom}.png"))
        return page.evaluate(MEASURE), page.evaluate(RAIL)
    finally:
        browser.close()


def _one(values, what, seen):
    assert len(set(values)) == 1, f"{what} moves between rows: {sorted(set(values))} {seen}"


def _matching_action_frames(seen):
    frames = [r["buttonBoxes"] for r in seen["rows"]]
    assert frames and all(len(row) == 3 for row in frames), seen
    for replay, pb, clear in frames:
        assert min(replay["width"], replay["height"], pb["height"], clear["height"]) > 0, frames
        assert abs(replay["width"] - replay["height"]) < .2, frames
        assert abs(clear["width"] - clear["height"]) < .2, frames
        assert max(b["height"] for b in (replay,pb,clear)) - min(b["height"] for b in (replay,pb,clear)) < .2, frames
        assert max(b["mid"] for b in (replay,pb,clear)) - min(b["mid"] for b in (replay,pb,clear)) < .2, frames
    assert max(row[0]["width"] for row in frames) - min(row[0]["width"] for row in frames) < .2, frames


def _reset_then_finish(service, anchor):
    """A reset on the fixture's star (its practised strategy), then a finish,
    so the card holds both kinds of row and nothing is left running."""
    for event in (ev("practice_reset", anchor, {"igt_frames_before": 0}),
                  ev("practice_reset", anchor + 300, {"igt_frames_before": 300}),
                  ev("star_collected", anchor + 660, {"course_id": FIXTURE_COURSE, "star_id": FIXTURE_STAR,
                                                      "igt_frames": 200, "igt_timed_at": "xcam"})):
        _run_coro(service.publish(event))


def _no_strategy(base):
    request = urllib.request.Request(f"{base}/api/strat", method="POST",
                                     headers={"Content-Type": "application/json"},
                                     data=json.dumps({"course_id": FIXTURE_COURSE, "star_id": FIXTURE_STAR,
                                                      "strat_tag": None}).encode())
    urllib.request.urlopen(request, timeout=15).read()


def test_the_log_lines_up_in_both_layouts_and_the_rail_dot_is_centred():
    with serve_ui_live() as (base, service):
        _reset_then_finish(service, 30000)
        with sync_playwright() as play:
            narrow, rail = _render(play, base, 800, save_newest=True)
            wide, _ = _render(play, base, 1500)
            _no_strategy(base)
            unpicked, _ = _render(play, base, 800)
    assert not wide["narrow"] and narrow["narrow"], (wide["narrow"], narrow["narrow"])
    for seen in (wide, narrow):
        _matching_action_frames(seen)
        rows = seen["rows"]
        assert any(r["success"] for r in rows) and any(not r["success"] for r in rows), rows
        for what in ("result", "delta", "strategy", "replay", "slot", "clear"):
            _one([r[what] for r in rows], what, rows)
        # One left edge per text column (round 10): a reset and a time start
        # together, a gap and GOLD!! start together, and the Time Save starts
        # where GOLD!! does.
        _one([r["resultText"] for r in rows], "the time column's text", rows)
        _one([r["deltaText"] for r in rows if r["deltaText"] is not None], "the gap column's text", rows)
        # The newest run is the PB on both pages (saved on the first), and
        # its row has no gold edge.
        pb_rows = [r for r in rows if r["pb"]]
        assert len(pb_rows) == 1 and pb_rows[0]["edge"] == "none", pb_rows
        # A disabled Save keeps its frame: full opacity, ▷'s border colour,
        # only its icon and words dimmed.
        frames = [r["frame"] for r in rows if r["frame"]]
        assert frames and all(f[0] == "1" and f[1] and float(f[2]) < 0.5 for f in frames), frames
        # The × is a drawn mark sitting on its button's centre.
        assert all(r["xOff"] and max(map(abs, r["xOff"])) < 0.75 for r in rows), [r["xOff"] for r in rows]
        # Every row has a mark, and the ✔ is no longer beside the time.
        assert all(r["mark"] for r in rows), rows
        assert not any(r["words"].startswith("✔") for r in rows), rows
        # Every row draws its PB slot's button (round 11): a failure and a
        # run on another strategy wear a disabled Save; a finish on the
        # practised strategy never does.
        assert all(r["slotButton"] for r in rows), rows
        assert all(r["unavailable"] for r in rows if not r["success"] or r["other"]), rows
        assert not any(r["unavailable"] for r in rows if r["success"] and not r["other"]), rows
        assert any(r["other"] for r in rows), "the fixture shows no other-strategy row"
        # GOLD!! sits on the time's mid-line (round 11: "in line with the rest of the row").
        golds = [r["goldBox"] for r in rows if r["goldBox"]]
        assert golds and all(abs(g["mid"] - g["timeMid"]) < 1.5 for g in golds), golds
        if seen["pb"] is not None:
            assert abs(seen["pb"] - seen["goal"]) < 0.5, (seen["pb"], seen["goal"])
    # The Time Save sits to the right of GOLD!!, on its line, and ends before
    # the strategy picker or the buttons -- in his narrow layout.
    saved = [(r["goldBox"], r["saveBox"]) for r in narrow["rows"] if r["saveBox"]]
    assert len(saved) == 1, narrow["rows"]
    gold, box = saved[0]
    assert gold["right"] < box["left"] < gold["right"] + 12, saved
    assert abs(box["mid"] - gold["mid"]) < 1.5 and box["right"] <= box["next"], saved
    # With no strategy picked every row still draws three frames, its Save
    # disabled and saying how to bring it back.
    assert all(r["slotButton"] and r["unavailable"] for r in unpicked["rows"]), unpicked["rows"]
    _matching_action_frames(unpicked)
    assert all(r["reason"].startswith(("Pick", "Only a finished")) for r in unpicked["rows"]), (
        [r["reason"] for r in unpicked["rows"]])
    for what in ("replay", "slot", "clear"):
        _one([r[what] for r in unpicked["rows"]], what, unpicked["rows"])
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


def test_action_frames_keep_their_size_at_zoom_and_when_a_row_is_removed():
    with serve_ui_live() as (base, service):
        _reset_then_finish(service, 30000)
        with sync_playwright() as play:
            first, _ = _render(play, base, 850, save_newest=True, clear_one=True)
            _matching_action_frames(first)
            for width, zoom in ((850, 1.25), (850, 1.5), (1500, 1.25), (1500, 1.5)):
                seen, _ = _render(play, base, width, zoom=zoom)
                _matching_action_frames(seen)
                assert any(row["pb"] for row in seen["rows"]), seen
                assert any(row["unavailable"] for row in seen["rows"]), seen
