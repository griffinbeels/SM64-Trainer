"""The timer hint's warning triangle draws LEFT of the entry number, only on
hinted rows, without moving `#N`; hovering or focusing it shows why the game's
timer differs; it leaves every row once he grabs a star under GrabX
(2026-10-17). Rendered through the real app at the supported floor and two
wider widths. Set SM64_SHOT_DIR to keep the screenshots.

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
WIDTHS = (850, 1200, 1500)
TIP = ("Your game showed a different time: Usamune's TIMER → STOP isn't GrabX."
       " This entry uses the xcam time and is still valid.")


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
# and the digits are separate text nodes), the symbol's box and attributes,
# and the card body the symbol must stay inside.
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
      const icon = hint && hint.querySelector('svg.ui-icon');
      const box = icon && icon.getBoundingClientRect();
      const row = tr.getBoundingClientRect();
      return {
        number: words.map((n) => n.textContent).join(''),
        numLeft: num.left, numTop: num.top, numBottom: num.bottom,
        bodyLeft: body.left,
        hint: !!hint,
        icon: !!icon,
        words: hint ? hint.textContent.trim() : null,
        caveatClass: !!(hint && hint.closest('.caveat-chip, .caveat-badge')),
        tip: hint && hint.getAttribute('data-tip'),
        label: hint && hint.getAttribute('aria-label'),
        tabindex: hint && hint.getAttribute('tabindex'),
        hintLeft: box && box.left, hintRight: box && box.right,
        hintTop: box && box.top, hintBottom: box && box.bottom,
        hintWidth: box && box.width,
        rowTop: row.top, rowBottom: row.bottom,
      };
    });
})()"""

# The tooltip is the symbol's ::after. Its box is not in the DOM, so its
# extent comes from the symbol's left edge, its offset and shift, and its used
# width.
TOOLTIP = """(() => {
  const el = document.querySelector('[data-probe="hint"]');
  const tip = getComputedStyle(el, '::after');
  const card = el.closest('.log-card').getBoundingClientRect();
  const shift = tip.transform === 'none' ? 0 : new DOMMatrix(tip.transform).e;
  const left = el.getBoundingClientRect().left + parseFloat(tip.left) + shift;
  return {
    content: tip.content, opacity: parseFloat(tip.opacity),
    visibility: tip.visibility, focusVisible: el.matches(':focus-visible'),
    left, right: left + parseFloat(tip.width),
    cardLeft: card.left, cardRight: card.right,
  };
})()"""

MARK_FIRST = """(() => {
  const el = document.querySelector('.attempt-table .timer-hint');
  el.setAttribute('data-probe', 'hint');
  el.closest('.log-card').scrollIntoView({block: 'start'});
  return true;
})()"""


def _rows(page):
    return page.evaluate(ROWS)


def _settle(page, want_hints, tries=40):
    for _ in range(tries):
        if page.count(".attempt-table .timer-hint") == want_hints:
            return
        page.wait_ms(100)
    raise AssertionError(f"{page.count('.attempt-table .timer-hint')} hints, wanted {want_hints}")


def _assert_tip_shown(tip):
    assert tip["content"] == f'"{TIP}"', tip
    assert tip["visibility"] == "visible" and tip["opacity"] == 1, tip
    # It opens over the row, inside the card, rather than hanging off its edge.
    assert tip["cardLeft"] <= tip["left"] and tip["right"] <= tip["cardRight"], tip


def _shoot(page, shots, name):
    box = page.evaluate("""(() => {
      const r = document.querySelector('[data-probe="hint"]').closest('.log-card').getBoundingClientRect();
      return {x: r.left, y: Math.max(0, r.top), width: r.width,
              height: Math.min(r.height, window.innerHeight - Math.max(0, r.top))};
    })()""")
    Path(shots, name).write_bytes(page.screenshot(clip=box))


