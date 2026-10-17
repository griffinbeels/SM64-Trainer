""""Set Timer -> GrabX" draws LEFT of the entry number, only on hinted rows,
without moving `#N`, and leaves every row once he grabs a star under GrabX
(2026-10-17). Rendered through the real app at the supported floor and at a
wide width. Set SM64_SHOT_DIR to keep the screenshots.

The server decides which rows (tests/test_timer_hint.py); this pins what the
practice log draws for that answer.
"""
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))

from find_uilab import find_uilab  # noqa: E402

_MISSING = find_uilab()
if _MISSING:
    pytest.skip(_MISSING, allow_module_level=True)

from sm64_events.core.events import Event  # noqa: E402
from ui_fixture import (FIXTURE_COURSE, FIXTURE_STAR, _run_coro,  # noqa: E402
                        serve_ui_live)
from uilab.driver import get_driver  # noqa: E402

NOW = datetime(2026, 10, 17, 12, 0, 0, tzinfo=timezone.utc)
WIDTHS = (850, 1500)
HINT_TEXT = "Set Timer → GrabX"
HINT_TITLE = ("The game's timer doesn't stop at the x-cam. This log shows the x-cam time."
              " Set Usamune's TIMER → STOP to GrabX to match.")


def _publish(service, type_, frame, payload):
    _run_coro(service.publish(Event(type=type_, frame=frame,
                                    timestamp_utc=NOW, payload=payload)))


def _run(service, frame, igt, stop):
    _publish(service, "practice_reset", frame,
             {"igt_frames_before": 300, "mario_acted": True, "area": 1,
              "prev_area": 1, "area_load": False, "teleport": False,
              "loads_journaled": True})
    grab = {"course_id": FIXTURE_COURSE, "star_id": FIXTURE_STAR,
            "igt_frames": igt, "igt_timed_at": "xcam"}
    if stop is not None:
        grab["usamune_stop"] = stop
    _publish(service, "star_collected", frame + 300, grab)


# Every row of the fixture star's card: its entry number's text box (the "#"
# and the digits are separate text nodes), the hint's box and words, and the
# card body the hint must stay inside.
ROWS = """(() => {
  const card = Array.from(document.querySelectorAll('.log-card'))
    .find((c) => c.querySelector('.attempt-table .attempt-index'));
  const body = card.querySelector('.log-card-body').getBoundingClientRect();
  return Array.from(card.querySelectorAll('.attempt-table tr'))
    .filter((tr) => tr.querySelector('.attempt-index'))
    .map((tr) => {
      const idx = tr.querySelector('.attempt-index');
      const words = Array.from(idx.childNodes)
        .filter((n) => n.nodeType === 3 && n.textContent.trim());
      const range = document.createRange();
      range.setStartBefore(words[0]); range.setEndAfter(words[words.length - 1]);
      const num = range.getBoundingClientRect();
      const hint = idx.querySelector('.timer-hint');
      const box = hint && hint.getBoundingClientRect();
      const row = tr.getBoundingClientRect();
      return {
        number: words.map((n) => n.textContent).join(''),
        numLeft: num.left, numTop: num.top, numBottom: num.bottom,
        bodyLeft: body.left,
        hint: hint ? hint.textContent.replace(/\\s+/g, ' ').trim() : null,
        title: hint ? hint.getAttribute('title') : null,
        hintLeft: box && box.left, hintRight: box && box.right,
        hintTop: box && box.top, hintBottom: box && box.bottom,
        rowTop: row.top, rowBottom: row.bottom,
      };
    });
})()"""


def _rows(page):
    return page.evaluate(ROWS)


def _settle(page, want_hints, tries=40):
    for _ in range(tries):
        if page.count(".attempt-table .timer-hint") == want_hints:
            return
        page.wait_ms(100)
    raise AssertionError(f"{page.count('.attempt-table .timer-hint')} hints, wanted {want_hints}")


def test_the_hint_sits_left_of_the_entry_number_on_hinted_rows_only():
    with serve_ui_live() as (base, service):
        # Oldest first: a grab from before the key existed, then two whose
        # Usamune setting stops short of the x-cam. The newest says "grab",
        # so the two stamped rows are hinted and the legacy one is not.
        _run(service, 20000, 905, None)
        _run(service, 21000, 880, "none")
        _run(service, 22000, 862, "grab")
        for width in WIDTHS:
            with get_driver().launch(viewport=(width, 900)) as page:
                page.goto(base + "/ui/index.html")
                page.wait_for(".attempt-table .timer-hint")
                page.wait_ms(400)
                rows = _rows(page)
                hinted = [r for r in rows if r["hint"]]
                plain = [r for r in rows if not r["hint"]]
                assert len(hinted) == 2 and len(plain) >= 2, rows
                # Newest first: the two hinted rows are the two stamped grabs.
                assert [r["hint"] for r in rows[:3]] == [HINT_TEXT, HINT_TEXT, None], rows
                for r in hinted:
                    assert r["title"] == HINT_TITLE, r
                    assert r["number"].startswith("#"), r
                    # LEFT of the entry number, inside the card, on its row.
                    assert r["hintRight"] <= r["numLeft"], r
                    assert r["hintLeft"] >= r["bodyLeft"], r
                    assert r["rowTop"] <= r["hintTop"] and r["hintBottom"] <= r["rowBottom"], r
                # `#N` does not move for the hint: one x on every row.
                lefts = {round(r["numLeft"], 1) for r in rows}
                assert len(lefts) == 1, rows
                assert not page.problems(), page.problems()
                shots = os.environ.get("SM64_SHOT_DIR")
                if shots:
                    box = page.evaluate("""(() => {
                      const card = document.querySelector('.attempt-table .timer-hint').closest('.log-card');
                      card.scrollIntoView({block: 'start'});
                      const r = card.getBoundingClientRect();
                      return {x: r.left, y: Math.max(0, r.top), width: r.width,
                              height: Math.min(r.height, window.innerHeight - Math.max(0, r.top))};
                    })()""")
                    page.wait_ms(300)
                    Path(shots, f"timer_hint_{width}.png").write_bytes(page.screenshot(clip=box))

        # He sets GrabX and grabs one star: every hint leaves, older rows too,
        # on the page he already has open.
        with get_driver().launch(viewport=(WIDTHS[0], 900)) as page:
            page.goto(base + "/ui/index.html")
            page.wait_for(".attempt-table .timer-hint")
            _settle(page, 2)
            _run(service, 23000, 850, "xcam")
            _settle(page, 0)
            assert len(_rows(page)) == len(rows) + 1
            assert not page.problems(), page.problems()