def _assert_rows(rows):
    hinted = [r for r in rows if r["hint"]]
    plain = [r for r in rows if not r["hint"]]
    assert len(hinted) == 2 and len(plain) >= 2, rows
    # Newest first: the two hinted rows are the two stamped grabs.
    assert [r["hint"] for r in rows[:3]] == [True, True, False], rows
    for r in hinted:
        # A symbol, not words, and not the caveat mark.
        assert r["icon"] and r["words"] == "" and not r["caveatClass"], r
        assert 12 <= r["hintWidth"] <= 18, r
        # The explanation rides the symbol, which keyboard can reach.
        assert r["tip"] == TIP and r["label"] == TIP, r
        assert r["tabindex"] == "0", r
        assert r["number"].startswith("#"), r
        # LEFT of the entry number, inside the card, on its row.
        assert r["hintRight"] <= r["numLeft"], r
        assert r["hintLeft"] >= r["bodyLeft"], r
        assert r["rowTop"] <= r["hintTop"] and r["hintBottom"] <= r["rowBottom"], r
    # `#N` does not move for the symbol: one x on every row.
    lefts = {round(r["numLeft"], 1) for r in rows}
    assert len(lefts) == 1, rows


def _assert_keyboard_reads_a_dimmed_row(page, base):
    """Focusing the symbol shows the same words, no pointer. On a dimmed row
    (another strategy) the row lights up while it is read, or the tooltip
    would draw at the row's dim."""
    probe = "document.querySelector('[data-probe=\"hint\"]')"
    row_opacity = f"parseFloat(getComputedStyle({probe}.closest('tr')).opacity)"
    page.goto(base + "/ui/index.html")
    page.wait_for(".attempt-table .timer-hint")
    page.evaluate(MARK_FIRST)
    page.evaluate(f"{probe}.closest('tr').classList.add('other-strat')")
    page.wait_ms(600)
    assert page.evaluate(row_opacity) < 0.9
    page.evaluate(f"{probe}.focus()")
    page.wait_ms(600)
    tip = page.evaluate(TOOLTIP)
    assert tip["focusVisible"], tip
    _assert_tip_shown(tip)
    assert page.evaluate(row_opacity) == 1


def test_the_symbol_sits_left_of_the_entry_number_on_hinted_rows_only():
    with serve_ui_live() as (base, service):
        # Oldest first: a grab from before the key existed, then two whose
        # Usamune setting stops short of the x-cam. The newest says "grab",
        # so the two stamped rows are hinted and the legacy one is not.
        _run(service, 20000, 905, None)
        _run(service, 21000, 880, "none")
        _run(service, 22000, 862, "grab")
        shots = os.environ.get("SM64_SHOT_DIR")
        for width in WIDTHS:
            with get_driver().launch(viewport=(width, 900)) as page:
                page.goto(base + "/ui/index.html")
                page.wait_for(".attempt-table .timer-hint")
                page.wait_ms(400)
                rows = _rows(page)
                _assert_rows(rows)
                page.evaluate(MARK_FIRST)
                page.wait_ms(300)
                quiet = page.evaluate(TOOLTIP)
                assert quiet["visibility"] == "hidden" and quiet["opacity"] == 0, quiet
                if shots:
                    _shoot(page, shots, f"hintsymbol_{width}.png")
                page.hover('[data-probe="hint"]')
                page.wait_ms(300)
                _assert_tip_shown(page.evaluate(TOOLTIP))
                if shots:
                    _shoot(page, shots, f"hintsymbol_{width}_hover.png")
                assert not page.problems(), page.problems()

        with get_driver().launch(viewport=(WIDTHS[0], 900)) as page:
            _assert_keyboard_reads_a_dimmed_row(page, base)

        # He sets GrabX and grabs one star: every symbol leaves, older rows
        # too, on the page he already has open.
        with get_driver().launch(viewport=(WIDTHS[0], 900)) as page:
            page.goto(base + "/ui/index.html")
            page.wait_for(".attempt-table .timer-hint")
            _settle(page, 2)
            _run(service, 23000, 850, "xcam")
            _settle(page, 0)
            assert len(_rows(page)) == len(rows) + 1
            assert not page.problems(), page.problems()
